# -*- coding: utf-8 -*-

"""Gateway port configuration and cross-client alignment checks."""

from __future__ import annotations

import json
import os
import re
import select
import shutil
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from kiro.atomic_io import AtomicWriteError, write_text_atomic
from kiro.dotenv_utils import find_raw_dotenv_values
from kiro.server_config import (
    DEFAULT_SERVER_PORT,
    PortConfigurationError,
    validate_port,
)


_ASSIGNMENT_TEMPLATE = r"^\s*(?:export\s+)?{variable}\s*="
_GATEWAY_HEADER = b"x-kiro-gateway: true"
_MAX_HEALTH_RESPONSE = 16384
_HEALTH_TIMEOUT_SECONDS = 0.75


@dataclass(frozen=True)
class PortAlignment:
    """Result of comparing runtime, Claude Code, and shell helper ports."""

    port: int
    expected_base_url: str
    claude_base_url: Optional[str]
    helper_status: str
    live_status: str
    problems: tuple[str, ...]

    @property
    def aligned(self) -> bool:
        """Return whether every configured consumer follows the runtime port."""
        return not self.problems


def _assignment_indexes(text: str, variable: str) -> list[int]:
    """Return line indexes assigning one dotenv variable."""
    pattern = re.compile(_ASSIGNMENT_TEMPLATE.format(variable=re.escape(variable)))
    return [
        index
        for index, line in enumerate(text.splitlines())
        if pattern.match(line)
    ]


def _single_dotenv_value(text: str, variable: str) -> Optional[str]:
    """Read one unambiguous dotenv value from already-loaded text."""
    values = find_raw_dotenv_values(text, variable)
    if len(values) > 1:
        raise PortConfigurationError(
            f"The gateway .env contains multiple {variable} entries; keep one and retry"
        )
    return values[0] if values else None


def resolve_gateway_port(env_path: Path) -> int:
    """Resolve the persisted gateway port, falling back to the project default.

    Args:
        env_path: Gateway dotenv file.

    Returns:
        Persisted port or ``DEFAULT_SERVER_PORT`` when ``SERVER_PORT`` is absent.

    Raises:
        PortConfigurationError: If a persisted port is invalid or duplicated.
        OSError: If an existing dotenv file cannot be read.
    """
    if not env_path.exists():
        return DEFAULT_SERVER_PORT
    text = env_path.read_text(encoding="utf-8")
    value = _single_dotenv_value(text, "SERVER_PORT")
    return DEFAULT_SERVER_PORT if value is None else validate_port(value)


def existing_setup_is_complete(env_path: Path, settings_path: Path) -> bool:
    """Return whether a focused port-only update is safe.

    Args:
        env_path: Gateway dotenv file.
        settings_path: Claude Code settings path.

    Returns:
        ``True`` only when both the proxy credential and managed Claude
        connection already exist.

    Raises:
        PortConfigurationError: If existing configuration is malformed.
    """
    if not env_path.exists() or not settings_path.exists():
        return False
    proxy_key = _single_dotenv_value(
        env_path.read_text(encoding="utf-8"), "PROXY_API_KEY"
    )
    if not proxy_key:
        return False
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortConfigurationError(
            f"Cannot read Claude settings from {settings_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise PortConfigurationError(
            f"Claude settings in {settings_path} must be a JSON object"
        )
    env = payload.get("env")
    if env is None:
        return False
    if not isinstance(env, dict):
        raise PortConfigurationError("Claude setting 'env' must be a JSON object")
    return bool(env.get("ANTHROPIC_AUTH_TOKEN") and env.get("ANTHROPIC_BASE_URL"))


def _render_dotenv_port(current: str, port: int) -> str:
    """Return dotenv content with exactly one normalized ``SERVER_PORT`` entry."""
    lines = current.splitlines()
    matches = _assignment_indexes(current, "SERVER_PORT")
    if len(matches) > 1:
        raise PortConfigurationError(
            "The gateway .env contains multiple SERVER_PORT entries; keep one and retry"
        )
    entry = f'SERVER_PORT="{port}"'
    if matches:
        lines[matches[0]] = entry
    else:
        lines.extend(["", "# Local gateway port shared by the server and Claude Code", entry])
    return "\n".join(lines) + "\n"


