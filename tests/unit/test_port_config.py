# -*- coding: utf-8 -*-

"""Tests for persisted gateway port configuration and alignment checks."""

from pathlib import Path

import json
import pytest

from kiro.claude_settings import ClaudeSettingsError
from kiro.port_config import (
    PortConfigurationError,
    check_port_alignment,
    configure_gateway_port,
    resolve_gateway_port,
    validate_port,
)


@pytest.mark.parametrize("value", ["", "abc", " 9000", "+9000", "-1", "0", "65536"])
def test_validate_port_rejects_invalid_values(value: str) -> None:
    """Only plain decimal TCP ports in the valid range are accepted."""
    with pytest.raises(PortConfigurationError, match="1 to 65535"):
        validate_port(value)


@pytest.mark.parametrize(("value", "expected"), [("1", 1), ("4567", 4567), (65535, 65535)])
def test_validate_port_accepts_valid_values(value: object, expected: int) -> None:
    """Valid integer and decimal-string ports normalize to integers."""
    assert validate_port(value) == expected


def test_resolve_gateway_port_uses_default_or_persisted_value(tmp_path: Path) -> None:
    """An absent key uses the default while a custom dotenv value is preserved."""
    env_path = tmp_path / ".env"
    assert resolve_gateway_port(env_path) == 4567

    env_path.write_text('PROXY_API_KEY="secret"\nSERVER_PORT=9000\n', encoding="utf-8")
    assert resolve_gateway_port(env_path) == 9000


def test_resolve_gateway_port_accepts_python_dotenv_syntax(tmp_path: Path) -> None:
    """Setup resolves the same export, spacing, and comment syntax as runtime."""
    env_path = tmp_path / ".env"
    env_path.write_text("export SERVER_PORT = 9000 # local gateway\n", encoding="utf-8")

    assert resolve_gateway_port(env_path) == 9000


def test_resolve_gateway_port_rejects_duplicate_entries(tmp_path: Path) -> None:
    """Ambiguous dotenv ports fail rather than disagreeing with runtime parsing."""
    env_path = tmp_path / ".env"
    env_path.write_text('SERVER_PORT="9000"\nSERVER_PORT="9100"\n', encoding="utf-8")

    with pytest.raises(PortConfigurationError, match="multiple SERVER_PORT"):
        resolve_gateway_port(env_path)


def test_configure_gateway_port_updates_both_files_and_preserves_settings(
    tmp_path: Path,
) -> None:
    """Changing a port reuses credentials and preserves unrelated Claude settings."""
    env_path = tmp_path / ".env"
    settings_path = tmp_path / "settings.json"
    env_path.write_text(
        'PROXY_API_KEY="secret"\nSERVER_PORT="9000"\nKEEP="yes"\n',
        encoding="utf-8",
    )
    settings_path.write_text(
        json.dumps(
            {
                "theme": "dark",
                "env": {
                    "KEEP": "yes",
                    "ANTHROPIC_BASE_URL": "http://localhost:9000",
                    "ANTHROPIC_AUTH_TOKEN": "existing-token",
                    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
                },
            }
        ),
        encoding="utf-8",
    )

    assert configure_gateway_port(env_path, settings_path, "9100") == 9100

    assert 'SERVER_PORT="9100"' in env_path.read_text(encoding="utf-8")
    assert 'SERVER_PORT="9000"' not in env_path.read_text(encoding="utf-8")
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert payload["theme"] == "dark"
    assert payload["env"] == {
        "KEEP": "yes",
        "ANTHROPIC_BASE_URL": "http://localhost:9100",
        "ANTHROPIC_AUTH_TOKEN": "existing-token",
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
    }


def test_configure_gateway_port_preserves_symlinked_env(tmp_path: Path) -> None:
    """Changing ports updates a managed dotenv target without replacing its link."""
    target = tmp_path / "managed.env"
    target.write_text('PROXY_API_KEY="secret"\nSERVER_PORT="9000"\n', encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.symlink_to(target.name)
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "env": {
                    "ANTHROPIC_BASE_URL": "http://localhost:9000",
                    "ANTHROPIC_AUTH_TOKEN": "existing-token",
                }
            }
        ),
        encoding="utf-8",
    )

    assert configure_gateway_port(env_path, settings_path, 9100) == 9100

    assert env_path.is_symlink()
    assert 'SERVER_PORT="9100"' in target.read_text(encoding="utf-8")
    assert (tmp_path / ".env.bak").exists()


