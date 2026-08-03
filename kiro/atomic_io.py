# -*- coding: utf-8 -*-

"""Symlink-safe atomic text file writes."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Optional


class AtomicWriteError(OSError):
    """Raised when a text file cannot be read or replaced atomically."""


def write_text_atomic(
    path: Path,
    content: str,
    default_mode: int = 0o600,
    preserve_mode: bool = True,
) -> bool:
    """Write UTF-8 text atomically while preserving symlinks and permissions.

    Args:
        path: Destination path. Existing symlinks are resolved and preserved.
        content: Complete UTF-8 text to write.
        default_mode: Permissions used when the destination does not exist.
        preserve_mode: Preserve an existing destination mode instead of applying
            ``default_mode`` on replacement.

    Returns:
        ``True`` when content changed, otherwise ``False``.

    Raises:
        AtomicWriteError: If the destination cannot be read or replaced safely.
    """
    try:
        target_path = path.resolve(strict=True) if path.is_symlink() else path
        current = target_path.read_text(encoding="utf-8") if target_path.exists() else None
    except (OSError, UnicodeDecodeError) as exc:
        raise AtomicWriteError(f"Cannot read {path}: {exc}") from exc
    if current == content:
        return False

    temporary_path: Optional[Path] = None
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        mode = (
            stat.S_IMODE(target_path.stat().st_mode)
            if target_path.exists() and preserve_mode
            else default_mode
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target_path.name}.", suffix=".tmp", dir=target_path.parent
        )
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target_path)
        temporary_path = None
        return True
    except OSError as exc:
        raise AtomicWriteError(f"Cannot atomically write {path}: {exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
