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
# Pass --port PORT to persist a custom gateway port, or --check-port to verify
# that the gateway, Claude Code, and the optional zsh helper stay aligned.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$REPO_DIR/.env"
CREDS_FILE="$HOME/.aws/sso/cache/kiro-auth-token.json"
KIRO_LOGS="$HOME/Library/Application Support/Kiro/logs"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
PORT=""
REQUESTED_PORT=""
PORT_REQUESTED=0
CHECK_PORT=0

info()  { printf '  %s\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
step()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ask() {
  if [ "$ASSUME_YES" -eq 1 ]; then
    return 0
  fi
  printf '  %s [Y/n] ' "$1"
  read -r reply
  case "$reply" in
    [nN]) return 1 ;;
    *)    return 0 ;;
  esac
}

# Non-interactive mode. When set (via -y/--yes), all prompts assume their
# default answer: overwrite .env (after backing it up) and configure Claude
# Code. This lets an AI agent run the installer unattended. The one thing it
# cannot auto-answer is a missing profileArn — that still requires the user to
# send a message in Kiro IDE first, so the script fails with guidance instead.
ASSUME_YES=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -y|--yes)
      ASSUME_YES=1
      shift
      ;;
    --port)
      [ "$#" -ge 2 ] || fail "--port requires a value (try --help)"
      REQUESTED_PORT="$2"
      PORT_REQUESTED=1
      shift 2
      ;;
    --port=*)
      REQUESTED_PORT="${1#*=}"
      PORT_REQUESTED=1
      shift
      ;;
    --check-port)
      CHECK_PORT=1
      shift
      ;;
    -h|--help)
      printf 'Usage: %s [-y|--yes] [--port PORT] [--check-port]\n\n' "$0"
      printf '  -y, --yes       Non-interactive: accept setup prompts\n'
      printf '  --port PORT     Persist a custom port (1-65535); safe on existing setups\n'
      printf '  --check-port    Verify .env, Claude Code, and the optional zsh helper\n'
      exit 0
      ;;
    *) fail "Unknown argument: $1 (try --help)" ;;
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

PORT_ARGS=(--env-file "$ENV_FILE" --settings "$CLAUDE_SETTINGS" --zshrc "$HOME/.zshrc")

if [ "$CHECK_PORT" -eq 1 ]; then
  [ "$PORT_REQUESTED" -eq 0 ] || fail "Use --check-port without --port"
  python3 "$REPO_DIR/scripts/manage_gateway_port.py" "${PORT_ARGS[@]}" check
  exit $?
fi

if [ "$PORT_REQUESTED" -eq 1 ]; then
  PORT=$(python3 "$REPO_DIR/scripts/manage_gateway_port.py" "${PORT_ARGS[@]}" resolve --port "$REQUESTED_PORT") \
    || fail "Invalid --port value: $REQUESTED_PORT"
else
  PORT=$(python3 "$REPO_DIR/scripts/manage_gateway_port.py" "${PORT_ARGS[@]}" resolve) \
    || fail "Fix SERVER_PORT in $ENV_FILE, then re-run setup"
fi

# On a complete existing installation, --port is a focused, non-destructive
# update. A partial .env continues through the full discovery/setup flow, while
# malformed existing configuration fails closed instead of being overwritten.
PORT_READY=1
if [ "$PORT_REQUESTED" -eq 1 ]; then
  if python3 "$REPO_DIR/scripts/manage_gateway_port.py" "${PORT_ARGS[@]}" ready; then
    PORT_READY=0
  else
    PORT_READY=$?
    [ "$PORT_READY" -eq 1 ] || fail "Existing port configuration is malformed; fix the reported error before setup"
  fi
fi
if [ "$PORT_REQUESTED" -eq 1 ] && [ "$PORT_READY" -eq 0 ]; then
  step "Updating existing gateway port"
  python3 "$REPO_DIR/scripts/manage_gateway_port.py" "${PORT_ARGS[@]}" set "$PORT" \
    || fail "Port update failed; existing configuration was preserved"
  ok "Gateway and Claude Code now use port $PORT"
  cat <<EOF

Restart checklist (complete one step at a time):
  1. Stop the running gateway with Ctrl+C.
  2. Start it again:  cd $REPO_DIR && python3 main.py
  3. Open a new Claude Code session so it reloads ~/.claude/settings.json.
  4. Verify alignment:  cd $REPO_DIR && ./setup.sh --check-port

The recommended ~/.zshrc helper runs python3 main.py without --port, so it
always follows SERVER_PORT from $ENV_FILE.
EOF
  if ! python3 "$REPO_DIR/scripts/manage_gateway_port.py" "${PORT_ARGS[@]}" check; then
    fail "Port files were updated, but the optional zsh helper still needs the fix shown above"
  fi
  exit 0
fi

if [ ! -f "$ENV_FILE" ] && [ "$PORT_REQUESTED" -eq 0 ] && [ "$ASSUME_YES" -eq 0 ]; then
  printf '  Gateway port [%s]: ' "$PORT"
  read -r reply
  if [ -n "$reply" ]; then
    PORT=$(python3 "$REPO_DIR/scripts/manage_gateway_port.py" "${PORT_ARGS[@]}" resolve --port "$reply") \
      || fail "Invalid port: $reply"
  fi
fi
ok "Gateway port: $PORT"

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

# Port shared by the gateway runtime and Claude Code.
SERVER_PORT="$PORT"

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
    info "Skipped. To configure it later, re-run setup or use:"
    info "  python3 scripts/sync_claude_models.py sync"
    info "The proxy token remains in .env and was not printed."
    ;;
  *)
    mkdir -p "$(dirname "$CLAUDE_SETTINGS")"
    KIRO_GATEWAY_SETUP_TOKEN="$PROXY_KEY" \
      python3 "$REPO_DIR/scripts/sync_claude_models.py" sync \
      --settings "$CLAUDE_SETTINGS" \
      --state "$REPO_DIR/state.json" \
      --accounts "$REPO_DIR/credentials.json" \
      --env-file "$ENV_FILE" \
      --base-url "http://localhost:$PORT" \
      --auth-token-env KIRO_GATEWAY_SETUP_TOKEN \
      || fail "Could not safely synchronize Claude Code settings. Fix the reported error and re-run setup."
    ok "Configured $CLAUDE_SETTINGS with the dynamic Kiro model catalog"
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
    python3 "$REPO_DIR/scripts/sync_claude_models.py" permission \
      --settings "$CLAUDE_SETTINGS" \
      --command "$CREDITS_CMD" \
      || fail "Could not safely update Claude Code permissions. Fix the reported error and re-run setup."
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
  Check port alignment: cd $REPO_DIR && ./setup.sh --check-port
  Change port safely:   cd $REPO_DIR && ./setup.sh --port 9000
  Switch model:         /model inside Claude Code
EOF

if python3 "$REPO_DIR/scripts/manage_gateway_port.py" "${PORT_ARGS[@]}" check; then
  ok "Gateway, Claude Code, and shell helper ports are aligned"
else
  warn "Setup finished, but port alignment needs attention; follow the check output above."
fi
