#!/usr/bin/env bash
#
# Kiro Gateway - setup for AWS IAM Identity Center users.
#
# --aws-profile performs AWS SSO OIDC device authorization and discovers the
# assigned Amazon Q Developer profile without Kiro IDE or Kiro CLI. Without it,
# the legacy Kiro IDE credential/log path remains available.
#
# Pass -y/--yes to accept local setup prompts; IAM Identity Center approval is
# still required. Keep direct login in a visible foreground terminal and approve
# only when the browser code exactly matches the printed Code: value. Use
# --port PORT to persist a custom port or --check-port to verify alignment.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$REPO_DIR/.env"
LEGACY_CREDS_FILE="$HOME/.aws/sso/cache/kiro-auth-token.json"
DIRECT_CREDS_FILE="$HOME/.aws/sso/cache/kiro-gateway-auth.json"
CREDS_FILE="$LEGACY_CREDS_FILE"
KIRO_LOGS="$HOME/Library/Application Support/Kiro/logs"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
PORT=""
REQUESTED_PORT=""
PORT_REQUESTED=0
CHECK_PORT=0
AWS_PROFILE_NAME=""
Q_PROFILE_SELECTOR=""
NO_BROWSER=0
AGENT_EVENTS=0
SETUP_STAGE="arguments"
SETUP_COMPLETED=0

# Detect event mode before parsing so argument failures also have a terminal
# event. In this mode stdout is a strict allowlisted JSONL channel; all human
# and third-party output is redirected to stderr.
for setup_arg in "$@"; do
  if [ "$setup_arg" = "--agent-events" ]; then
    AGENT_EVENTS=1
    break
  fi
done
if [ "$AGENT_EVENTS" -eq 1 ]; then
  exec 3>&1
  exec 1>&2
fi

emit_setup_event() {
  [ "$AGENT_EVENTS" -eq 1 ] || return 0
  printf '{"event":"KIRO_EVENT","scope":"setup","type":"%s","stage":"%s","category":"%s"}\n' \
    "$1" "$SETUP_STAGE" "$2" >&3
}
finish_setup_events() {
  status=$?
  if [ "$SETUP_COMPLETED" -eq 1 ] && [ "$status" -eq 0 ]; then
    emit_setup_event "setup_succeeded" "success"
  elif [ "$status" -eq 130 ]; then
    emit_setup_event "setup_cancelled" "cancelled"
  elif [ "$status" -ne 0 ]; then
    emit_setup_event "setup_failed" "failed"
  fi
}
trap finish_setup_events EXIT

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

# --yes accepts local setup prompts only. IAM Identity Center authorization
# remains an interactive user approval; multiple Q profiles need --q-profile.
ASSUME_YES=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -y|--yes)
      ASSUME_YES=1
      shift
      ;;
    --aws-profile)
      [ "$#" -ge 2 ] && [ -n "$2" ] && [[ "$2" != -* ]] \
        || fail "--aws-profile requires a profile name"
      AWS_PROFILE_NAME="$2"
      shift 2
      ;;
    --aws-profile=*)
      AWS_PROFILE_NAME="${1#*=}"
      [ -n "$AWS_PROFILE_NAME" ] || fail "--aws-profile requires a profile name"
      shift
      ;;
    --q-profile)
      [ "$#" -ge 2 ] && [ -n "$2" ] && [[ "$2" != -* ]] \
        || fail "--q-profile requires a profile name or ARN"
      Q_PROFILE_SELECTOR="$2"
      shift 2
      ;;
    --q-profile=*)
      Q_PROFILE_SELECTOR="${1#*=}"
      [ -n "$Q_PROFILE_SELECTOR" ] || fail "--q-profile requires a profile name or ARN"
      shift
      ;;
    --no-browser)
      NO_BROWSER=1
      shift
      ;;
    --agent-events)
      AGENT_EVENTS=1
      shift
      ;;
    --port)
      [ "$#" -ge 2 ] && [ -n "$2" ] && [[ "$2" != -* ]] \
        || fail "--port requires a value (try --help)"
      REQUESTED_PORT="$2"
      PORT_REQUESTED=1
      shift 2
      ;;
    --port=*)
      REQUESTED_PORT="${1#*=}"
      [ -n "$REQUESTED_PORT" ] || fail "--port requires a value (try --help)"
      PORT_REQUESTED=1
      shift
      ;;
    --check-port)
      CHECK_PORT=1
      shift
      ;;
    -h|--help)
      cat <<EOF
