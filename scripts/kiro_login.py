#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Authenticate Kiro Gateway directly with AWS IAM Identity Center."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import webbrowser
from pathlib import Path
from typing import Callable, Optional, Sequence

import httpx

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from kiro.idc_bootstrap import (  # noqa: E402
    DeviceAuthorizationError,
    IdcBootstrapError,
    SsoOidcDeviceClient,
)
from kiro.idc_login import (  # noqa: E402
    IdcLoginEvent,
    IdcLoginResult,
    build_event_sink as build_shared_event_sink,
    emit_agent_event,
    run_idc_login,
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
        help=(
            "Print and flush the verification code and URL without opening a "
            "browser; approve only when the browser code matches exactly"
        ),
    )
    parser.add_argument(
        "--agent-events",
        action="store_true",
        help="Emit only allowlisted KIRO_EVENT JSON lines on stdout",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing credential output file",
    )
    return parser


def emit_agent_event(event: IdcLoginEvent, *, scope: str = "login") -> None:
    """Emit one allowlisted machine-readable event and flush it immediately."""
    payload = {"event": "KIRO_EVENT", "scope": scope, **event.payload()}
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True), flush=True)


def _safe_failure_category(exc: BaseException) -> str:
    """Map failures to stable categories without exposing provider content."""
    if isinstance(exc, DeviceAuthorizationError):
        message = str(exc).lower()
        if "denied" in message:
            return "denied"
        if "expired" in message or "timed out" in message:
            return "expired"
        return "authorization"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.TransportError):
        return "network"
    if isinstance(exc, IdcBootstrapError):
        return "configuration"
    if isinstance(exc, OSError):
        return "filesystem"
    return "unexpected"


def _browser_opener(
    event: IdcLoginEvent,
    *,
    no_browser: bool,
    agent_events: bool,
) -> None:
    """Render authorization instructions and optionally open the browser."""
    if event.name == "authorization_required":
        if agent_events:
            emit_agent_event(event)
        else:
            browser_instruction = (
                "Open the URL above in your browser."
                if no_browser
                else "The browser will open after these instructions are visible."
            )
            print(
                "\nAWS IAM Identity Center authorization required:\n"
                f"  Code: {event.code}\n"
                f"  URL:  {event.url}\n\n"
                "Approve only if the browser code matches the Code above exactly.\n"
                "If it differs, is missing, or the request is unexpected, cancel and rerun setup.\n"
                f"{browser_instruction}\n"
                f"The code expires in {event.expires_in} seconds. "
                "Waiting for approval; press Ctrl+C to cancel.\n",
                flush=True,
            )

        if no_browser:
            return
        try:
            opened = webbrowser.open(event.url or "")
        except (webbrowser.Error, OSError):
            opened = False
        if not opened and not agent_events:
            print(
                "The browser could not be opened automatically; open the URL above "
                "and confirm the exact Code shown.",
                flush=True,
            )
        return

    if event.name == "waiting" and agent_events:
        emit_agent_event(event)


def build_event_sink(args: argparse.Namespace) -> Callable[[IdcLoginEvent], None]:
    """Build the renderer/browser callback for one login invocation."""
    return lambda event: _browser_opener(
        event,
        no_browser=getattr(args, "no_browser", False),
        agent_events=getattr(args, "agent_events", False),
    )


async def login(
    args: argparse.Namespace,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Path:
    """Run device login, profile discovery, and credential persistence."""
    result: IdcLoginResult = await run_idc_login(
        aws_profile=args.aws_profile,
        aws_config=args.aws_config,
        q_profile=args.q_profile,
        output=args.output,
        force=getattr(args, "force", False),
        event_sink=build_event_sink(args),
        client=client,
    )
    if not getattr(args, "agent_events", False):
        print(f"Authenticated as Q Developer profile: {result.profile.profile_name}")
        print(f"API region: {result.profile.region}")
        print(f"Credentials written to: {result.output}")
        print(f'Configure KIRO_CREDS_FILE="{result.output}"')
    return result.output


async def async_main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the asynchronous command entry point."""
    args = build_parser().parse_args(argv)
    try:
        await login(args)
    except (IdcBootstrapError, httpx.HTTPError, OSError) as exc:
        category = _safe_failure_category(exc)
        if args.agent_events:
            terminal_type = category if category in {"denied", "expired"} else "failed"
            emit_agent_event(IdcLoginEvent(terminal_type, category=category))
        else:
            print(f"Login failed: {exc}", file=sys.stderr)
        return 1
    if args.agent_events:
        emit_agent_event(IdcLoginEvent("succeeded"))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the command and return a process exit code."""
    agent_events = bool(argv and "--agent-events" in argv) or (
        argv is None and "--agent-events" in sys.argv[1:]
    )
    try:
        return asyncio.run(async_main(argv))
    except KeyboardInterrupt:
        if agent_events:
            emit_agent_event(IdcLoginEvent("cancelled", category="cancelled"))
        else:
            print("Login cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
