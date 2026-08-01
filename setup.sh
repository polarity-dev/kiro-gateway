#!/usr/bin/env bash
#
# Kiro Gateway - Interactive setup for Kiro IDE (Enterprise / IdC) users on macOS.
#
# Automates the discovery steps that are not documented upstream:
#   1. Locates the Kiro IDE credentials file
#   2. Probes which runtime.{region}.kiro.dev endpoint your subscription lives in
#   3. Extracts the CodeWhisperer profileArn from Kiro IDE logs
#   4. Writes .env and configures ~/.claude/settings.json for Claude Code
#
# Safe to re-run: prompts before overwriting anything.
# Pass -y/--yes for non-interactive mode (accepts all prompts; for AI agents).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$REPO_DIR/.env"
CREDS_FILE="$HOME/.aws/sso/cache/kiro-auth-token.json"
KIRO_LOGS="$HOME/Library/Application Support/Kiro/logs"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
PORT="8000"

info()  { printf '  %s\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
step()  { printf '\n\033[1m%s\033[0m\n' "$*"; }

# Non-interactive mode. When set (via -y/--yes), all prompts assume their
# default answer: overwrite .env (after backing it up) and configure Claude
# Code. This lets an AI agent run the installer unattended. The one thing it
# cannot auto-answer is a missing profileArn — that still requires the user to
# send a message in Kiro IDE first, so the script fails with guidance instead.
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help)
      printf 'Usage: %s [-y|--yes]\n\n  -y, --yes   Non-interactive: accept all prompts (for AI agents / CI)\n' "$0"
      exit 0 ;;
    *) fail "Unknown argument: $arg (try --help)" ;;
  esac
done

# ---------------------------------------------------------------------------
step "1/7  Checking prerequisites"

command -v python3 >/dev/null || fail "python3 not found. Install Python 3.10+ first."
ok "python3 $(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"

if ! python3 -c 'import httpx, fastapi' 2>/dev/null; then
  warn "Dependencies missing. Installing from requirements.txt..."
  python3 -m pip install -q -r "$REPO_DIR/requirements.txt" || fail "pip install failed."
fi
ok "Python dependencies present"

# ---------------------------------------------------------------------------
step "2/7  Locating Kiro credentials"

[ -f "$CREDS_FILE" ] || fail "$CREDS_FILE not found. Log in to Kiro IDE first."

ACCESS_TOKEN=$(python3 -c "import json;print(json.load(open('$CREDS_FILE'))['accessToken'])")
SSO_REGION=$(python3 -c "import json;print(json.load(open('$CREDS_FILE')).get('region',''))")
ok "Credentials found (SSO region: ${SSO_REGION:-unknown})"

# ---------------------------------------------------------------------------
step "3/7  Extracting profileArn from Kiro IDE logs"

# Kiro IDE logs every API call it makes, including the profileArn. The profile ID
# is an opaque string that cannot be derived from your AWS account settings, and
# the SSO OIDC refresh flow never returns it — reading the logs is the only way.
# Pick the most frequently seen ARN, as older logs may contain stale entries.
PROFILE_ARN=""
if [ -d "$KIRO_LOGS" ]; then
  PROFILE_ARN=$(grep -rhao 'arn:aws:codewhisperer:[a-z0-9-]*:[0-9]*:profile/[A-Za-z0-9_-]*' \
    "$KIRO_LOGS" 2>/dev/null | sort | uniq -c | sort -rn | head -1 | awk '{print $2}')
fi

if [ -z "$PROFILE_ARN" ]; then
  warn "Could not find profileArn in Kiro IDE logs."
  info "Send at least one message in Kiro IDE, then re-run this script."
  if [ "$ASSUME_YES" -eq 1 ]; then
    fail "profileArn is required. Send a message in Kiro IDE, then re-run."
  fi
  info "Or paste it manually (format: arn:aws:codewhisperer:REGION:ACCOUNT:profile/ID)"
  printf '  profileArn: '
  read -r PROFILE_ARN
  [ -n "$PROFILE_ARN" ] || fail "profileArn is required."