Usage: $0 [-y|--yes] [--aws-profile NAME] [--q-profile NAME_OR_ARN]
          [--no-browser] [--agent-events] [--port PORT] [--check-port]

  --aws-profile NAME       Log in directly through an AWS CLI IdC profile.
  --q-profile NAME_OR_ARN  Select among multiple Q Developer profiles.
  --no-browser             Print the approval URL without opening a browser.
  --agent-events           Emit only allowlisted KIRO_EVENT JSONL on stdout.
  --port PORT              Persist a custom port (1-65535).
  --check-port             Verify .env, Claude Code, and the optional zsh helper.
  -y, --yes                Accept local setup prompts only; IdC approval remains required.

Keep this command in a visible foreground terminal. Approve the AWS IAM Identity
Center request only if its code matches the printed Code: value exactly.
Without --aws-profile, setup reuses existing Kiro IDE credentials.
EOF
      exit 0
      ;;
    *) fail "Unknown argument: $1 (try --help)" ;;
  esac
done

[ -z "$Q_PROFILE_SELECTOR" ] || [ -n "$AWS_PROFILE_NAME" ] \
  || fail "--q-profile requires --aws-profile"
[ "$NO_BROWSER" -eq 0 ] || [ -n "$AWS_PROFILE_NAME" ] \
  || fail "--no-browser requires --aws-profile"
[ "$AGENT_EVENTS" -eq 0 ] || [ -n "$AWS_PROFILE_NAME" ] \
  || fail "--agent-events requires --aws-profile"
[ "$CHECK_PORT" -eq 0 ] || { [ -z "$AWS_PROFILE_NAME" ] && [ -z "$Q_PROFILE_SELECTOR" ] && [ "$NO_BROWSER" -eq 0 ] && [ "$AGENT_EVENTS" -eq 0 ]; } \
  || fail "Use --check-port without authentication options"

# ---------------------------------------------------------------------------
SETUP_STAGE="prerequisites"
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
if [ "$PORT_REQUESTED" -eq 1 ] && [ "$PORT_READY" -eq 0 ] && [ -z "$AWS_PROFILE_NAME" ]; then
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

ok "Gateway port: $PORT"

# Confirm all replacements before device login so cancellation leaves both the
# old dotenv and old refresh credentials untouched.
if [ -f "$ENV_FILE" ]; then
  if [ "$ASSUME_YES" -eq 1 ]; then
    reply="y"
  else
    printf '  .env already exists. Overwrite? [y/N] '
    read -r reply
  fi
  case "$reply" in
    [yY]) cp "$ENV_FILE" "$ENV_FILE.bak" && info "Backed up to .env.bak" ;;
    *)    fail "Aborted without changing credentials or .env." ;;
  esac
fi

LOGIN_ARGS_FORCE=0
if [ -n "$AWS_PROFILE_NAME" ] && [ -e "$DIRECT_CREDS_FILE" ]; then
  if [ "$ASSUME_YES" -eq 1 ]; then
    reply="y"
  else
    printf '  Direct-login credentials already exist. Replace them? [y/N] '
    read -r reply
  fi
  case "$reply" in
    [yY]) LOGIN_ARGS_FORCE=1 ;;
    *)    fail "Aborted without changing credentials." ;;
  esac
fi

# ---------------------------------------------------------------------------
SETUP_STAGE="identity_center_login"
step "2/7  Obtaining Kiro credentials"

if [ -n "$AWS_PROFILE_NAME" ]; then
  CREDS_FILE="$DIRECT_CREDS_FILE"
  # kiro_login.py completes OIDC and bearer ListAvailableProfiles discovery.
  LOGIN_ARGS=(--aws-profile "$AWS_PROFILE_NAME" --output "$CREDS_FILE")
  [ -z "$Q_PROFILE_SELECTOR" ] || LOGIN_ARGS+=(--q-profile "$Q_PROFILE_SELECTOR")
  [ "$NO_BROWSER" -eq 0 ] || LOGIN_ARGS+=(--no-browser)
  [ "$AGENT_EVENTS" -eq 0 ] || LOGIN_ARGS+=(--agent-events)
  [ "$LOGIN_ARGS_FORCE" -eq 0 ] || LOGIN_ARGS+=(--force)
  if [ "$AGENT_EVENTS" -eq 1 ]; then
    if python3 "$REPO_DIR/scripts/kiro_login.py" "${LOGIN_ARGS[@]}" >&3; then
      login_status=0
    else
      login_status=$?
    fi
  elif python3 "$REPO_DIR/scripts/kiro_login.py" "${LOGIN_ARGS[@]}"; then
    login_status=0
  else
    login_status=$?
  fi
  if [ "$login_status" -eq 0 ]; then
    ok "Direct IAM Identity Center credentials created"
  elif [ "$login_status" -eq 130 ]; then
    warn "IAM Identity Center login cancelled; rerun setup to request a fresh code."
    exit 130
  else
    fail "IAM Identity Center login failed. Review the message above and retry with a fresh code."
  fi
