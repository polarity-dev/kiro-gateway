# -*- coding: utf-8 -*-

"""Structural regression tests for the setup script integration."""

from pathlib import Path


SETUP_SCRIPT = Path(__file__).resolve().parents[2] / "setup.sh"


def test_setup_resolves_and_persists_one_shared_port() -> None:
    """Setup uses the port manager instead of duplicating a fixed default."""
    script = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert 'manage_gateway_port.py" "${PORT_ARGS[@]}" resolve' in script
    assert 'manage_gateway_port.py" "${PORT_ARGS[@]}" ready' in script
    assert 'manage_gateway_port.py" "${PORT_ARGS[@]}" set "$PORT"' in script
    assert 'manage_gateway_port.py" "${PORT_ARGS[@]}" check' in script
    assert 'reconcile_setup_config.py"' in script
    assert '--port "$PORT"' in script
    assert "--port PORT" in script
    assert "--check-port" in script


def test_setup_uses_default_port_without_prompting() -> None:
    """Setup accepts the resolved default unless --port is supplied."""
    script = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert "Gateway port [%s]:" not in script
    assert 'ok "Gateway port: $PORT"' in script


def test_setup_prints_port_neutral_shell_helper() -> None:
    """The recommended helper delegates port selection to the checkout dotenv."""
    script = SETUP_SCRIPT.read_text(encoding="utf-8")

    helper = script.split("Optional shell helper for ~/.zshrc:", 1)[1]
    helper = helper.split("Available models:", 1)[0]
    assert "python3 main.py" in helper
    assert "--port" not in helper


def test_setup_delegates_model_sync_to_safe_command() -> None:
    """Setup uses the tested synchronizer instead of inline JSON mutation."""
    script = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert 'sync_claude_models.py" sync' in script
    assert '--state "$REPO_DIR/state.json"' in script
    assert '--accounts "$REPO_DIR/credentials.json"' in script
    assert '--env-file "$ENV_FILE"' in script
    assert '--base-url "http://localhost:$PORT"' in script
    assert 'KIRO_GATEWAY_SETUP_TOKEN="$PROXY_KEY"' in script
    assert "--auth-token-env KIRO_GATEWAY_SETUP_TOKEN" in script
    assert "--auth-token \"$PROXY_KEY\"" not in script
    assert "availableModels" not in script
    assert "DEFAULT_MODEL" not in script
    assert "claude-auto · 1x" not in script


def test_setup_delegates_permission_update_to_safe_command() -> None:
    """Permission setup shares validated loading and atomic writing."""
    script = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert 'sync_claude_models.py" permission' in script
    assert '--command "$CREDITS_CMD"' in script
    assert "json.dump(settings" not in script


def test_setup_never_prints_proxy_token() -> None:
    """Skipped configuration guidance must not expose the generated secret."""
    script = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert 'info "  ANTHROPIC_AUTH_TOKEN=$PROXY_KEY"' not in script
    assert "The proxy token remains in .env and was not printed." in script


def test_setup_defines_prompt_helper_for_optional_installs() -> None:
    """Interactive and --yes SwiftBar paths use a defined prompt helper."""
    script = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert "ask() {" in script
    assert 'if ask "Install the SwiftBar menu bar widget?"' in script


def test_setup_supports_direct_identity_center_login() -> None:
    """The installer can bootstrap from an AWS profile without Kiro artifacts."""
    script = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert "--aws-profile" in script
    assert "--q-profile" in script
    assert 'scripts/kiro_login.py" "${LOGIN_ARGS[@]}"' in script
    assert 'CREDS_FILE="$DIRECT_CREDS_FILE"' in script
    assert '--credential "$CREDS_FILE"' in script
    assert "--prefer-env" in script
    assert "ListAvailableProfiles" in script
    assert "LOGIN_ARGS_FORCE=1" in script
    assert 'LOGIN_ARGS+=(--force)' in script
    assert "LOGIN_FORCE_ARGS" not in script
    assert script.index('.env already exists. Overwrite?') < script.index('scripts/kiro_login.py')
    assert '--q-profile requires --aws-profile' in script
    assert '--no-browser requires --aws-profile' in script
    assert '--agent-events requires --aws-profile' in script
    assert 'exec 3>&1' in script
    assert 'exec 1>&2' in script
    assert '"${LOGIN_ARGS[@]}" >&3' in script
    assert '"setup_succeeded"' in script
    assert '"setup_cancelled"' in script
    assert '"setup_failed"' in script
    assert '[ "$NO_BROWSER" -eq 0 ] || LOGIN_ARGS+=(--no-browser)' in script
    assert "visible foreground terminal" in script
    assert "matches the printed Code: value exactly" in script
    assert 'if python3 "$REPO_DIR/scripts/kiro_login.py" "${LOGIN_ARGS[@]}"; then' in script
    assert 'if [ "$login_status" -eq 130 ]; then' in script
    assert "exit 130" in script
    assert '[[ "$2" != -* ]]' in script


def test_setup_does_not_load_tokens_into_shell_variables() -> None:
    """Setup reads non-secret metadata without exposing tokens to the shell."""
    script = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert "ACCESS_TOKEN=" not in script
    assert 'required = ("accessToken", "refreshToken")' in script
    assert "print(data['accessToken'])" not in script
