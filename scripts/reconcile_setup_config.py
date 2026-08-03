#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Reconcile setup-owned dotenv, account, and preferred-account state."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from kiro.account_config import (  # noqa: E402
    AccountConfigError,
    reconcile_selected_json_account,
    set_preferred_account,
    write_setup_dotenv,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the setup reconciliation parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--accounts", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--credential", required=True, type=Path)
    parser.add_argument("--api-region", required=True)
    parser.add_argument("--profile-arn", required=True)
    parser.add_argument("--port", required=True)
    parser.add_argument("--multi-account", action="store_true")
    parser.add_argument(
        "--proxy-key-env",
        default="KIRO_GATEWAY_SETUP_TOKEN",
        help="Environment variable containing the proxy key",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate and atomically reconcile each setup configuration file."""
    args = build_parser().parse_args(argv)
    proxy_key = os.getenv(args.proxy_key_env)
    if not proxy_key:
        print(f"Error: {args.proxy_key_env} is empty", file=sys.stderr)
        return 2
    try:
        # Validate every JSON input before the first write so malformed state
        # cannot leave only part of the setup reconciled.
        for path, expected in ((args.accounts, list), (args.state, dict)):
            if path.exists():
                parsed = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(parsed, expected):
                    raise AccountConfigError(
                        f"{path} must contain a JSON {expected.__name__}"
                    )
        current_env = (
            args.env_file.read_text(encoding="utf-8")
            if args.env_file.exists()
            else ""
        )
        configured_multi_account = os.getenv("ACCOUNT_SYSTEM", "").lower() in {
            "true", "1", "yes"
        } or any(
            line.strip().lower() in {
                "account_system=true", "account_system=1", "account_system=yes"
            }
            for line in current_env.splitlines()
        )
        account_id = reconcile_selected_json_account(
            args.accounts,
            args.credential,
            multi_account=args.multi_account or configured_multi_account,
            profile_arn=args.profile_arn,
            api_region=args.api_region,
        )
        set_preferred_account(args.state, account_id)
        write_setup_dotenv(
            args.env_file,
            {
                "PROXY_API_KEY": proxy_key,
                "KIRO_CREDS_FILE": str(args.credential.expanduser().resolve()),
                "KIRO_API_REGION": args.api_region,
                "PROFILE_ARN": args.profile_arn,
                "SERVER_PORT": str(args.port),
                "DEBUG_MODE": "off",
            },
        )
    except AccountConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
