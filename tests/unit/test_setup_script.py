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
    assert 'SERVER_PORT="$PORT"' in script
    assert "--port PORT" in script
    assert "--check-port" in script


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
