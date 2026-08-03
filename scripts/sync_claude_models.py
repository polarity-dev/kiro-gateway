#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Synchronize Claude Code settings with Kiro Gateway's dynamic catalog."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from kiro.account_manager import AccountManager  # noqa: E402
from kiro.auth_factory import (  # noqa: E402
    AuthConfigurationError,
    build_auth_manager_from_environment,
)
from kiro.claude_settings import (  # noqa: E402
    ClaudeSettingsError,
    load_claude_settings,
    merge_gateway_connection,
    merge_model_policy,
    merge_permission,
    render_claude_settings,
    validate_available_models,
    write_claude_settings_atomic,
)
from kiro.model_discovery import (  # noqa: E402
    ModelDiscoveryError,
    build_catalog_display_ids,
    fetch_available_models,
    load_state_model_catalog,
)


@dataclass(frozen=True)
class ResolvedCatalog:
    """Model IDs selected for synchronization and their source."""

    model_ids: list[str]
    source: str
    warning: Optional[str] = None


async def _discover_live_catalog(env_file: Path) -> list[str]:
    """Discover and format one legacy environment-backed Kiro catalog.

    Args:
        env_file: Explicit gateway dotenv file.

    Returns:
        Sorted Claude-compatible model IDs.

    Raises:
        AuthConfigurationError: If the dotenv file has no credential source.
        ModelDiscoveryError: If Kiro returns an invalid or empty catalog.
        ValueError: If the configured credential cannot obtain an access token.
        httpx.HTTPError: If authentication fails at the transport layer.
    """
    auth = build_auth_manager_from_environment(env_file)
    await auth.get_access_token()
    return build_catalog_display_ids(await fetch_available_models(auth))


async def _discover_account_catalog(
    accounts_path: Path,
    state_path: Path,
) -> Optional[list[str]]:
    """Discover the complete catalog across configured account-system entries.

    Args:
        accounts_path: Account-system credentials configuration.
        state_path: Per-account last-known-good state file.

    Returns:
        Complete sorted union, or ``None`` when any configured account lacks
        both live and last-known-good metadata.
    """
    manager = AccountManager(str(accounts_path), str(state_path))
    await manager.load_credentials()
    await manager.load_state()
    if not await manager.initialize_all_model_catalogs():
        return None
    model_ids = manager.get_all_available_models()
    return model_ids or None


async def resolve_catalog(
    settings: dict,
    state_path: Path,
    env_file: Path,
    accounts_path: Path,
    *,
    prefer_env: bool = False,
) -> ResolvedCatalog:
    """Resolve models from live Kiro, gateway state, or Claude LKG settings.

    Args:
        settings: Existing Claude Code settings.
        state_path: Gateway state file containing per-account catalogs.
        env_file: Gateway dotenv file used for legacy Kiro authentication.
        accounts_path: Account-system credentials configuration.

    Returns:
        Non-empty model catalog and source information.

    Raises:
        ClaudeSettingsError: If every source is unavailable or invalid.
    """
    failures: list[str] = []
    if prefer_env or not accounts_path.exists():
        try:
            live_ids = await _discover_live_catalog(env_file)
            source = (
                "live selected credential discovery"
                if prefer_env
                else "live Kiro discovery"
            )
            return ResolvedCatalog(live_ids, source)
        except (
            AuthConfigurationError,
            ModelDiscoveryError,
            OSError,
            ValueError,
            httpx.HTTPError,
        ) as exc:
            failures.append(f"live discovery: {exc}")

    if accounts_path.exists():
        try:
            account_ids = await _discover_account_catalog(accounts_path, state_path)
            if account_ids:
                return ResolvedCatalog(
                    account_ids,
                    "complete account-system catalog",
                    warning="; ".join(failures) or None,
                )
            failures.append(
                "account system: at least one configured account has no live or "
                "last-known-good catalog"
            )
        except (
            ModelDiscoveryError,
            OSError,
            ValueError,
            httpx.HTTPError,
        ) as exc:
            failures.append(f"account system: {exc}")

    try:
        state_catalog = load_state_model_catalog(state_path)
        if state_catalog:
            return ResolvedCatalog(
                build_catalog_display_ids(state_catalog),
                "gateway state last-known-good",
                warning="; ".join(failures),
            )
        failures.append("gateway state: no saved model catalog")
    except ModelDiscoveryError as exc:
        failures.append(f"gateway state: {exc}")

    existing = validate_available_models(settings.get("availableModels"))
    if existing:
        return ResolvedCatalog(
            sorted(set(existing)),
            "Claude settings last-known-good",
            warning="; ".join(failures),
        )

    raise ClaudeSettingsError(
        "No non-empty model catalog is available. "
        + "; ".join(failures)
        + ". Existing Claude settings were left unchanged."
    )


