# -*- coding: utf-8 -*-

"""
Unit tests for tool_removal blocks (tools Claude Code withdraws mid-conversation).

Regression: an MCP server disconnecting made Claude Code send a tool_removal block in a
user message; the gateway answered 422 to that request and to every later one of the
session, because the block stays in the history.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from kiro.converters_anthropic import (
    anthropic_to_kiro,
    convert_anthropic_messages,
    extract_tool_additions,
)
from kiro.models_anthropic import (
    AnthropicMessage,
    AnthropicMessagesRequest,
    ToolRemovalContentBlock,
)
from tests.unit.test_tool_addition import RUN_SCRIPT, tool_addition, tool_names


PROFILE_ARN = "arn:aws:codewhisperer:us-east-1:000000000000:profile/test"


def tool_removal(name=RUN_SCRIPT["name"]):
    """The block as Claude Code sent it (from the gateway log of 2026-11-18)."""
    return {
        "type": "tool_removal",
        "tool": {"type": "tool_reference", "name": name},
        "cache_control": {"type": "ephemeral"},
    }


def request_payload(with_addition=True):
    """A conversation that adds the tool, then withdraws it, as in the log."""
    messages = [{"role": "user", "content": "question 0"}, {"role": "assistant", "content": "answer 0"}]
    if with_addition:
        messages += [
            {"role": "user", "content": [{"type": "text", "text": "# Environment update"}, tool_addition()]},
            {"role": "assistant", "content": "answer 1"},
        ]
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": "# Environment update\n - Primary working directory: /tmp/app"},
            tool_removal(),
        ],
    })
    return {
        "model": "claude-sonnet-4-5",
        "max_tokens": 1024,
        "messages": messages,
        "tools": [{"name": "Bash", "description": "Run a command", "input_schema": {"type": "object", "properties": {}}}],
    }


class TestToolRemovalModel:
    def test_request_with_tool_removal_validates(self):
        """
        What it does: Parses the request shape that failed with 422.
        Purpose: The block must be accepted as its own content block type.
        """
        request = AnthropicMessagesRequest(**request_payload())
        block = request.messages[-1].content[1]
        print(f"Block type: {type(block).__name__}")
        assert isinstance(block, ToolRemovalContentBlock)
        assert block.tool["name"] == RUN_SCRIPT["name"]

    def test_keeps_extra_fields(self):
        block = ToolRemovalContentBlock(**tool_removal())
        assert block.model_dump()["cache_control"] == {"type": "ephemeral"}

    def test_rejects_other_type(self):
        with pytest.raises(ValidationError):
            ToolRemovalContentBlock(type="text", tool={})

    def test_without_tool_still_validates(self):
        """A bare block must not break the session either."""
        request = AnthropicMessagesRequest(
            model="claude-sonnet-4-5", max_tokens=10,
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "tool_removal"}]}],
        )
        assert extract_tool_additions(request.messages) == []


class TestToolRemovalOrdering:
    def test_removal_withdraws_earlier_addition(self):
        request = AnthropicMessagesRequest(**request_payload())
        assert extract_tool_additions(request.messages) == []

    def test_addition_after_removal_restores_tool(self):
        """MCP server reconnecting: the latest announcement counts."""
        messages = [AnthropicMessage(role="user", content=[tool_addition(), tool_removal(), tool_addition()])]
        assert [t.name for t in extract_tool_additions(messages)] == [RUN_SCRIPT["name"]]

    def test_removal_of_unknown_tool_is_ignored(self):
        messages = [AnthropicMessage(role="user", content=[tool_addition(), tool_removal("other"), {"type": "tool_removal", "tool": {"name": 42}}])]
        assert [t.name for t in extract_tool_additions(messages)] == [RUN_SCRIPT["name"]]


class TestToolRemovalConversion:
    def test_block_not_in_text(self):
        request = AnthropicMessagesRequest(**request_payload())
        unified = convert_anthropic_messages(request.messages)
        print(f"Last message text: {unified[-1].content!r}")
        assert "tool_reference" not in unified[-1].content
        assert "Environment update" in unified[-1].content

    def test_removed_tool_not_sent_to_kiro(self):
        request = AnthropicMessagesRequest(**request_payload())
        names = tool_names(anthropic_to_kiro(request, "conv-1", PROFILE_ARN))
        print(f"Kiro tools: {names}")
        assert names == ["Bash"]

    def test_removal_without_addition_keeps_request_tools(self):
        request = AnthropicMessagesRequest(**request_payload(with_addition=False))
        assert tool_names(anthropic_to_kiro(request, "conv-1", PROFILE_ARN)) == ["Bash"]


class TestToolRemovalRoute:
    def test_route_does_not_answer_422(self, test_client: TestClient, valid_proxy_api_key):
        """
        What it does: Posts the request shape of the log to /v1/messages.
        Purpose: The validation layer must not reject it (it answered 422 before the fix).
        """
        response = test_client.post(
            "/v1/messages",
            json=request_payload(),
            headers={"x-api-key": valid_proxy_api_key, "anthropic-version": "2023-06-01"},
        )
        print(f"Status: {response.status_code}")
        assert response.status_code != 422
