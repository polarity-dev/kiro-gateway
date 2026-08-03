# -*- coding: utf-8 -*-

"""Dotenv helpers for values that must preserve literal path characters."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


def find_raw_dotenv_values(text: str, variable: str) -> list[str]:
    """Find literal values for one variable in dotenv text.

    Args:
        text: Complete dotenv content.
        variable: Environment variable name to find.

    Returns:
        Values in declaration order, preserving literal backslashes and removing
        matching quotes and unquoted inline comments.
    """
    assignment = re.compile(
        rf"^(?:export\s+)?{re.escape(variable)}\s*=\s*(.*)$"
    )
    quoted = re.compile(r'^(?P<quote>["\'])(?P<value>.*?)\1(?:\s+#.*)?$')
    values: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = assignment.match(line)
        if match is None:
            continue
        raw_value = match.group(1).strip()
        quoted_match = quoted.match(raw_value)
        if quoted_match is not None:
            values.append(quoted_match.group("value"))
        else:
            values.append(re.sub(r"\s+#.*$", "", raw_value).strip())
    return values


def read_raw_dotenv_value(path: Path, variable: str) -> Optional[str]:
    """Read one dotenv value without decoding backslash escape sequences.

    Args:
        path: Explicit dotenv file to inspect.
        variable: Environment variable name to find.

    Returns:
        Literal value with matching single or double quotes removed, or ``None``
        when the file or variable is absent.

    Raises:
        OSError: If an existing dotenv file cannot be read.
        UnicodeDecodeError: If the file is not valid UTF-8.
    """
    if not path.exists():
        return None

    values = find_raw_dotenv_values(path.read_text(encoding="utf-8"), variable)
    return values[0] if values else None