async def synchronize_models(args: argparse.Namespace) -> int:
    """Synchronize gateway connection and model policy."""
    settings_path = Path(args.settings).expanduser()
    settings = load_claude_settings(settings_path)
    catalog = await resolve_catalog(
        settings,
        Path(args.state).expanduser(),
        Path(args.env_file).expanduser(),
        Path(args.accounts).expanduser(),
        prefer_env=args.prefer_env,
    )

    updated = settings
    auth_token = os.getenv(args.auth_token_env) if args.auth_token_env else None
    if args.base_url is not None or args.auth_token_env is not None:
        if not args.base_url or not auth_token:
            raise ClaudeSettingsError(
                "--base-url and --auth-token-env must be provided together, and "
                "the named environment variable must be non-empty"
            )
        updated = merge_gateway_connection(
            updated,
            base_url=args.base_url,
            auth_token=auth_token,
        )

    merge = merge_model_policy(updated, catalog.model_ids)
    changed = render_claude_settings(settings) != render_claude_settings(merge.settings)

    if catalog.warning:
        print(f"Warning: {catalog.warning}", file=sys.stderr)
    print(f"Catalog source: {catalog.source}")
    print(f"Models synchronized: {len(catalog.model_ids)}")
    if merge.selection_changed:
        previous = merge.previous_model or "<unset>"
        print(f"Selected model: {previous} -> {merge.selected_model}")
    else:
        print(f"Selected model: {merge.selected_model}")

    if args.check:
        if changed:
            print("Claude settings are out of sync.", file=sys.stderr)
            return 1
        print("Claude settings are in sync.")
        return 0

    was_written = write_claude_settings_atomic(settings_path, merge.settings)
    print("Claude settings updated." if was_written else "Claude settings already current.")
    return 0


def update_permission(args: argparse.Namespace) -> int:
    """Add one permission using the same validated atomic settings path."""
    settings_path = Path(args.settings).expanduser()
    settings = load_claude_settings(settings_path)
    updated = merge_permission(settings, args.command)
    was_written = write_claude_settings_atomic(settings_path, updated)
    print("Claude permission added." if was_written else "Claude permission already present.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync = subparsers.add_parser("sync", help="Synchronize the dynamic model catalog")
    sync.add_argument("--settings", default="~/.claude/settings.json")
    sync.add_argument("--state", default=str(_REPO_ROOT / "state.json"))
    sync.add_argument("--accounts", default=str(_REPO_ROOT / "credentials.json"))
    sync.add_argument("--env-file", default=str(_REPO_ROOT / ".env"))
    sync.add_argument(
        "--prefer-env",
        action="store_true",
        help="Prefer the credential selected by --env-file over an existing account file",
    )
    sync.add_argument("--base-url")
    sync.add_argument(
        "--auth-token-env",
        help="Name of the environment variable containing the gateway token",
    )
    sync.add_argument(
        "--check",
        action="store_true",
        help="Report drift without changing the settings file",
    )

    permission = subparsers.add_parser(
        "permission", help="Add one Claude Code permission safely"
    )
    permission.add_argument("--settings", default="~/.claude/settings.json")
    permission.add_argument("--command", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the requested settings operation."""
    args = build_parser().parse_args(argv)
    try:
        if args.command == "sync":
            return asyncio.run(synchronize_models(args))
        return update_permission(args)
    except (ClaudeSettingsError, AuthConfigurationError, ModelDiscoveryError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
