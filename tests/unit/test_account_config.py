# -*- coding: utf-8 -*-

"""Tests for setup account and dotenv reconciliation."""

import json
from pathlib import Path

from kiro.account_config import (
    canonical_account_id,
    reconcile_selected_json_account,
    render_setup_dotenv,
    set_preferred_account,
)


def test_default_mode_replaces_stale_account(tmp_path: Path) -> None:
    """Default mode selects only the freshly authorized direct credential."""
    accounts = tmp_path / "credentials.json"
    accounts.write_text('[{"type":"json","path":"old.json"}]', encoding="utf-8")
    selected = tmp_path / "direct.json"
    selected.write_text("{}", encoding="utf-8")

    account_id = reconcile_selected_json_account(
        accounts, selected, multi_account=False, profile_arn="arn:new", api_region="eu-central-1"
    )

    assert account_id == canonical_account_id(selected)
    assert json.loads(accounts.read_text(encoding="utf-8")) == [
        {
            "type": "json",
            "path": account_id,
            "enabled": True,
            "profile_arn": "arn:new",
            "api_region": "eu-central-1",
        }
    ]


def test_multi_account_preserves_unrelated_entries_and_unknown_fields(tmp_path: Path) -> None:
    """Multi-account reconciliation upserts direct IdC without deleting peers."""
    selected = tmp_path / "direct.json"
    selected.write_text("{}", encoding="utf-8")
    accounts = tmp_path / "credentials.json"
    accounts.write_text(
        json.dumps(
            [
                {"type": "json", "path": str(selected), "label": "keep-me", "enabled": False},
                {"type": "sqlite", "path": "other.sqlite", "custom": 1},
            ]
        ),
        encoding="utf-8",
    )

    account_id = reconcile_selected_json_account(accounts, selected, multi_account=True)
    payload = json.loads(accounts.read_text(encoding="utf-8"))

    assert payload[0]["path"] == account_id
    assert payload[0]["enabled"] is True
    assert payload[0]["label"] == "keep-me"
    assert payload[1] == {"type": "sqlite", "path": "other.sqlite", "custom": 1}


def test_dotenv_replaces_owned_keys_and_preserves_unrelated_settings() -> None:
    """Setup updates its keys without deleting account, VPN, or feature settings."""
    current = (
        'ACCOUNT_SYSTEM=true\nVPN_PROXY_URL="http://proxy"\n'
        'PROXY_API_KEY="old"\nKIRO_CREDS_FILE="old.json"\n'
    )

    rendered = render_setup_dotenv(
        current,
        {"PROXY_API_KEY": "same", "KIRO_CREDS_FILE": "/new.json", "SERVER_PORT": "4567"},
    )

    assert "ACCOUNT_SYSTEM=true" in rendered
    assert 'VPN_PROXY_URL="http://proxy"' in rendered
    assert rendered.count("PROXY_API_KEY=") == 1
    assert 'KIRO_CREDS_FILE="/new.json"' in rendered


def test_stable_preferred_account_preserves_existing_state(tmp_path: Path) -> None:
    """Selecting a direct account does not discard model or account state."""
    state = tmp_path / "state.json"
    state.write_text('{"current_account_index":1,"accounts":{"old":{}}}', encoding="utf-8")

    set_preferred_account(state, "/selected.json")

    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["current_account_id"] == "/selected.json"
    assert payload["current_account_index"] == 1
    assert payload["accounts"] == {"old": {}}