fi
ok "profileArn: $PROFILE_ARN"

# ---------------------------------------------------------------------------
step "4/7  Determining Q API region"

# The ARN's region field is authoritative: it names the region actually serving
# your subscription. Guessing by DNS is unreliable because several
# runtime.*.kiro.dev hosts resolve regardless of where your subscription lives.
API_REGION=$(printf '%s' "$PROFILE_ARN" | cut -d: -f4)

if [ -z "$API_REGION" ]; then
  fail "Malformed profileArn: cannot read region from '$PROFILE_ARN'."
fi

host "runtime.$API_REGION.kiro.dev" >/dev/null 2>&1 \
  || fail "runtime.$API_REGION.kiro.dev does not resolve. Check your network or VPN."

if [ "$API_REGION" != "$SSO_REGION" ]; then
  info "API region ($API_REGION) differs from SSO region (${SSO_REGION:-unknown}) — expected."
fi
ok "API region: $API_REGION"

# ---------------------------------------------------------------------------
step "5/7  Writing .env"

if [ -f "$ENV_FILE" ]; then
  if [ "$ASSUME_YES" -eq 1 ]; then
    reply="y"
  else
    printf '  .env already exists. Overwrite? [y/N] '
    read -r reply
  fi
  case "$reply" in
    [yY]) cp "$ENV_FILE" "$ENV_FILE.bak" && info "Backed up to .env.bak" ;;
    *)    fail "Aborted. Edit .env manually with the values shown above." ;;
  esac
fi

PROXY_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')

cat > "$ENV_FILE" <<EOF
# Kiro Gateway configuration — generated by setup.sh

# Password protecting THIS proxy. Used as the api_key by your clients.
PROXY_API_KEY="$PROXY_KEY"

# Kiro IDE credentials (tokens are refreshed automatically)
KIRO_CREDS_FILE="~/.aws/sso/cache/kiro-auth-token.json"

# Region hosting the Q API endpoint. Differs from your SSO region
# (${SSO_REGION:-unknown}) when the subscription lives elsewhere.
KIRO_API_REGION="$API_REGION"

# CodeWhisperer profile ARN. Not returned by the SSO OIDC refresh flow, so it
# must be set explicitly. Read from Kiro IDE logs by setup.sh.
PROFILE_ARN="$PROFILE_ARN"

# Debug logging: off | errors | all
DEBUG_MODE=off
EOF

ok "Wrote $ENV_FILE"

# ---------------------------------------------------------------------------
step "6/7  Configuring Claude Code"

if [ "$ASSUME_YES" -eq 1 ]; then
  reply="y"
else
  printf '  Point Claude Code at this gateway? [Y/n] '
  read -r reply
fi
case "$reply" in
  [nN])
    info "Skipped. To do it later, set these in $CLAUDE_SETTINGS:"
    info "  ANTHROPIC_BASE_URL=http://localhost:$PORT"
    info "  ANTHROPIC_AUTH_TOKEN=$PROXY_KEY"
    ;;
  *)
    mkdir -p "$(dirname "$CLAUDE_SETTINGS")"
    PROXY_KEY="$PROXY_KEY" PORT="$PORT" SETTINGS="$CLAUDE_SETTINGS" python3 - <<'PY'
import json, os

path = os.environ['SETTINGS']
try:
    with open(path) as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    settings = {}

settings.setdefault('env', {}).update({
    'ANTHROPIC_BASE_URL': f"http://localhost:{os.environ['PORT']}",
    # AUTH_TOKEN (not API_KEY) skips the interactive OAuth login entirely.
    'ANTHROPIC_AUTH_TOKEN': os.environ['PROXY_KEY'],
    'CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY': '1',
})

# Legacy setups pinned ANTHROPIC_MODEL="claude-opus-4.7", which (a) is now
# hardcoded by Claude Code to render as the retired "Claude Opus 4" (with a
# deprecation warning) and (b) overrides any /model choice on every restart
# since env-var precedence beats the persisted model setting. Drop it if
# present so the top-level "model" key below can actually stick.
settings.get('env', {}).pop('ANTHROPIC_MODEL', None)