def configure_gateway_port(env_path: Path, settings_path: Path, value: object) -> int:
    """Persist one port in the gateway dotenv and Claude Code settings.

    Both files are validated before either is changed. The dotenv is backed up
    only when its content changes and is rolled back if the settings write fails.

    Args:
        env_path: Existing gateway dotenv file.
        settings_path: Claude Code settings path.
        value: Requested TCP port.

    Returns:
        Configured port.

    Raises:
        PortConfigurationError: If configuration is missing or invalid.
        ClaudeSettingsError: If Claude settings cannot be safely updated.
    """
    port = validate_port(value)
    if not env_path.exists():
        raise PortConfigurationError(
            f"{env_path} does not exist; run the full setup before changing its port"
        )

    from kiro.claude_settings import (
        ClaudeSettingsError,
        load_claude_settings,
        merge_gateway_base_url,
        write_claude_settings_atomic,
    )

    current_env = env_path.read_text(encoding="utf-8")
    updated_env = _render_dotenv_port(current_env, port)
    settings = load_claude_settings(settings_path)
    existing_env = settings.get("env", {})
    if not isinstance(existing_env, dict):
        raise ClaudeSettingsError("Claude setting 'env' must be a JSON object")
    if not existing_env.get("ANTHROPIC_AUTH_TOKEN") or not existing_env.get(
        "ANTHROPIC_BASE_URL"
    ):
        raise PortConfigurationError(
            "Claude Code is not configured for this gateway; run the full setup "
            "before using the focused port update"
        )
    updated_settings = merge_gateway_base_url(
        settings,
        base_url=f"http://localhost:{port}",
    )

    env_changed = updated_env != current_env
    if env_changed:
        try:
            shutil.copy2(env_path, env_path.with_name(f"{env_path.name}.bak"))
            write_text_atomic(env_path, updated_env)
        except (OSError, AtomicWriteError) as exc:
            raise PortConfigurationError(
                f"Cannot safely update {env_path}: {exc}"
            ) from exc
    try:
        write_claude_settings_atomic(settings_path, updated_settings)
    except ClaudeSettingsError:
        if env_changed:
            try:
                write_text_atomic(env_path, current_env)
            except AtomicWriteError as rollback_exc:
                raise PortConfigurationError(
                    f"Claude settings failed and {env_path} could not be restored: "
                    f"{rollback_exc}"
                ) from rollback_exc
        raise
    return port


def _extract_helper(zshrc_path: Path) -> Optional[str]:
    """Extract the effective ``kiro-gateway`` function body from a zshrc file."""
    if not zshrc_path.exists():
        return None
    text = zshrc_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(?ms)^\s*(?:(?:function\s+)?kiro-gateway\s*(?:\(\s*\))?)\s*\{"
        r"(.*?)^\s*\}"
    )
    matches = list(pattern.finditer(text))
    return None if not matches else matches[-1].group(1)


def _check_helper(zshrc_path: Path, port: int) -> tuple[str, Optional[str]]:
    """Classify whether an optional zsh helper follows the runtime dotenv."""
    body = _extract_helper(zshrc_path)
    if body is None:
        return "not installed (optional)", None
    hardcoded = re.search(r"--port(?:=|\s+)[\"']?([0-9]+)", body)
    inline_env = re.search(r"(?:^|\s)SERVER_PORT=[\"']?([0-9]+)", body)
    if hardcoded or inline_env:
        helper_port = validate_port((hardcoded or inline_env).group(1))
        return (
            f"hard-coded to {helper_port}",
            f"The kiro-gateway helper in {zshrc_path} hard-codes port {helper_port}; "
            "remove --port and SERVER_PORT assignments so python3 main.py always "
            "follows SERVER_PORT from .env",
        )
    if "--port" in body or "SERVER_PORT=" in body:
        return (
            "uses an unverifiable port override",
            f"The kiro-gateway helper in {zshrc_path} overrides the port dynamically; "
            "remove the override so python3 main.py reads SERVER_PORT from .env",
        )
    if re.search(r"python3\s+main\.py(?:\s|$|\))", body):
        return "follows SERVER_PORT from .env", None
    return (
        "does not start python3 main.py",
        f"The kiro-gateway helper in {zshrc_path} cannot be verified; replace it "
        "with the helper printed by setup.sh",
    )