def test_configure_gateway_port_is_byte_stable_when_already_current(
    tmp_path: Path,
) -> None:
    """Repeated configuration does not rewrite already aligned files."""
    env_path = tmp_path / ".env"
    settings_path = tmp_path / "settings.json"
    env_path.write_text('PROXY_API_KEY="secret"\nSERVER_PORT="9000"\n', encoding="utf-8")
    settings_path.write_text(
        json.dumps(
            {
                "env": {
                    "ANTHROPIC_BASE_URL": "http://localhost:9000",
                    "ANTHROPIC_AUTH_TOKEN": "secret",
                    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    before_env = env_path.read_bytes()
    before_settings = settings_path.read_bytes()
    assert configure_gateway_port(env_path, settings_path, 9000) == 9000
    assert env_path.read_bytes() == before_env
    assert settings_path.read_bytes() == before_settings


def test_configure_gateway_port_validates_settings_before_writing_env(
    tmp_path: Path,
) -> None:
    """Malformed Claude settings leave the runtime dotenv untouched."""
    env_path = tmp_path / ".env"
    settings_path = tmp_path / "settings.json"
    original = 'PROXY_API_KEY="secret"\nSERVER_PORT="9000"\n'
    env_path.write_text(original, encoding="utf-8")
    settings_path.write_text('{"env": []}\n', encoding="utf-8")

    with pytest.raises(ClaudeSettingsError, match="env"):
        configure_gateway_port(env_path, settings_path, 9100)

    assert env_path.read_text(encoding="utf-8") == original


def test_check_port_alignment_accepts_port_neutral_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recommended helper follows the checkout dotenv without another port."""
    monkeypatch.delenv("SERVER_PORT", raising=False)
    monkeypatch.setattr(
        "kiro.port_config._check_live_gateway",
        lambda port: (f"not running on {port}", None),
    )
    env_path = tmp_path / ".env"
    settings_path = tmp_path / "settings.json"
    zshrc_path = tmp_path / ".zshrc"
    env_path.write_text('SERVER_PORT="9000"\n', encoding="utf-8")
    settings_path.write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://localhost:9000"}}),
        encoding="utf-8",
    )
    zshrc_path.write_text(
        'kiro-gateway() {\n  (cd "$HOME/repo/kiro-gateway" && python3 main.py)\n}\n',
        encoding="utf-8",
    )

    result = check_port_alignment(env_path, settings_path, zshrc_path)

    assert result.aligned
    assert result.helper_status == "follows SERVER_PORT from .env"


def test_check_port_alignment_reports_claude_helper_and_shell_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every independent port override is reported with actionable context."""
    monkeypatch.setenv("SERVER_PORT", "9200")
    monkeypatch.setattr(
        "kiro.port_config._check_live_gateway",
        lambda port: (f"not running on {port}", None),
    )
    env_path = tmp_path / ".env"
    settings_path = tmp_path / "settings.json"
    zshrc_path = tmp_path / ".zshrc"
    env_path.write_text('SERVER_PORT="9000"\n', encoding="utf-8")
    settings_path.write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://localhost:4567"}}),
        encoding="utf-8",
    )
    zshrc_path.write_text(
        'kiro-gateway() {\n  python3 main.py --port 9100\n}\n', encoding="utf-8"
    )

    result = check_port_alignment(env_path, settings_path, zshrc_path)

    assert not result.aligned
    assert any("SERVER_PORT=9200" in problem for problem in result.problems)
    assert any("localhost:4567" in problem for problem in result.problems)
    assert any("hard-codes port 9100" in problem for problem in result.problems)


def test_check_port_alignment_rejects_matching_hardcoded_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A matching helper override is still future drift and must be removed."""
    monkeypatch.delenv("SERVER_PORT", raising=False)
    monkeypatch.setattr(
        "kiro.port_config._check_live_gateway",
        lambda port: (f"not running on {port}", None),
    )
    env_path = tmp_path / ".env"
    settings_path = tmp_path / "settings.json"
    zshrc_path = tmp_path / ".zshrc"
    env_path.write_text('SERVER_PORT="9000"\n', encoding="utf-8")
    settings_path.write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://localhost:9000"}}),
        encoding="utf-8",
    )
    zshrc_path.write_text(
        "kiro-gateway() {\n  python3 main.py --port 9000\n}\n",
        encoding="utf-8",
    )

    result = check_port_alignment(env_path, settings_path, zshrc_path)

    assert not result.aligned
    assert result.helper_status == "hard-coded to 9000"
    assert any("remove --port" in problem for problem in result.problems)