# Default initial model = Kiro's server-side "auto" router. The alias key is
# prefixed with "claude-" because Claude Code's gateway model discovery
# hard-drops any /v1/models entry whose id doesn't start with claude/anthropic
# — the value forwarded to Kiro is still the bare "auto".
#
# Idempotency contract with the /update-kiro-models skill: the display key
# "claude-auto · 1x" is treated as stable (auto is always 1x by definition, so
# a model-list refresh will never rewrite this specific alias). If the skill
# ever renames it, it must run this same reset. We DO detect and heal two
# common broken states below rather than silently leave the user stuck:
DEFAULT_MODEL = 'claude-auto · 1x'
current = settings.get('model')
if current is None:
    settings['model'] = DEFAULT_MODEL
elif '·' not in current and (current.startswith('claude-') or current.startswith('anthropic')):
    # Bare model id (e.g. "claude-opus-4.7") — almost always a leftover from the
    # old ANTHROPIC_MODEL env-var pattern we dropped above. Reset to the router
    # so the user doesn't restart into the retired-Opus-4 label.
    settings['model'] = DEFAULT_MODEL
# Anything else (a real "claude-<name> · <rate>x · <ctx>" alias the user has
# actively chosen via /model) is left alone.

with open(path, 'w') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')
PY
    ok "Configured $CLAUDE_SETTINGS (existing settings preserved)"
    ;;
esac

# ---------------------------------------------------------------------------
step "7/7  Auto-approve kiro-credits skill"

CREDITS_CMD='Bash(python3 ~/.claude/skills/kiro-credits/check.py)'

if [ "$ASSUME_YES" -eq 1 ]; then
  reply="y"
else
  printf '  Allow the kiro-credits skill to run without permission prompts? [Y/n] '
  read -r reply
fi
case "$reply" in
  [nN])
    info "Skipped. You can add it later in $CLAUDE_SETTINGS under permissions.allow:"
    info "  \"$CREDITS_CMD\""
    ;;
  *)
    CREDITS_CMD="$CREDITS_CMD" SETTINGS="$CLAUDE_SETTINGS" python3 - <<'PY'
import json, os

path = os.environ['SETTINGS']
cmd = os.environ['CREDITS_CMD']

try:
    with open(path) as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    settings = {}

perms = settings.setdefault('permissions', {})
allow = perms.setdefault('allow', [])
if cmd not in allow:
    allow.append(cmd)

with open(path, 'w') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')
PY
    ok "Added kiro-credits to auto-approve in $CLAUDE_SETTINGS"
    ;;
esac

# ---------------------------------------------------------------------------
# Optional: SwiftBar menu bar widget (macOS only)
if [ "$(uname)" = "Darwin" ] && [ -f "$REPO_DIR/scripts/swiftbar/install.sh" ]; then
  printf '\n'
  step "Optional: Menu bar credit widget"
  info "Show live Kiro credits in your macOS menu bar (⚡️used/cap, refreshes every 60s)."
  info "Requires SwiftBar (free, open source)."
  if ask "Install the SwiftBar menu bar widget?"; then
    if [ "$ASSUME_YES" -eq 1 ]; then
      bash "$REPO_DIR/scripts/swiftbar/install.sh" -y
    else
      bash "$REPO_DIR/scripts/swiftbar/install.sh"
    fi
  else
    info "Skipped. You can install it later: scripts/swiftbar/install.sh"
  fi
fi

# ---------------------------------------------------------------------------
cat <<EOF

$(printf '\033[1mSetup complete.\033[0m')

  Start the gateway:   cd $REPO_DIR && python3 main.py
  Then run 'claude' in any terminal — no environment exports needed.

  Optional shell helper for ~/.zshrc:

    kiro-gateway() {
      (cd "$REPO_DIR" && python3 main.py)
    }

  Available models:     curl -s localhost:$PORT/v1/models \\
                          -H "Authorization: Bearer \$PROXY_API_KEY"
  Switch model:         /model inside Claude Code
EOF
