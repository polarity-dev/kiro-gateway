# -*- coding: utf-8 -*-

"""Tests for safe Claude Code settings synchronization."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from kiro.claude_settings import (
    ClaudeSettingsError,
    load_claude_settings,
    merge_gateway_connection,
    merge_model_policy,
    merge_permission,
    render_claude_settings,
    validate_available_models,
    write_claude_settings_atomic,
)


def test_load_missing_settings_returns_empty_object(tmp_path: Path) -> None:
    """A first-time setup starts from an empty settings object."""
    assert load_claude_settings(tmp_path / "settings.json") == {}


@pytest.mark.parametrize("contents", ["{", "[]", '"text"'])
def test_load_rejects_malformed_or_non_object_settings(
    tmp_path: Path, contents: str
) -> None:
    """Invalid user settings must never be silently replaced."""
    path = tmp_path / "settings.json"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(ClaudeSettingsError):
        load_claude_settings(path)

    assert path.read_text(encoding="utf-8") == contents


@pytest.mark.parametrize(
    "value, expected",
    [
        (["a", "b"], ["a", "b"]),
        ([], []),
        (None, []),
        (["valid", 3], []),
        (["valid", ""], []),
        ("valid", []),
    ],
)
def test_validate_available_models_is_all_or_nothing(value: object, expected: list[str]) -> None:
    """A partially corrupt allowlist is not accepted as last-known-good state."""
    assert validate_available_models(value) == expected


def test_merge_policy_sorts_deduplicates_and_preserves_unrelated_settings() -> None:
    """The gateway replaces only catalog policy fields."""
    original = {"theme": "dark", "model": "model-b"}

    result = merge_model_policy(original, ["model-b", "model-a", "model-a"])

    assert result.settings == {
        "theme": "dark",
        "model": "model-b",
        "availableModels": ["model-a", "model-b"],
        "enforceAvailableModels": True,
    }
    assert result.selection_changed is False
    assert original == {"theme": "dark", "model": "model-b"}


def test_merge_policy_preserves_underlying_model_across_metadata_drift() -> None:
    """Changing cosmetic rate/context metadata keeps the selected Kiro model."""
    old = "claude-kiro-11-gpt-5.6-sol · 2.4x · 272k"
    new = "claude-kiro-11-gpt-5.6-sol · 2.2x · 1M"

    result = merge_model_policy({"model": old}, [new, "claude-opus-4.8 · 2x"])

    assert result.selected_model == new
    assert result.selection_changed is True


def test_merge_policy_prefers_real_auto_when_selection_disappears() -> None:
    """The Kiro auto router is preferred only when present in the catalog."""
    auto = "claude-kiro-4-auto · 1x"

    result = merge_model_policy(
        {"model": "retired"}, ["claude-zeta · 1x", auto, "claude-alpha · 1x"]
    )

    assert result.selected_model == auto
    assert result.settings["env"] == {
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": auto,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME": "Kiro Auto",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL_DESCRIPTION": (
            "Kiro server-side automatic model routing"
        ),
    }


def test_merge_policy_removes_stale_auto_default_when_auto_is_absent() -> None:
    """A retired auto router cannot remain as Claude Code's virtual Default."""
    result = merge_model_policy(
        {
            "env": {
                "KEEP": "yes",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "retired-auto",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME": "Kiro Auto",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL_DESCRIPTION": "old",
            }
        },
        ["claude-haiku-4.5"],
    )

    assert result.settings["env"] == {"KEEP": "yes"}


def test_merge_policy_uses_first_sorted_model_when_auto_is_absent() -> None:
    """A removed selection has a deterministic fallback without synthetic defaults."""
    result = merge_model_policy({"model": "retired"}, ["zeta", "alpha"])

    assert result.selected_model == "alpha"


def test_merge_policy_refuses_empty_catalog() -> None:
    """An empty enforced allowlist is never written."""
    with pytest.raises(ClaudeSettingsError, match="empty model catalog"):
        merge_model_policy({"model": "keep-me"}, [])


def test_merge_gateway_connection_removes_legacy_model_and_preserves_env() -> None:
    """Connection sync removes the higher-precedence legacy model override."""
    result = merge_gateway_connection(
        {"env": {"KEEP": "yes", "ANTHROPIC_MODEL": "old"}},
        "http://localhost:9000",
        "secret",
    )

    assert result["env"] == {
        "KEEP": "yes",
        "ANTHROPIC_BASE_URL": "http://localhost:9000",
        "ANTHROPIC_AUTH_TOKEN": "secret",
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
    }


def test_merge_gateway_connection_rejects_invalid_env_container() -> None:
    """Setup does not overwrite a malformed env setting."""
    with pytest.raises(ClaudeSettingsError, match="env"):
        merge_gateway_connection({"env": []}, "http://localhost:8000", "secret")


def test_merge_permission_is_idempotent_and_validates_shape() -> None:
    """Permission updates do not duplicate commands or repair malformed values."""
    command = "Bash(example)"
    once = merge_permission({}, command)
    twice = merge_permission(once, command)

    assert twice == {"permissions": {"allow": [command]}}
    with pytest.raises(ClaudeSettingsError, match="permissions.allow"):
        merge_permission({"permissions": {"allow": "wrong"}}, command)


def test_atomic_write_is_idempotent_and_preserves_mode(tmp_path: Path) -> None:
    """Unchanged settings are not replaced and existing permissions remain."""
    path = tmp_path / "settings.json"
    settings = {"model": "alpha"}

    assert write_claude_settings_atomic(path, settings) is True
    path.chmod(0o640)
    inode = path.stat().st_ino
    assert write_claude_settings_atomic(path, settings) is False

    assert path.stat().st_ino == inode
    assert path.stat().st_mode & 0o777 == 0o640
    assert path.read_text(encoding="utf-8") == render_claude_settings(settings)


def test_atomic_write_preserves_symlink_and_updates_target(tmp_path: Path) -> None:
    """Dotfile-managed settings symlinks survive atomic updates."""
    target_dir = tmp_path / "dotfiles"
    target_dir.mkdir()
    target = target_dir / "claude.json"
    target.write_text('{"model":"old"}\n', encoding="utf-8")
    target.chmod(0o640)
    link = tmp_path / "settings.json"
    link.symlink_to(target.relative_to(link.parent))

    assert write_claude_settings_atomic(link, {"model": "new"}) is True

    assert link.is_symlink()
    assert json.loads(target.read_text(encoding="utf-8")) == {"model": "new"}
    assert target.stat().st_mode & 0o777 == 0o640


def test_atomic_write_rejects_dangling_symlink(tmp_path: Path) -> None:
    """A missing dotfiles target is not silently replaced by a regular file."""
    link = tmp_path / "settings.json"
    link.symlink_to("missing.json")

    with pytest.raises(ClaudeSettingsError, match="Cannot read Claude settings"):
        write_claude_settings_atomic(link, {"model": "new"})

    assert link.is_symlink()
    assert not list(tmp_path.glob(".settings.json.*.tmp"))


def test_atomic_replace_failure_preserves_original(tmp_path: Path) -> None:
    """A failed replace leaves the original user settings untouched."""
    path = tmp_path / "settings.json"
    original = {"model": "old"}
    path.write_text(json.dumps(original), encoding="utf-8")

    with patch("kiro.claude_settings.os.replace", side_effect=OSError("blocked")):
        with pytest.raises(ClaudeSettingsError, match="atomically write"):
            write_claude_settings_atomic(path, {"model": "new"})

    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert not list(tmp_path.glob(".settings.json.*.tmp"))
