# -*- coding: utf-8 -*-

# Kiro Gateway
# https://github.com/jwadow/kiro-gateway
# Copyright (C) 2025 Jwadow
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""
Upstream stop metadata from Kiro's ``metadataEvent``.

Kiro ends every response with a metadata event such as::

    {"stopReason": "END_TURN"}
    {"stopDetails": {"refusal": {"category": "REASONING_EXTRACTION",
                                 "explanation": "..."}},
     "stopReason": "CONTENT_FILTERED"}

Without this module a ``CONTENT_FILTERED`` stop was invisible: clients got an
empty ``end_turn`` answer and no hint about why. This module turns the raw
event into an :class:`UpstreamStop`, maps it to each API's native stop field
(Anthropic ``stop_reason="refusal"``, OpenAI ``finish_reason="content_filter"``)
and builds the user-facing notice shown in place of the missing answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger


# Kiro stop reasons that mean "the response was blocked", not "model finished".
REFUSAL_STOP_REASONS: frozenset[str] = frozenset({"CONTENT_FILTERED"})

# Stop reasons that need no special handling (normal completion).
NORMAL_STOP_REASONS: frozenset[str] = frozenset({"END_TURN"})

# Refusal category caused by the gateway's own fake reasoning injection.
REASONING_EXTRACTION_CATEGORY = "REASONING_EXTRACTION"

ANTHROPIC_REFUSAL_STOP_REASON = "refusal"
OPENAI_REFUSAL_FINISH_REASON = "content_filter"

DEFAULT_REFUSAL_EXPLANATION = (
    "The model refused to continue this conversation. Select a different model, "
    "start a new conversation, or rewind to an earlier point and try a different approach."
)


@dataclass(frozen=True)
class UpstreamStop:
    """
    Stop metadata reported by Kiro at the end of a response.

    Attributes:
        stop_reason: Raw Kiro stop reason (e.g. ``END_TURN``, ``CONTENT_FILTERED``)
        refusal_category: Refusal category when the response was filtered
        refusal_explanation: Kiro's human-readable explanation of the refusal
    """

    stop_reason: Optional[str] = None
    refusal_category: Optional[str] = None
    refusal_explanation: Optional[str] = None

    @property
    def is_refusal(self) -> bool:
        """Whether Kiro blocked the response instead of completing it."""
        return self.stop_reason in REFUSAL_STOP_REASONS or self.refusal_category is not None


def parse_upstream_stop(data: Dict[str, Any]) -> UpstreamStop:
    """
    Build an UpstreamStop from a raw Kiro metadata event.

    Malformed fields are ignored rather than raised: a broken metadata event
    must never break an otherwise valid response.

    Args:
        data: Parsed JSON of the metadata event

    Returns:
        UpstreamStop describing why the response ended
    """
    raw_reason = data.get("stopReason")
    stop_reason = raw_reason if isinstance(raw_reason, str) and raw_reason else None

    refusal_category: Optional[str] = None
    refusal_explanation: Optional[str] = None
    details = data.get("stopDetails")
    refusal = details.get("refusal") if isinstance(details, dict) else None
    if isinstance(refusal, dict):
        category = refusal.get("category")
        explanation = refusal.get("explanation")
        refusal_category = category if isinstance(category, str) and category else "UNKNOWN"
        if isinstance(explanation, str) and explanation.strip():
            refusal_explanation = explanation.strip()

    stop = UpstreamStop(
        stop_reason=stop_reason,
        refusal_category=refusal_category,
        refusal_explanation=refusal_explanation,
    )

    if stop.is_refusal:
        hint = ""
        if stop.refusal_category == REASONING_EXTRACTION_CATEGORY:
            hint = (
                " The injected fake reasoning instruction is a known trigger: "
                "remove FAKE_REASONING=true from .env (default is off) and restart the gateway."
            )
        logger.warning(
            f"Kiro refused the response: stopReason={stop.stop_reason}, "
            f"category={stop.refusal_category}.{hint}"
        )
    elif stop.stop_reason and stop.stop_reason not in NORMAL_STOP_REASONS:
        logger.debug(f"Kiro reported non-standard stopReason: {stop.stop_reason}")

    return stop


def build_refusal_notice(stop: UpstreamStop) -> str:
    """
    Build the text shown to the user when Kiro refused the response.

    Args:
        stop: Refusal stop metadata

    Returns:
        Actionable notice including Kiro's own explanation
    """
    explanation = stop.refusal_explanation or DEFAULT_REFUSAL_EXPLANATION
    category = stop.refusal_category or "UNKNOWN"
    reason = stop.stop_reason or "CONTENT_FILTERED"
    return f"[Kiro Gateway] Kiro blocked this response ({reason}, category {category}): {explanation}"
