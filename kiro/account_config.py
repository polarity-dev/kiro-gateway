# -*- coding: utf-8 -*-

"""Validated account configuration reconciliation helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kiro.atomic_io import AtomicWriteError, write_text_atomic


_SETUP_ENV_KEYS = {
    "PROXY_API_KEY",
    "KIRO_CREDS_FILE",
    "KIRO_API_REGION",
    "PROFILE_ARN",
    "SERVER_PORT",
    "DEBUG_MODE",
}


class AccountConfigError(RuntimeError):
    """Raised when account or state configuration cannot be reconciled safely."""


def render_setup_dotenv(current: str, values: dict[str, str]) -> str:
    """Replace setup-owned keys while preserving unrelated dotenv settings."""
    kept: list[str] = []
    for line in current.splitlines():
        stripped = line.strip()
        key = stripped.removeprefix("export ").split("=", 1)[0].strip()
        if "=" in stripped and key in _SETUP_ENV_KEYS:
            continue
        kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    generated = [
        "",
        "# Kiro Gateway configuration — managed by setup.sh",
        *(f'{key}={json.dumps(value, ensure_ascii=False)}' for key, value in values.items()),
    ]
    return "\n".join([*kept, *generated]).lstrip("\n") + "\n"


def write_setup_dotenv(path: Path, values: dict[str, str]) -> None:
    """Atomically update setup-owned dotenv keys without losing other settings."""
    try:
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        write_text_atomic(path, render_setup_dotenv(current, values))
    except (AtomicWriteError, OSError, UnicodeDecodeError) as exc:
        raise AccountConfigError(f"Cannot update gateway dotenv {path}: {exc}") from exc


def canonical_account_id(path: Path) -> str:
    """Return the stable account ID used by AccountManager for one file."""
    return str(path.expanduser().resolve())


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AccountConfigError(f"Cannot read valid JSON from {path}: {exc}") from exc


def reconcile_selected_json_account(
    accounts_path: Path,
    credential_path: Path,
    *,
    multi_account: bool,
    profile_arn: str | None = None,
    api_region: str | None = None,
) -> str:
    """Make one JSON credential authoritative without losing unrelated accounts."""
    selected_id = canonical_account_id(credential_path)
    existing = _load_json(accounts_path, [])
    if not isinstance(existing, list) or not all(isinstance(item, dict) for item in existing):
        raise AccountConfigError(f"Account configuration must be a JSON array: {accounts_path}")

    selected: dict[str, Any] = {}
    unrelated: list[dict[str, Any]] = []
    for entry in existing:
        same_path = False
        if entry.get("type") == "json" and isinstance(entry.get("path"), str):
            try:
                same_path = canonical_account_id(Path(entry["path"])) == selected_id
            except (OSError, RuntimeError):
                same_path = False
        if same_path and not selected:
            selected = dict(entry)
        elif multi_account:
            unrelated.append(dict(entry))

    selected.update({"type": "json", "path": selected_id, "enabled": True})
    if profile_arn:
        selected["profile_arn"] = profile_arn
    if api_region:
        selected["api_region"] = api_region
    reconciled = [selected, *unrelated] if multi_account else [selected]

    try:
        write_text_atomic(
            accounts_path,
            json.dumps(reconciled, indent=2, ensure_ascii=False) + "\n",
        )
    except AtomicWriteError as exc:
        raise AccountConfigError(str(exc)) from exc
    return selected_id


def set_preferred_account(state_path: Path, account_id: str) -> None:
    """Persist a stable preferred account while retaining unrelated state."""
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        raise AccountConfigError(f"Account state must be a JSON object: {state_path}")
    state["current_account_id"] = account_id
    try:
        write_text_atomic(
            state_path,
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        )
    except AtomicWriteError as exc:
        raise AccountConfigError(str(exc)) from exc