def _read_health_response(port: int) -> tuple[bytes, bytes]:
    """Read a bounded HTTP response under one wall-clock deadline."""
    deadline = time.monotonic() + _HEALTH_TIMEOUT_SECONDS
    with socket.create_connection(("localhost", port), timeout=_HEALTH_TIMEOUT_SECONDS) as sock:
        sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        sock.setblocking(False)
        response = bytearray()
        while len(response) <= _MAX_HEALTH_RESPONSE:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("health probe deadline exceeded")
            readable, _, _ = select.select([sock], [], [], remaining)
            if not readable:
                raise TimeoutError("health probe deadline exceeded")
            chunk = sock.recv(min(4096, _MAX_HEALTH_RESPONSE + 1 - len(response)))
            if not chunk:
                break
            response.extend(chunk)
        if len(response) > _MAX_HEALTH_RESPONSE:
            raise ValueError("health response is too large")
        headers, separator, body = bytes(response).partition(b"\r\n\r\n")
        if not separator:
            raise ValueError("health response has no HTTP header boundary")
        return headers, body


def _check_live_gateway(port: int) -> tuple[str, Optional[str]]:
    """Probe the configured local port without requiring the gateway to run."""
    try:
        headers, body = _read_health_response(port)
    except (ConnectionError, OSError, TimeoutError):
        return "not running (restart required after a change)", None
    except ValueError as exc:
        return (
            f"unexpected service on localhost:{port}",
            f"Port {port} returned an invalid health response: {exc}",
        )

    lines = headers.lower().split(b"\r\n")
    identified_by_header = _GATEWAY_HEADER in lines[1:]
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = None
    identified_by_body = (
        isinstance(payload, dict)
        and payload.get("message") == "Kiro Gateway is running"
    )
    if lines and b" 200 " in lines[0] and (identified_by_header or identified_by_body):
        return f"responding on localhost:{port}", None
    return (
        f"unexpected service on localhost:{port}",
        f"Port {port} is occupied, but the root endpoint did not identify Kiro Gateway",
    )


def check_port_alignment(
    env_path: Path,
    settings_path: Path,
    zshrc_path: Path,
) -> PortAlignment:
    """Compare the persisted runtime port with Claude Code and the zsh helper."""
    from kiro.claude_settings import ClaudeSettingsError, load_claude_settings

    port = resolve_gateway_port(env_path)
    expected = f"http://localhost:{port}"
    settings = load_claude_settings(settings_path)
    env = settings.get("env", {})
    if not isinstance(env, dict):
        raise ClaudeSettingsError("Claude setting 'env' must be a JSON object")
    actual = env.get("ANTHROPIC_BASE_URL")

    problems: list[str] = []
    inherited_port = os.environ.get("SERVER_PORT")
    if inherited_port is not None:
        shell_port = validate_port(inherited_port)
        if shell_port != port:
            problems.append(
                f"The current shell exports SERVER_PORT={shell_port}, overriding "
                f"the persisted .env port {port}; run 'unset SERVER_PORT'"
            )
    if actual != expected:
        shown = actual if isinstance(actual, str) and actual else "<not configured>"
        problems.append(
            f"Claude Code uses {shown}, but the persisted gateway uses {expected}"
        )
    helper_status, helper_problem = _check_helper(zshrc_path, port)
    if helper_problem:
        problems.append(helper_problem)
    live_status, live_problem = _check_live_gateway(port)
    if live_problem:
        problems.append(live_problem)
    return PortAlignment(
        port=port,
        expected_base_url=expected,
        claude_base_url=actual if isinstance(actual, str) else None,
        helper_status=helper_status,
        live_status=live_status,
        problems=tuple(problems),
    )
