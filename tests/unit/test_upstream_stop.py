# -*- coding: utf-8 -*-

"""
Unit tests for kiro.upstream_stop.

Covers parsing of Kiro ``metadataEvent`` stop data, refusal detection, the
actionable log hint for fake-reasoning-triggered refusals, and the notice
text shown to users instead of an empty answer.
"""

import pytest
from loguru import logger

from kiro.upstream_stop import (
    ANTHROPIC_REFUSAL_STOP_REASON,
    DEFAULT_REFUSAL_EXPLANATION,
    OPENAI_REFUSAL_FINISH_REASON,
    REASONING_EXTRACTION_CATEGORY,
    UpstreamStop,
    build_refusal_notice,
    parse_upstream_stop,
)


# Exact shape observed from Kiro for claude-opus-5.5 with fake reasoning injected
REAL_REFUSAL_EVENT = {
    "stopDetails": {
        "refusal": {
            "category": "REASONING_EXTRACTION",
            "explanation": (
                "The selected model cannot continue this conversation. Please select a "
                "different model, or start a new conversation, or rewind the current "
                "conversation to an earlier point and try a different approach."
            ),
        }
    },
    "stopReason": "CONTENT_FILTERED",
}


@pytest.fixture
def captured_logs():
    """Capture loguru messages emitted during a test."""
    messages = []
    handler_id = logger.add(lambda message: messages.append(message.record), level="DEBUG")
    yield messages
    logger.remove(handler_id)


class TestParseUpstreamStopSuccess:
    """Tests for parse_upstream_stop() on well-formed events."""

    def test_parses_normal_end_turn(self):
        """
        What it does: Parses a plain END_TURN metadata event.
        Purpose: Normal completions must not be mistaken for refusals.
        """
        stop = parse_upstream_stop({"stopReason": "END_TURN"})

        assert stop == UpstreamStop(stop_reason="END_TURN")
        assert stop.is_refusal is False

    def test_parses_real_reasoning_extraction_refusal(self):
        """
        What it does: Parses the exact refusal Kiro returned for Opus 5.5.
        Purpose: Regression test for the empty-answer bug.
        """
        stop = parse_upstream_stop(REAL_REFUSAL_EVENT)

        assert stop.stop_reason == "CONTENT_FILTERED"
        assert stop.refusal_category == REASONING_EXTRACTION_CATEGORY
        assert stop.refusal_explanation.startswith("The selected model cannot continue")
        assert stop.is_refusal is True

    def test_explanation_is_stripped(self):
        """
        What it does: Strips surrounding whitespace from the explanation.
        Purpose: Notice text must not contain stray newlines from upstream.
        """
        stop = parse_upstream_stop({
            "stopReason": "CONTENT_FILTERED",
            "stopDetails": {"refusal": {"category": "X", "explanation": "  \n Reason.\n "}},
        })

        assert stop.refusal_explanation == "Reason."


class TestParseUpstreamStopEdgeCases:
    """Tests for parse_upstream_stop() on unusual or malformed events."""

    def test_content_filtered_without_details_is_refusal(self):
        """
        What it does: Treats CONTENT_FILTERED without stopDetails as a refusal.
        Purpose: The stop reason alone must be enough to surface the block.
        """
        stop = parse_upstream_stop({"stopReason": "CONTENT_FILTERED"})

        assert stop.is_refusal is True
        assert stop.refusal_category is None
        assert stop.refusal_explanation is None

    def test_refusal_details_without_stop_reason_is_refusal(self):
        """
        What it does: Treats refusal details without stopReason as a refusal.
        Purpose: Survive Kiro omitting or reordering fields.
        """
        stop = parse_upstream_stop({"stopDetails": {"refusal": {"category": "SAFETY"}}})

        assert stop.stop_reason is None
        assert stop.refusal_category == "SAFETY"
        assert stop.is_refusal is True

    def test_refusal_without_category_uses_unknown(self):
        """
        What it does: Uses UNKNOWN when refusal has no category.
        Purpose: Refusal must stay detectable even with partial details.
        """
        stop = parse_upstream_stop({"stopDetails": {"refusal": {}}})

        assert stop.refusal_category == "UNKNOWN"
        assert stop.is_refusal is True

    @pytest.mark.parametrize("details", [None, "text", 42, [], {"other": 1}, {"refusal": "text"}])
    def test_malformed_stop_details_are_ignored(self, details):
        """
        What it does: Ignores stopDetails that are not the expected object shape.
        Purpose: A malformed metadata event must never raise.
        """
        stop = parse_upstream_stop({"stopReason": "END_TURN", "stopDetails": details})

        assert stop.refusal_category is None
        assert stop.is_refusal is False

    @pytest.mark.parametrize("reason", [None, "", 7, {"x": 1}, ["END_TURN"]])
    def test_non_string_stop_reason_is_ignored(self, reason):
        """
        What it does: Drops stopReason values that are not non-empty strings.
        Purpose: Type confusion in upstream data must not produce false refusals.
        """
        stop = parse_upstream_stop({"stopReason": reason})

        assert stop.stop_reason is None
        assert stop.is_refusal is False

    @pytest.mark.parametrize("explanation", [None, "", "   ", 5, {"text": "x"}])
    def test_unusable_explanation_is_dropped(self, explanation):
        """
        What it does: Drops empty or non-string explanations.
        Purpose: The notice must fall back to a default instead of showing junk.
        """
        stop = parse_upstream_stop({
            "stopReason": "CONTENT_FILTERED",
            "stopDetails": {"refusal": {"category": "X", "explanation": explanation}},
        })

        assert stop.refusal_explanation is None

    def test_unknown_stop_reason_is_not_refusal(self):
        """
        What it does: Treats unknown non-refusal stop reasons as normal.
        Purpose: Only documented blocking reasons change the client stop field.
        """
        stop = parse_upstream_stop({"stopReason": "MAX_TOKENS"})

        assert stop.stop_reason == "MAX_TOKENS"
        assert stop.is_refusal is False

    def test_empty_event(self):
        """
        What it does: Parses an empty dict.
        Purpose: Ensure defensive defaults.
        """
        assert parse_upstream_stop({}) == UpstreamStop()


