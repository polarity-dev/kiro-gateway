# -*- coding: utf-8 -*-

"""Dotenv helpers for values that must preserve literal path characters."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


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

    pattern = re.compile(rf'^{re.escape(variable)}=(["\']?)(.*?)\1\s*$')
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = pattern.match(line)
        if match:
            return match.group(2)
    return None