else
  [ -f "$CREDS_FILE" ] || fail \
    "$CREDS_FILE not found. Log in to Kiro IDE, or use --aws-profile NAME."
  ok "Existing Kiro IDE credentials found"
fi

# Read only non-secret metadata. Tokens never enter shell variables or output.
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
SETUP_STAGE="profile_discovery"
step "3/7  Resolving Q Developer profile"

if [ -z "$PROFILE_ARN" ] && [ -z "$AWS_PROFILE_NAME" ] && [ -d "$KIRO_LOGS" ]; then
  PROFILE_ARN=$(grep -rhao 'arn:aws:codewhisperer:[a-z0-9-]*:[0-9]*:profile/[A-Za-z0-9_-]*' \
    "$KIRO_LOGS" 2>/dev/null | sort | uniq -c | sort -rn | head -1 | awk '{print $2}')
fi
if [ -z "$PROFILE_ARN" ] && [ -z "$AWS_PROFILE_NAME" ] && [ "$ASSUME_YES" -eq 0 ]; then
  info "Could not recover profileArn from Kiro IDE logs."
  printf '  profileArn (arn:aws:codewhisperer:REGION:ACCOUNT:profile/ID): '
  read -r PROFILE_ARN
fi
[ -n "$PROFILE_ARN" ] || fail \
  "No Q Developer profile was discovered. Assign one or provide a legacy profileArn."
ok "Q Developer profile: $PROFILE_ARN"

# ---------------------------------------------------------------------------
SETUP_STAGE="api_region"
step "4/7  Determining Q API region"

ARN_REGION=$(printf '%s' "$PROFILE_ARN" | cut -d: -f4)
[ -n "$ARN_REGION" ] || fail "Malformed profileArn: cannot read region from '$PROFILE_ARN'."
if [ -n "$API_REGION" ] && [ "$API_REGION" != "$ARN_REGION" ]; then
  warn "Credential API region $API_REGION disagrees with profile ARN; using $ARN_REGION."
fi
API_REGION="$ARN_REGION"
if command -v host >/dev/null 2>&1; then
  host "runtime.$API_REGION.kiro.dev" >/dev/null 2>&1 \
    || fail "runtime.$API_REGION.kiro.dev does not resolve. Check your network or VPN."
fi
if [ "$API_REGION" != "$SSO_REGION" ]; then
  info "API region ($API_REGION) differs from SSO region (${SSO_REGION:-unknown}) — expected."
fi
ok "API region: $API_REGION"

# ---------------------------------------------------------------------------
SETUP_STAGE="environment"
step "5/7  Writing .env"

PROXY_KEY=$(PYTHONPATH="$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}" python3 - "$ENV_FILE" <<'PY'
import secrets
import sys
from pathlib import Path
from kiro.dotenv_utils import read_raw_dotenv_value
path = Path(sys.argv[1])
existing = read_raw_dotenv_value(path, "PROXY_API_KEY")
print(existing or secrets.token_urlsafe(32))
PY
) || fail "Could not preserve or generate the gateway proxy key"
KIRO_GATEWAY_SETUP_TOKEN="$PROXY_KEY" \
  python3 "$REPO_DIR/scripts/reconcile_setup_config.py" \
    --env-file "$ENV_FILE" \
    --accounts "$REPO_DIR/credentials.json" \
    --state "$REPO_DIR/state.json" \
    --credential "$CREDS_FILE" \
    --api-region "$API_REGION" \
    --profile-arn "$PROFILE_ARN" \
    --port "$PORT" \
    --proxy-key-env KIRO_GATEWAY_SETUP_TOKEN \
  || fail "Could not reconcile gateway credential configuration; existing files were preserved where possible."
ok "Reconciled $ENV_FILE and selected $CREDS_FILE"

# ---------------------------------------------------------------------------
SETUP_STAGE="claude_settings"
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
      --prefer-env \
      --base-url "http://localhost:$PORT" \
      --auth-token-env KIRO_GATEWAY_SETUP_TOKEN \
      || fail "Could not safely synchronize Claude Code settings. Fix the reported error and re-run setup."
    ok "Configured $CLAUDE_SETTINGS with the dynamic Kiro model catalog"
    ;;
esac

# ---------------------------------------------------------------------------
SETUP_STAGE="permissions"
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
if [ "$AGENT_EVENTS" -eq 0 ] && [ "$(uname)" = "Darwin" ] && [ -f "$REPO_DIR/scripts/swiftbar/install.sh" ]; then
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

SETUP_STAGE="verification"
if python3 "$REPO_DIR/scripts/manage_gateway_port.py" "${PORT_ARGS[@]}" check; then
  ok "Gateway, Claude Code, and shell helper ports are aligned"
else
  warn "Setup finished, but port alignment needs attention; follow the check output above."
fi
SETUP_COMPLETED=1
