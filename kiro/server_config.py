# -*- coding: utf-8 -*-

"""Shared server defaults and validation without loading environment state."""

import re


DEFAULT_SERVER_HOST: str = "0.0.0.0"
DEFAULT_SERVER_PORT: int = 4567


class PortConfigurationError(ValueError):
    """Raised when a server port is not a valid TCP port."""


def validate_port(value: object) -> int:
    """Validate and normalize a TCP port.

    Args:
        value: Candidate integer or decimal string.

    Returns:
        Valid port in the inclusive range 1..65535.

    Raises:
        PortConfigurationError: If the value is not a valid TCP port.
    """
    text = str(value)
    if not re.fullmatch(r"[0-9]+", text):
        raise PortConfigurationError(
            f"Invalid port '{text}': use a decimal number from 1 to 65535"
        )
    port = int(text)
    if not 1 <= port <= 65535:
        raise PortConfigurationError(
            f"Invalid port '{text}': use a decimal number from 1 to 65535"
        )
    return port
