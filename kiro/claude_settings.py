# -*- coding: utf-8 -*-

"""Safe synchronization helpers for Claude Code user settings."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from kiro.model_resolver import parse_model_display_id


class ClaudeSettingsError(RuntimeError):
    """Raised when Claude Code settings cannot be safely read or updated."""


@dataclass(frozen=True)
class ModelPolicyMerge:
    """Result of applying the gateway model policy to Claude Code settings."""

    settings: Dict[str, Any]
    selected_model: str
    previous_model: Optional[str]
    selection_changed: bool


def load_claude_settings(path: Path) -> Dict[str, Any]:
    """Load a Claude Code settings object without hiding malformed input.

    Args:
        path: Path to Claude Code's JSON settings file.

    Returns:
        Parsed settings object, or an empty object when the file does not exist.

    Raises:
        ClaudeSettingsError: If the file cannot be read, is malformed, or does
            not contain a JSON object.
    """
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClaudeSettingsError(f"Cannot read Claude settings from {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ClaudeSettingsError(f"Claude settings in {path} must be a JSON object")
    return payload


def validate_available_models(value: object) -> list[str]:
    """Validate an existing Claude Code model allowlist.

    Args:
        value: Candidate ``availableModels`` setting.

    Returns:
        A copy of the non-empty string list, or an empty list if any item is
        invalid. The all-or-nothing policy avoids preserving a partially corrupt
        allowlist as last-known-good state.
    """
    if not isinstance(value, list) or not value:
        return []
    if any(not isinstance(item, str) or not item for item in value):
        return []
    return list(value)


def _normalized_model_ids(model_ids: Iterable[str]) -> list[str]:
    """Return a deterministic non-empty list of Claude model IDs."""
    normalized = sorted({model_id for model_id in model_ids if isinstance(model_id, str) and model_id})
    if not normalized:
        raise ClaudeSettingsError(
            "Refusing to enforce an empty model catalog; no Claude settings were changed."
        )
    return normalized


def _select_model(previous: object, model_ids: list[str]) -> tuple[str, Optional[str]]:
    """Preserve a model selection by exact ID or underlying Kiro model ID."""
    previous_model = previous if isinstance(previous, str) and previous else None
    if previous_model in model_ids:
        return previous_model, previous_model

    if previous_model:
        previous_raw = parse_model_display_id(previous_model)
        for model_id in model_ids:
            if parse_model_display_id(model_id) == previous_raw:
                return model_id, previous_model

    for model_id in model_ids:
        if parse_model_display_id(model_id) == "auto":
            return model_id, previous_model
    return model_ids[0], previous_model


def merge_model_policy(
    settings: Dict[str, Any],
    model_ids: Iterable[str],
) -> ModelPolicyMerge:
    """Apply a non-empty gateway catalog to Claude Code settings.

    Args:
        settings: Existing settings object. It is not mutated.
        model_ids: Claude Code-compatible IDs exposed by the gateway.

    Returns:
        Merge result including selection-change information.

    Raises:
        ClaudeSettingsError: If the model catalog is empty.
    """
    normalized = _normalized_model_ids(model_ids)
    merged = dict(settings)
    existing_env = merged.get("env", {})
    if not isinstance(existing_env, dict):
        raise ClaudeSettingsError("Claude setting 'env' must be a JSON object")
    env = dict(existing_env)
    env.pop("ANTHROPIC_MODEL", None)
    if env or "env" in merged:
        merged["env"] = env

    selected, previous = _select_model(merged.get("model"), normalized)
    merged["availableModels"] = normalized
    merged["enforceAvailableModels"] = True
    merged["model"] = selected
    return ModelPolicyMerge(
        settings=merged,
        selected_model=selected,
        previous_model=previous,
        selection_changed=previous != selected,
    )


def merge_gateway_connection(
    settings: Dict[str, Any],
    base_url: str,
    auth_token: str,
) -> Dict[str, Any]:
    """Merge gateway connection values while preserving unrelated settings.

    Args:
        settings: Existing settings object. It is not mutated.
        base_url: Local gateway URL.
        auth_token: Gateway proxy token.

    Returns:
        Updated settings object.

    Raises:
        ClaudeSettingsError: If the existing ``env`` value is not an object.
    """
    merged = dict(settings)
    existing_env = merged.get("env", {})
    if not isinstance(existing_env, dict):
        raise ClaudeSettingsError("Claude setting 'env' must be a JSON object")
    env = dict(existing_env)
    env.update(
        {
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_AUTH_TOKEN": auth_token,
            "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
        }
    )
    env.pop("ANTHROPIC_MODEL", None)
    merged["env"] = env
    return merged


def merge_permission(settings: Dict[str, Any], command: str) -> Dict[str, Any]:
    """Add one Claude Code permission without duplicates.

    Args:
        settings: Existing settings object. It is not mutated.
        command: Permission expression to add.

    Returns:
        Updated settings object.

    Raises:
        ClaudeSettingsError: If existing permission containers have wrong types.
    """
    merged = dict(settings)
    existing_permissions = merged.get("permissions", {})
    if not isinstance(existing_permissions, dict):
        raise ClaudeSettingsError("Claude setting 'permissions' must be a JSON object")
    permissions = dict(existing_permissions)
    existing_allow = permissions.get("allow", [])
    if not isinstance(existing_allow, list) or any(
        not isinstance(item, str) for item in existing_allow
    ):
        raise ClaudeSettingsError("Claude setting 'permissions.allow' must be a string array")
    allow = list(existing_allow)
    if command not in allow:
        allow.append(command)
    permissions["allow"] = allow
    merged["permissions"] = permissions
    return merged


def render_claude_settings(settings: Dict[str, Any]) -> str:
    """Serialize settings deterministically with a trailing newline."""
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


def write_claude_settings_atomic(path: Path, settings: Dict[str, Any]) -> bool:
    """Write settings atomically, preserving mode and avoiding no-op replaces.

    Args:
        path: Destination settings file.
        settings: Complete settings object to serialize.

    Returns:
        ``True`` when the file changed, otherwise ``False``.

    Raises:
        ClaudeSettingsError: If the destination cannot be written atomically.
    """
    rendered = render_claude_settings(settings)
    try:
        if path.is_symlink():
            target_path = path.resolve(strict=True)
        else:
            target_path = path
        current = target_path.read_text(encoding="utf-8") if target_path.exists() else None
    except (OSError, UnicodeDecodeError) as exc:
        raise ClaudeSettingsError(f"Cannot read Claude settings from {path}: {exc}") from exc
    if current == rendered:
        return False

    temporary_path: Optional[Path] = None
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        existing_mode = (
            stat.S_IMODE(target_path.stat().st_mode) if target_path.exists() else 0o600
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target_path.name}.", suffix=".tmp", dir=target_path.parent
        )
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, existing_mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target_path)
        temporary_path = None
        return True
    except OSError as exc:
        raise ClaudeSettingsError(f"Cannot atomically write Claude settings to {path}: {exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