class TestParseUpstreamStopLogging:
    """Tests for the diagnostics emitted by parse_upstream_stop()."""

    def test_reasoning_extraction_logs_fake_reasoning_hint(self, captured_logs):
        """
        What it does: Logs a warning that points at FAKE_REASONING.
        Purpose: Operators must learn the fix from the log alone.
        """
        parse_upstream_stop(REAL_REFUSAL_EVENT)

        warnings = [r for r in captured_logs if r["level"].name == "WARNING"]
        assert len(warnings) == 1
        assert "REASONING_EXTRACTION" in warnings[0]["message"]
        assert "FAKE_REASONING" in warnings[0]["message"]

    def test_other_refusal_logs_warning_without_hint(self, captured_logs):
        """
        What it does: Logs a warning without the fake reasoning hint.
        Purpose: Do not blame fake reasoning for unrelated refusals.
        """
        parse_upstream_stop({"stopReason": "CONTENT_FILTERED",
                             "stopDetails": {"refusal": {"category": "SAFETY"}}})

        warnings = [r for r in captured_logs if r["level"].name == "WARNING"]
        assert len(warnings) == 1
        assert "FAKE_REASONING" not in warnings[0]["message"]

    def test_end_turn_logs_nothing(self, captured_logs):
        """
        What it does: Emits no log for a normal END_TURN.
        Purpose: Avoid log noise on every successful response.
        """
        parse_upstream_stop({"stopReason": "END_TURN"})

        assert captured_logs == []


class TestBuildRefusalNotice:
    """Tests for build_refusal_notice()."""

    def test_notice_includes_reason_category_and_explanation(self):
        """
        What it does: Builds the notice from a full refusal.
        Purpose: Users need the upstream explanation verbatim.
        """
        notice = build_refusal_notice(parse_upstream_stop(REAL_REFUSAL_EVENT))

        assert notice.startswith("[Kiro Gateway]")
        assert "CONTENT_FILTERED" in notice
        assert "REASONING_EXTRACTION" in notice
        assert REAL_REFUSAL_EVENT["stopDetails"]["refusal"]["explanation"] in notice

    def test_notice_uses_defaults_for_missing_fields(self):
        """
        What it does: Builds a notice from an UpstreamStop with no details.
        Purpose: The notice must always be actionable.
        """
        notice = build_refusal_notice(UpstreamStop())

        assert DEFAULT_REFUSAL_EXPLANATION in notice
        assert "CONTENT_FILTERED" in notice
        assert "UNKNOWN" in notice


class TestStopFieldConstants:
    """Tests for client-facing stop field constants."""

    def test_native_stop_values(self):
        """
        What it does: Checks the API-native refusal stop values.
        Purpose: Anthropic and OpenAI clients understand these values natively.
        """
        assert ANTHROPIC_REFUSAL_STOP_REASON == "refusal"
        assert OPENAI_REFUSAL_FINISH_REASON == "content_filter"
