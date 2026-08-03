# -*- coding: utf-8 -*-

"""Tests for environment-backed Kiro authentication construction."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from kiro.auth_factory import AuthConfigurationError, build_auth_manager_from_environment


@pytest.fixture(autouse=True)
def clear_auth_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove inherited credential values before every test."""
    for name in (
        "KIRO_CLI_DB_FILE",
        "KIRO_CREDS_FILE",
        "REFRESH_TOKEN",
        "PROFILE_ARN",
        "KIRO_REGION",
        "KIRO_API_REGION",
    ):
        monkeypatch.delenv(name, raising=False)


def test_dotenv_credentials_expand_home_and_override_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freshly-written setup dotenv is authoritative and supports tilde paths."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        'KIRO_CREDS_FILE="~/kiro.json"\n'
        'KIRO_REGION="us-west-2"\n'
        'KIRO_API_REGION="eu-central-1"\n'
        'PROFILE_ARN="arn:test"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KIRO_CREDS_FILE", "/stale/path")

    with patch("kiro.auth_factory.KiroAuthManager") as constructor:
        build_auth_manager_from_environment(env_file)

    constructor.assert_called_once_with(
        creds_file=str(Path("~/kiro.json").expanduser()),
        profile_arn="arn:test",
        region="us-west-2",
        api_region="eu-central-1",
    )
    assert os.environ["KIRO_CREDS_FILE"] == "/stale/path"
    assert "KIRO_API_REGION" not in os.environ


def test_credential_precedence_prefers_sqlite_then_file_then_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The factory follows the gateway's established credential precedence."""
    monkeypatch.setenv("KIRO_CLI_DB_FILE", "/db")
    monkeypatch.setenv("KIRO_CREDS_FILE", "/file")
    monkeypatch.setenv("REFRESH_TOKEN", "token")

    with patch("kiro.auth_factory.KiroAuthManager") as constructor:
        build_auth_manager_from_environment()

    assert constructor.call_args.kwargs["sqlite_db"] == "/db"
    assert "creds_file" not in constructor.call_args.kwargs
    assert "refresh_token" not in constructor.call_args.kwargs


def test_double_quoted_windows_path_remains_literal(tmp_path: Path) -> None:
    """Dotenv parsing must not decode backslashes in credential paths."""
    env_file = tmp_path / ".env"
    env_file.write_text('KIRO_CREDS_FILE="D:\\new\\token.json"\n', encoding="utf-8")

    with patch("kiro.auth_factory.KiroAuthManager") as constructor:
        build_auth_manager_from_environment(env_file)

    assert constructor.call_args.kwargs["creds_file"] == "D:\\new\\token.json"


def test_refresh_token_only_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refresh-token installations build the supported authentication branch."""
    monkeypatch.setenv("REFRESH_TOKEN", "token")

    with patch("kiro.auth_factory.KiroAuthManager") as constructor:
        build_auth_manager_from_environment()

    assert constructor.call_args.kwargs["refresh_token"] == "token"


def test_missing_credentials_is_actionable() -> None:
    """Setup fails clearly instead of trying an anonymous catalog request."""
    with pytest.raises(AuthConfigurationError, match="No Kiro credentials"):
        build_auth_manager_from_environment()
