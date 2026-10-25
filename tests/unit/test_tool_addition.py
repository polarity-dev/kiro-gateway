# -*- coding: utf-8 -*-

"""
Unit tests for tool_addition blocks (tools Claude Code adds mid-conversation).

Regression: an MCP server connecting after the session started made Claude Code send a
tool_addition block in a user message; the gateway answered 422 to that request and to
every later one of the session, because the block stays in the history.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from kiro.converters_anthropic import (
    anthropic_to_kiro,
    convert_anthropic_messages,
    extract_tool_additions,
    merge_tool_additions,
)
from kiro.models_anthropic import (
    AnthropicMessage,
    AnthropicMessagesRequest,
    AnthropicTool,
    ToolAdditionContentBlock,
)


RUN_SCRIPT = {
    "name": "mcp__aws-mcp__aws___run_script",
    "description": "Execute Python code in sandboxed environment with AWS API access.",
    "input_schema": {
        "type": "object",
        "properties": {"code": {"description": "Python code to execute", "type": "string"}},
        "required": ["code"],
        "additionalProperties": False,
    },
}


def tool_addition(definition=RUN_SCRIPT):
    """The block as Claude Code sent it (from the gateway log of 2026-10-25)."""
    return {
        "type": "tool_addition",
        "tool": {"type": "tool_definition", "definition": definition},
        "cache_control": {"type": "ephemeral"},
    }


def request_payload():
    """A conversation whose 7th message carries the tool_addition, as in the log."""
    messages = []
    for i in range(3):
        messages.append({"role": "user", "content": f"question {i}"})
        messages.append({"role": "assistant", "content": f"answer {i}"})
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": "# Environment update\n - Primary working directory: /tmp/app"},
            tool_addition(),
        ],
    })
    return {
        "model": "claude-sonnet-4-5",
        "max_tokens": 1024,
        "messages": messages,
        "tools": [{"name": "Bash", "description": "Run a command", "input_schema": {"type": "object", "properties": {}}}],
    }


def tool_names(payload):
    context = payload["conversationState"]["currentMessage"]["userInputMessage"]["userInputMessageContext"]
    return [t["toolSpecification"]["name"] for t in context["tools"]]


class TestToolAdditionModel:
    def test_request_with_tool_addition_validates(self):
        """
        What it does: Parses the request shape that failed with 422.
        Purpose: The block must be accepted as its own content block type.
        """
        request = AnthropicMessagesRequest(**request_payload())
        block = request.messages[6].content[1]
        print(f"Block type: {type(block).__name__}")
        assert isinstance(block, ToolAdditionContentBlock)
        assert block.tool["definition"]["name"] == RUN_SCRIPT["name"]

    def test_keeps_extra_fields(self):
        """cache_control and future fields are kept, not rejected."""
        block = ToolAdditionContentBlock(**tool_addition())
        assert block.model_dump()["cache_control"] == {"type": "ephemeral"}

    def test_rejects_other_type(self):
        """Only type tool_addition matches this model."""
        with pytest.raises(ValidationError):
            ToolAdditionContentBlock(type="text", tool={})

    def test_without_tool_still_validates(self):
        """A bare block must not break the session either."""
        request = AnthropicMessagesRequest(
            model="claude-sonnet-4-5", max_tokens=10,
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "tool_addition"}]}],
        )
        assert extract_tool_additions(request.messages) == []


class TestExtractToolAdditions:
    def test_extracts_definition(self):
        request = AnthropicMessagesRequest(**request_payload())
        added = extract_tool_additions(request.messages)
        assert [t.name for t in added] == [RUN_SCRIPT["name"]]
        assert added[0].input_schema == RUN_SCRIPT["input_schema"]
        assert added[0].description == RUN_SCRIPT["description"]

    def test_skips_malformed_definitions(self):
        """No name, wrong types: skipped, never an exception."""
        messages = [AnthropicMessage(role="user", content=[
            {"type": "tool_addition", "tool": {"type": "tool_definition"}},
            {"type": "tool_addition", "tool": {"definition": "not a dict"}},
            {"type": "tool_addition", "tool": {"definition": {"name": ""}}},
            {"type": "tool_addition", "tool": {"definition": {"name": 42}}},
            {"type": "tool_addition", "tool": {"definition": {"name": "no_schema", "input_schema": "bad"}}},
        ])]
        added = extract_tool_additions(messages)
        assert [t.name for t in added] == ["no_schema"]
        assert added[0].input_schema == {"type": "object", "properties": {}}

    def test_ignores_string_content(self):
        messages = [AnthropicMessage(role="user", content="tool_addition")]
        assert extract_tool_additions(messages) == []


class TestMergeToolAdditions:
    def test_request_tool_wins(self):
        """A tool already in the request keeps its own definition."""
        existing = AnthropicTool(name=RUN_SCRIPT["name"], description="from request", input_schema={"type": "object"})
        merged = merge_tool_additions([existing], [AnthropicTool(**RUN_SCRIPT)])
        assert len(merged) == 1
        assert merged[0].description == "from request"

    def test_repeated_additions_count_once(self):
        merged = merge_tool_additions(None, [AnthropicTool(**RUN_SCRIPT), AnthropicTool(**RUN_SCRIPT)])
        assert [t.name for t in merged] == [RUN_SCRIPT["name"]]

    def test_no_additions_keeps_tools(self):
        assert merge_tool_additions(None, []) is None


class TestToolAdditionConversion:
    def test_block_not_in_text(self):
        """The definition is not pasted into the conversation as text."""
        request = AnthropicMessagesRequest(**request_payload())
        unified = convert_anthropic_messages(request.messages)
        print(f"Last message text: {unified[-1].content!r}")
        assert "tool_definition" not in unified[-1].content
        assert "Environment update" in unified[-1].content

    def test_added_tool_sent_to_kiro(self):
        """The tool announced in the history is usable: it is among the Kiro tools."""
        request = AnthropicMessagesRequest(**request_payload())
        payload = anthropic_to_kiro(request, "conv-1", "arn:aws:codewhisperer:us-east-1:000000000000:profile/test")
        names = tool_names(payload)
        print(f"Kiro tools: {names}")
        assert "Bash" in names
        assert RUN_SCRIPT["name"] in names

    def test_addition_in_history_still_sent(self):
        """Later turns keep the block in history: the tool stays available."""
        data = request_payload()
        data["messages"] += [
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "next question"},
        ]
        payload = anthropic_to_kiro(AnthropicMessagesRequest(**data), "conv-1", "arn:aws:codewhisperer:us-east-1:000000000000:profile/test")
        assert RUN_SCRIPT["name"] in tool_names(payload)


class TestToolAdditionRoute:
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
