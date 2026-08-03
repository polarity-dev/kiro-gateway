# -*- coding: utf-8 -*-

"""Tests for the Claude Code model synchronization command."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(scope="module")
def sync_module() -> ModuleType:
    """Load the sync script without invoking its command-line entry point."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "sync_claude_models.py"
    spec = importlib.util.spec_from_file_location("sync_claude_models_script", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load sync script from {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_live_catalog_has_priority(sync_module: ModuleType, tmp_path: Path) -> None:
    """Fresh Kiro discovery wins over state and user settings."""
    settings = {"availableModels": ["old"]}
    with patch.object(
        sync_module,
        "_discover_live_catalog",
        AsyncMock(return_value=["live-b", "live-a"]),
    ):
        resolved = await sync_module.resolve_catalog(
            settings,
            tmp_path / "state.json",
            tmp_path / ".env",
            tmp_path / "credentials.json",
        )

    assert resolved.model_ids == ["live-b", "live-a"]
    assert resolved.source == "live Kiro discovery"
    assert resolved.warning is None


@pytest.mark.asyncio
async def test_preferred_env_overrides_existing_stale_account_file(
    sync_module: ModuleType, tmp_path: Path
) -> None:
    """Direct setup uses its freshly selected credential despite stale accounts."""
    accounts = tmp_path / "credentials.json"
    accounts.write_text('[{"type":"json","path":"old.json"}]', encoding="utf-8")
    with patch.object(
        sync_module,
        "_discover_live_catalog",
        AsyncMock(return_value=["fresh-model"]),
    ) as live, patch.object(
        sync_module,
        "_discover_account_catalog",
        AsyncMock(side_effect=AssertionError("stale accounts must not win")),
    ):
        resolved = await sync_module.resolve_catalog(
            {},
            tmp_path / "state.json",
            tmp_path / ".env",
            accounts,
            prefer_env=True,
        )

    live.assert_awaited_once()
    assert resolved.model_ids == ["fresh-model"]
    assert resolved.source == "live selected credential discovery"


@pytest.mark.asyncio
async def test_state_lkg_is_used_when_live_discovery_fails(
    sync_module: ModuleType, tmp_path: Path
) -> None:
    """Persisted Kiro metadata is formatted through the production formatter."""
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "accounts": {
                    "a": {
                        "model_catalog": [
                            {"modelId": "auto", "rateMultiplier": 1},
                            {"modelId": "gpt-x"},
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    with patch.object(
        sync_module,
        "_discover_live_catalog",
        AsyncMock(side_effect=ValueError("expired token")),
    ):
        resolved = await sync_module.resolve_catalog(
            {}, state, tmp_path / ".env", tmp_path / "credentials.json"
        )

    assert resolved.model_ids == ["claude-kiro-4-auto · 1x", "claude-kiro-5-gpt-x"]
    assert resolved.source == "gateway state last-known-good"
    assert "expired token" in (resolved.warning or "")


@pytest.mark.asyncio
async def test_existing_allowlist_is_final_lkg(sync_module: ModuleType, tmp_path: Path) -> None:
    """An existing all-string allowlist survives both upstream source failures."""
    state = tmp_path / "state.json"
    state.write_text("{", encoding="utf-8")
    with patch.object(
        sync_module,
        "_discover_live_catalog",
        AsyncMock(side_effect=ValueError("offline")),
    ):
        resolved = await sync_module.resolve_catalog(
            {"availableModels": ["b", "a", "a"]},
            state,
            tmp_path / ".env",
            tmp_path / "credentials.json",
        )

    assert resolved.model_ids == ["a", "b"]
    assert resolved.source == "Claude settings last-known-good"


def test_no_valid_source_fails_without_write(
    sync_module: ModuleType, tmp_path: Path
) -> None:
    """No source can degrade into an enforced empty allowlist."""
    settings = tmp_path / "settings.json"
    original = '{"theme":"dark"}\n'
    settings.write_text(original, encoding="utf-8")
    with patch.object(
        sync_module,
        "_discover_live_catalog",
        AsyncMock(side_effect=ValueError("offline")),
    ):
        exit_code = sync_module.main(
            [
                "sync",
                "--settings",
                str(settings),
                "--state",
                str(tmp_path / "missing-state.json"),
                "--accounts",
                str(tmp_path / "missing-accounts.json"),
                "--env-file",
                str(tmp_path / ".env"),
            ]
        )

    assert exit_code == 2
    assert settings.read_text(encoding="utf-8") == original


def test_sync_writes_strings_connection_and_policy(
    sync_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sync updates connection and catalog in one atomic settings write."""
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"theme": "dark", "env": {"KEEP": "yes"}}), encoding="utf-8"
    )
    monkeypatch.setenv("TEST_GATEWAY_TOKEN", "secret")
    with patch.object(
        sync_module,
        "resolve_catalog",
        AsyncMock(
            return_value=sync_module.ResolvedCatalog(
                ["claude-kiro-4-auto · 1x", "claude-opus-4.8 · 2x"], "test"
            )
        ),
    ):
        exit_code = sync_module.main(
            [
                "sync",
                "--settings",
                str(settings),
                "--state",
                str(tmp_path / "state.json"),
                "--env-file",
                str(tmp_path / ".env"),
                "--base-url",
                "http://localhost:9000",
                "--auth-token-env",
                "TEST_GATEWAY_TOKEN",
            ]
        )

    payload = json.loads(settings.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["theme"] == "dark"
    assert payload["availableModels"] == [
        "claude-kiro-4-auto · 1x",
        "claude-opus-4.8 · 2x",
    ]
    assert all(isinstance(item, str) for item in payload["availableModels"])
    assert payload["enforceAvailableModels"] is True
    assert payload["model"] == "claude-kiro-4-auto · 1x"
    assert payload["env"]["KEEP"] == "yes"
    assert payload["env"]["ANTHROPIC_AUTH_TOKEN"] == "secret"


def test_bare_sync_removes_legacy_model_override(
    sync_module: ModuleType, tmp_path: Path
) -> None:
    """Catalog-only refresh removes the env override that beats `/model`."""
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"env": {"ANTHROPIC_MODEL": "stale", "KEEP": "yes"}}),
        encoding="utf-8",
    )
    with patch.object(
        sync_module,
        "resolve_catalog",
        AsyncMock(return_value=sync_module.ResolvedCatalog(["new"], "test")),
    ):
        assert sync_module.main(["sync", "--settings", str(settings)]) == 0

    payload = json.loads(settings.read_text(encoding="utf-8"))
    assert payload["env"] == {"KEEP": "yes"}
    assert payload["model"] == "new"


@pytest.mark.asyncio
async def test_incomplete_account_catalog_preserves_existing_allowlist(
    sync_module: ModuleType, tmp_path: Path
) -> None:
    """A partial multi-account result cannot shrink the enforced picker."""
    accounts = tmp_path / "credentials.json"
    accounts.write_text("[]", encoding="utf-8")
    with patch.object(
        sync_module, "_discover_account_catalog", AsyncMock(return_value=None)
    ):
        resolved = await sync_module.resolve_catalog(
            {"availableModels": ["account-a", "account-b"]},
            tmp_path / "state.json",
            tmp_path / ".env",
            accounts,
        )

    assert resolved.model_ids == ["account-a", "account-b"]
    assert resolved.source == "Claude settings last-known-good"


@pytest.mark.asyncio
async def test_complete_account_catalog_has_priority(
    sync_module: ModuleType, tmp_path: Path
) -> None:
    """All configured account catalogs replace legacy single-account discovery."""
    accounts = tmp_path / "credentials.json"
    accounts.write_text("[]", encoding="utf-8")
    with patch.object(
        sync_module,
        "_discover_account_catalog",
        AsyncMock(return_value=["account-a", "account-b"]),
    ), patch.object(sync_module, "_discover_live_catalog", AsyncMock()) as legacy:
        resolved = await sync_module.resolve_catalog(
            {}, tmp_path / "state.json", tmp_path / ".env", accounts
        )

    assert resolved.model_ids == ["account-a", "account-b"]
    assert resolved.source == "complete account-system catalog"
    legacy.assert_not_awaited()


def test_check_reports_drift_without_writing(sync_module: ModuleType, tmp_path: Path) -> None:
    """The check mode is safe for verification skills and CI."""
    settings = tmp_path / "settings.json"
    original = '{"model":"old"}\n'
    settings.write_text(original, encoding="utf-8")
    with patch.object(
        sync_module,
        "resolve_catalog",
        AsyncMock(return_value=sync_module.ResolvedCatalog(["new"], "test")),
    ):
        exit_code = sync_module.main(
            ["sync", "--settings", str(settings), "--check"]
        )

    assert exit_code == 1
    assert settings.read_text(encoding="utf-8") == original


def test_permission_subcommand_is_idempotent(sync_module: ModuleType, tmp_path: Path) -> None:
    """Setup can safely add its skill permission through the shared writer."""
    settings = tmp_path / "settings.json"
    command = "Bash(example)"
    argv = ["permission", "--settings", str(settings), "--command", command]

    assert sync_module.main(argv) == 0
    first = settings.read_bytes()
    assert sync_module.main(argv) == 0

    assert settings.read_bytes() == first
    assert json.loads(first)["permissions"]["allow"] == [command]
