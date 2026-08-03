#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Authenticate Kiro Gateway directly with AWS IAM Identity Center."""

from __future__ import annotations

import argparse
import asyncio
import sys
import webbrowser
from pathlib import Path
from typing import Optional, Sequence

import httpx

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from kiro.idc_bootstrap import (  # noqa: E402
    IdcBootstrapError,
    SsoOidcDeviceClient,
    build_credentials,
    list_available_profiles,
    load_aws_sso_profile,
    select_profile,
    write_credentials,
)


DEFAULT_OUTPUT = Path("~/.aws/sso/cache/kiro-gateway-auth.json")


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Log in through AWS IAM Identity Center, discover the assigned "
            "Amazon Q Developer profile, and write Kiro Gateway credentials."
        )
    )
    parser.add_argument(
        "--aws-profile",
        default="default",
        help="AWS CLI profile containing sso_session or inline SSO settings (default: default)",
    )
    parser.add_argument(
        "--aws-config",
        type=Path,
        default=Path("~/.aws/config"),
        help="AWS shared config file (default: ~/.aws/config)",
    )
    parser.add_argument(
        "--q-profile",
        help="Q Developer profile name or ARN; required only when multiple profiles are assigned",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Credential JSON output (default: ~/.aws/sso/cache/kiro-gateway-auth.json)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the verification URL without opening a browser",
    )
    return parser


async def login(
    args: argparse.Namespace,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Path:
    """Run device login, profile discovery, and credential persistence.

    Args:
        args: Parsed CLI arguments.
        client: Optional injected HTTP client for tests.

    Returns:
        Expanded output credential path.

    Raises:
        IdcBootstrapError: If configuration, login, discovery, or persistence fails.
        httpx.HTTPError: If an unexpected transport failure occurs.
    """
    sso_profile = load_aws_sso_profile(args.aws_profile, args.aws_config)
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    try:
        oidc = SsoOidcDeviceClient(sso_profile.region, http_client)
        registration = await oidc.register_client()
        authorization = await oidc.start_device_authorization(
            registration, sso_profile.start_url
        )

        print("\nApprove Kiro Gateway in your browser:")
        print(f"  Code: {authorization.user_code}")
        print(f"  URL:  {authorization.verification_uri_complete}\n")
        if not args.no_browser:
            opened = webbrowser.open(authorization.verification_uri_complete)
            if not opened:
                print("The browser could not be opened automatically; use the URL above.")

        token = await oidc.poll_for_token(registration, authorization)
        profiles = await list_available_profiles(token.access_token, http_client)
        q_profile = select_profile(profiles, args.q_profile)
        credentials = build_credentials(
            sso_profile, registration, token, q_profile
        )
        output = args.output.expanduser()
        write_credentials(output, credentials)
        print(f"Authenticated as Q Developer profile: {q_profile.profile_name}")
        print(f"API region: {q_profile.region}")
        print(f"Credentials written to: {output}")
        print(f'Configure KIRO_CREDS_FILE="{output}"')
        return output
    finally:
        if owns_client:
            await http_client.aclose()


async def async_main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the asynchronous command entry point."""
    args = build_parser().parse_args(argv)
    try:
        await login(args)
    except (IdcBootstrapError, httpx.HTTPError, OSError) as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the command and return a process exit code."""
    try:
        return asyncio.run(async_main(argv))
    except KeyboardInterrupt:
        print("Login cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
