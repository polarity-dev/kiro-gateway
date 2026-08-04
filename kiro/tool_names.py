# -*- coding: utf-8 -*-

# Kiro Gateway
# https://github.com/jwadow/kiro-gateway
# Copyright (C) 2025 Jwadow
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Request-scoped aliases for tool names that exceed Kiro API limits."""

import hashlib
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Mapping


KIRO_TOOL_NAME_MAX_LENGTH = 64
_ALIAS_PREFIX = "kgw_"
_ALIAS_DIGEST_LENGTH = 32
_ALIAS_SEPARATOR = "_"
_ALIAS_STEM_LENGTH = (
    KIRO_TOOL_NAME_MAX_LENGTH
    - len(_ALIAS_PREFIX)
    - len(_ALIAS_SEPARATOR)
    - _ALIAS_DIGEST_LENGTH
)
_NON_WORD_CHARACTERS = re.compile(r"[^A-Za-z0-9_]+")
_REPEATED_UNDERSCORES = re.compile(r"_+")


def _tool_name_digest(name: str) -> str:
    """Return a stable digest for an exact client-facing tool name.

    Args:
        name: Original tool name.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


def _tool_name_stem(name: str) -> str:
    """Build a readable ASCII stem without affecting alias identity.

    Args:
        name: Original tool name.

    Returns:
        ASCII word-character stem, or ``tool`` when none remains.
    """
    stem = _NON_WORD_CHARACTERS.sub("_", name)
    stem = _REPEATED_UNDERSCORES.sub("_", stem).strip("_")
    return stem or "tool"


def _base_alias(name: str) -> str:
    """Build the unsuffixed Kiro alias for a long tool name.

    Args:
        name: Original tool name.

    Returns:
        Alias no longer than the Kiro tool-name limit.
    """
    stem = _tool_name_stem(name)[:_ALIAS_STEM_LENGTH]
    digest = _tool_name_digest(name)[:_ALIAS_DIGEST_LENGTH]
    return f"{_ALIAS_PREFIX}{stem}{_ALIAS_SEPARATOR}{digest}"


@dataclass(frozen=True)
class ToolNameMapping:
    """Bidirectional, immutable tool-name mapping for one request."""

    original_to_kiro: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    kiro_to_original: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        """Freeze defensive copies of both directions."""
        object.__setattr__(
            self,
            "original_to_kiro",
            MappingProxyType(dict(self.original_to_kiro)),
        )
        object.__setattr__(
            self,
            "kiro_to_original",
            MappingProxyType(dict(self.kiro_to_original)),
        )

    def to_kiro(self, name: str) -> str:
        """Translate a client-facing name, preserving unknown names."""
        return self.original_to_kiro.get(name, name)

    def to_original(self, name: str) -> str:
        """Translate a Kiro-facing name, preserving unknown names."""
        return self.kiro_to_original.get(name, name)

    def restore_text(self, text: str) -> str:
        """Restore complete aliases embedded in a text value in one pass."""
        if not self.kiro_to_original:
            return text
        aliases = sorted(self.kiro_to_original, key=len, reverse=True)
        pattern = re.compile("|".join(re.escape(alias) for alias in aliases))
        return pattern.sub(lambda match: self.kiro_to_original[match.group(0)], text)


class ToolNameTextRestorer:
    """Restore aliases in streamed text, including aliases split across chunks."""

    def __init__(self, mapping: ToolNameMapping) -> None:
        """Initialize a request-scoped text restorer.

        Args:
            mapping: Tool aliases active for the request.
        """
        self._mapping = mapping
        self._aliases = tuple(mapping.kiro_to_original)
        self._pending = ""

    def feed(self, text: str) -> str:
        """Consume one chunk and return text safe to emit immediately."""
        if not self._aliases:
            return text

        combined = self._pending + text
        pending_length = 0
        for alias in self._aliases:
            max_prefix = min(len(alias) - 1, len(combined))
            for prefix_length in range(max_prefix, 0, -1):
                if combined.endswith(alias[:prefix_length]):
                    pending_length = max(pending_length, prefix_length)
                    break

        if pending_length:
            emitted = combined[:-pending_length]
            self._pending = combined[-pending_length:]
        else:
            emitted = combined
            self._pending = ""
        return self._mapping.restore_text(emitted)

    def flush(self) -> str:
        """Return any buffered suffix at end of stream."""
        emitted = self._mapping.restore_text(self._pending)
        self._pending = ""
        return emitted


EMPTY_TOOL_NAME_MAPPING = ToolNameMapping()


def build_tool_name_mapping(names: Iterable[str]) -> ToolNameMapping:
    """Build deterministic aliases for tool names longer than 64 characters.

    Short names reserve their exact spelling before aliases are allocated.
    Long names are sorted so request ordering cannot affect collision suffixes.

    Args:
        names: Client-facing tool names used by the request.

    Returns:
        Immutable bidirectional mapping. Identity entries are omitted.
    """
    unique_names = set(names)
    long_names = sorted(
        name for name in unique_names if len(name) > KIRO_TOOL_NAME_MAX_LENGTH
    )
    if not long_names:
        return EMPTY_TOOL_NAME_MAPPING

    occupied = {
        name for name in unique_names if len(name) <= KIRO_TOOL_NAME_MAX_LENGTH
    }
    original_to_kiro: dict[str, str] = {}
    kiro_to_original: dict[str, str] = {}

    for original_name in long_names:
        base_alias = _base_alias(original_name)
        alias = base_alias
        suffix_index = 0
        while alias in occupied:
            suffix_index += 1
            suffix = f"_{suffix_index}"
            alias = f"{base_alias[:KIRO_TOOL_NAME_MAX_LENGTH - len(suffix)]}{suffix}"

        occupied.add(alias)
        original_to_kiro[original_name] = alias
        kiro_to_original[alias] = original_name

    return ToolNameMapping(
        original_to_kiro=MappingProxyType(original_to_kiro),
        kiro_to_original=MappingProxyType(kiro_to_original),
    )
