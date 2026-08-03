#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Configure and verify the Kiro Gateway port across local clients."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from kiro.port_config import (  # noqa: E402
    PortConfigurationError,
    check_port_alignment,
    configure_gateway_port,
    existing_setup_is_complete,
    resolve_gateway_port,
    validate_port,
)


def _print_alignment(result: object) -> None:
    """Print one human-readable alignment report."""
    print(f"Gateway runtime:  {result.expected_base_url}")
    print(f"Claude Code:      {result.claude_base_url or '<not configured>'}")
    print(f"~/.zshrc helper:  {result.helper_status}")
    print(f"Running gateway:  {result.live_status}")


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(_REPO_ROOT / ".env"))
    parser.add_argument("--settings", default="~/.claude/settings.json")
    parser.add_argument("--zshrc", default="~/.zshrc")
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve = subparsers.add_parser("resolve", help="Print the persisted or default port")
    resolve.add_argument("--port", help="Optional explicit port to validate and print")

    configure = subparsers.add_parser(
        "set", help="Persist a port in .env and Claude Code settings"
    )
    configure.add_argument("port")

    subparsers.add_parser(
        "ready", help="Check whether a focused existing-install update is safe"
    )
    subparsers.add_parser(
        "check", help="Verify runtime, Claude Code, and the optional zsh helper"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run a port configuration operation."""
    args = build_parser().parse_args(argv)
    env_path = Path(args.env_file).expanduser()
    settings_path = Path(args.settings).expanduser()
    zshrc_path = Path(args.zshrc).expanduser()

    try:
        if args.command == "resolve":
            port = validate_port(args.port) if args.port is not None else resolve_gateway_port(env_path)
            print(port)
            return 0

        if args.command == "ready":
            return 0 if existing_setup_is_complete(env_path, settings_path) else 1

        if args.command == "set":
            port = configure_gateway_port(env_path, settings_path, args.port)
            print(f"Configured gateway and Claude Code to use port {port}.")
            return 0

        result = check_port_alignment(env_path, settings_path, zshrc_path)
        _print_alignment(result)
        if result.aligned:
            print("Port configuration is aligned.")
            return 0

        sys.stdout.flush()
        print("Port configuration is not aligned:", file=sys.stderr)
        for problem in result.problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            f"Fix: run ./setup.sh --port {result.port}, then ./setup.sh --check-port",
            file=sys.stderr,
        )
        return 1
    except (PortConfigurationError, RuntimeError, OSError, UnicodeDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
