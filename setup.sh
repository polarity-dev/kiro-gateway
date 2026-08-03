#!/usr/bin/env bash
#
# Kiro Gateway - Interactive setup for AWS IAM Identity Center users.
#
# Preferred path: --aws-profile performs AWS SSO OIDC device authorization and
# discovers the assigned Amazon Q Developer profile without Kiro IDE or Kiro CLI.
# With no --aws-profile, the legacy Kiro IDE credential/log discovery remains
# available for existing installations.
#
# Safe to re-run: prompts before overwriting anything.
# Pass -y/--yes to accept setup prompts; device authorization still requires the
# user to approve the displayed code in their browser.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$REPO_DIR/.env"
LEGACY_CREDS_FILE="$HOME/.aws/sso/cache/kiro-auth-token.json"
DIRECT_CREDS_FILE="$HOME/.aws/sso/cache/kiro-gateway-auth.json"
CREDS_FILE="$LEGACY_CREDS_FILE"
KIRO_LOGS="$HOME/Library/Application Support/Kiro/logs"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
PORT="4567"
AWS_PROFILE_NAME=""
Q_PROFILE_SELECTOR=""

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

# Non-interactive mode accepts setup prompts. IAM Identity Center device login
# still requires browser approval, and multiple Q profiles require --q-profile.
ASSUME_YES=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -y|--yes)
      ASSUME_YES=1
      shift
      ;;
    --aws-profile)
      [ "$#" -ge 2 ] || fail "--aws-profile requires a profile name"
      AWS_PROFILE_NAME="$2"
      shift 2
      ;;
    --q-profile)
      [ "$#" -ge 2 ] || fail "--q-profile requires a profile name or ARN"
      Q_PROFILE_SELECTOR="$2"
      shift 2
      ;;
    -h|--help)
      cat <<EOF
Usage: $0 [-y|--yes] [--aws-profile NAME] [--q-profile NAME_OR_ARN]

  --aws-profile NAME       Log in directly through the AWS CLI IdC profile.
                           Kiro IDE and Kiro CLI are not required.
  --q-profile NAME_OR_ARN  Select a Q Developer profile when more than one is assigned.
  -y, --yes                Accept setup prompts. Browser approval is still required.

Without --aws-profile, setup uses existing Kiro IDE credentials and logs.
EOF
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

# ---------------------------------------------------------------------------
step "2/7  Obtaining Kiro credentials"

if [ -n "$AWS_PROFILE_NAME" ]; then
  CREDS_FILE="$DIRECT_CREDS_FILE"
  LOGIN_ARGS=(
    --aws-profile "$AWS_PROFILE_NAME"
    --output "$CREDS_FILE"
  )
  if [ -n "$Q_PROFILE_SELECTOR" ]; then
    LOGIN_ARGS+=(--q-profile "$Q_PROFILE_SELECTOR")
  fi
  python3 "$REPO_DIR/scripts/kiro_login.py" "${LOGIN_ARGS[@]}" \
    || fail "IAM Identity Center login failed. Review the message above and retry."
  ok "Direct IAM Identity Center credentials created"
else
  [ -f "$CREDS_FILE" ] || fail \
    "$CREDS_FILE not found. Log in to Kiro IDE, or use --aws-profile NAME."
  ok "Existing Kiro IDE credentials found"
fi

# Read only non-secret metadata. The access and refresh tokens never enter shell
# variables and are never printed by setup.sh.
CREDENTIAL_METADATA=$(python3 - "$CREDS_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1]).expanduser()
with path.open(encoding="utf-8") as stream:
    data = json.load(stream)
required = ("accessToken", "refreshToken")
if any(not isinstance(data.get(key), str) or not data[key] for key in required):
    raise SystemExit("credential file is missing required tokens")
for key in ("region", "profileArn", "apiRegion"):
    value = data.get(key, "")
    print(value if isinstance(value, str) else "")
PY
) || fail "Could not read credential metadata from $CREDS_FILE"
SSO_REGION=$(printf '%s\n' "$CREDENTIAL_METADATA" | sed -n '1p')
PROFILE_ARN=$(printf '%s\n' "$CREDENTIAL_METADATA" | sed -n '2p')
API_REGION=$(printf '%s\n' "$CREDENTIAL_METADATA" | sed -n '3p')
ok "Credentials loaded (SSO region: ${SSO_REGION:-unknown})"

# ---------------------------------------------------------------------------
step "3/7  Resolving Q Developer profile"

if [ -z "$PROFILE_ARN" ] && [ -z "$AWS_PROFILE_NAME" ]; then
  # Legacy Kiro IDE fallback. Direct login obtains the ARN from the bearer
  # ListAvailableProfiles API and never reads Kiro logs.
  if [ -d "$KIRO_LOGS" ]; then
    PROFILE_ARN=$(grep -rhao 'arn:aws:codewhisperer:[a-z0-9-]*:[0-9]*:profile/[A-Za-z0-9_-]*' \
      "$KIRO_LOGS" 2>/dev/null | sort | uniq -c | sort -rn | head -1 | awk '{print $2}')
  fi
fi

if [ -z "$PROFILE_ARN" ]; then
  fail "No Q Developer profile was discovered. Ask an administrator to assign one, then retry."
fi
ok "Q Developer profile: $PROFILE_ARN"

# ---------------------------------------------------------------------------
step "4/7  Determining Q API region"

# Direct login records the region returned by ListAvailableProfiles. Legacy Kiro
# files fall back to the region field embedded in the profile ARN.
if [ -z "$API_REGION" ]; then
  API_REGION=$(printf '%s' "$PROFILE_ARN" | cut -d: -f4)
fi
if [ -z "$API_REGION" ]; then
  fail "Malformed profileArn: cannot read region from '$PROFILE_ARN'."
fi

if command -v host >/dev/null 2>&1; then
  host "runtime.$API_REGION.kiro.dev" >/dev/null 2>&1 \
    || fail "runtime.$API_REGION.kiro.dev does not resolve. Check your network or VPN."
fi

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

# Credentials used and refreshed automatically by the gateway.
KIRO_CREDS_FILE="$CREDS_FILE"

# Region hosting the Q API endpoint. Differs from your SSO region
# (${SSO_REGION:-unknown}) when the subscription lives elsewhere.
KIRO_API_REGION="$API_REGION"

# Amazon Q Developer profile discovered through ListAvailableProfiles or,
# for the legacy Kiro IDE path, recovered from existing local logs.
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
  Switch model:         /model inside Claude Code
EOF
