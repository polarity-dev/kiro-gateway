# -*- coding: utf-8 -*-

"""
Unit tests for the readable summary of 422 validation errors.

An unknown content block type made Pydantic report one error per union member, so
the log line of the 422 hid the only useful fact (the block type) in a wall of noise.
The request is still rejected; only the report changes.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import ValidationError

from kiro.exceptions import summarize_validation_errors, validation_exception_handler
from kiro.models_anthropic import AnthropicMessagesRequest


def validation_errors(content):
    """Errors as FastAPI reports them for a user message with the given content."""
    with pytest.raises(ValidationError) as exc_info:
        AnthropicMessagesRequest(
            model="claude-sonnet-4-5", max_tokens=10,
            messages=[{"role": "user", "content": "hi"}, {"role": "user", "content": content}],
        )
    return [dict(error, loc=("body",) + tuple(error["loc"])) for error in exc_info.value.errors()]


class TestSummarizeValidationErrors:
    def test_unknown_block_type_is_one_line(self):
        """
        What it does: Summarizes the errors of a block type no model accepts.
        Purpose: The 422 must name the block type and where it is, in one line.
        """
        errors = validation_errors([
            {"type": "text", "text": "Environment update"},
            {"type": "tool_frobnicate", "tool": {"name": "x"}, "cache_control": {"type": "ephemeral"}},
        ])
        summary = summarize_validation_errors(errors)
        print(f"{len(errors)} errors -> {summary}")
        assert len(errors) > 10
        assert summary == ["Unsupported content block type 'tool_frobnicate' at messages[1].content[1]"]

    def test_known_block_type_keeps_its_own_errors(self):
        """A known block with a missing field reports that field, not the union noise."""
        summary = summarize_validation_errors(validation_errors([{"type": "tool_use", "name": "Bash", "input": {}}]))
        assert summary == ["messages[1].content[0].id: Field required"]

    def test_two_unknown_blocks_two_lines(self):
        summary = summarize_validation_errors(validation_errors([{"type": "a_block"}, {"type": "b_block"}]))
        assert summary == [
            "Unsupported content block type 'a_block' at messages[1].content[0]",
            "Unsupported content block type 'b_block' at messages[1].content[1]",
        ]

    def test_plain_field_error(self):
        summary = summarize_validation_errors([{"type": "missing", "loc": ("body", "model"), "msg": "Field required", "input": {}}])
        assert summary == ["model: Field required"]

    def test_empty(self):
        assert summarize_validation_errors([]) == []


class TestHandlerReportsSummary:
    @pytest.mark.asyncio
    async def test_log_line_and_response_carry_summary(self):
        """
        What it does: Runs the handler on the errors of an unknown block type.
        Purpose: The ERROR log line and the response message are the summary; the
        full Pydantic errors stay in the response detail.
        """
        errors = validation_errors([{"type": "tool_frobnicate"}])
        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=b"{}")
        exc = MagicMock(spec=RequestValidationError)
        exc.errors.return_value = errors

        with patch("kiro.debug_logger.debug_logger"), patch("kiro.exceptions.logger") as logger:
            response = await validation_exception_handler(request, exc)

        logged = logger.error.call_args[0][0]
        print(f"Logged: {logged}")
        assert logged == "Validation error (422): Unsupported content block type 'tool_frobnicate' at messages[1].content[0]"
        body = json.loads(response.body)
        assert response.status_code == 422
        assert body["message"] == "Unsupported content block type 'tool_frobnicate' at messages[1].content[0]"
        assert len(body["detail"]) == len(errors)


class TestRouteStillRejects:
    def test_unknown_block_still_422(self, test_client: TestClient, valid_proxy_api_key):
        """Unknown block types keep failing loudly; only the message is clearer."""
        response = test_client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-4-5", "max_tokens": 10,
                "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "tool_frobnicate"}]}],
            },
            headers={"x-api-key": valid_proxy_api_key, "anthropic-version": "2023-06-01"},
        )
        print(f"Status: {response.status_code}, message: {response.json().get('message')}")
        assert response.status_code == 422
        assert response.json()["message"] == "Unsupported content block type 'tool_frobnicate' at messages[0].content[1]"
