# -*- coding: utf-8 -*-

"""Tests for request-scoped Kiro tool-name aliases."""

import asyncio
import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kiro.converters_anthropic import anthropic_to_kiro, anthropic_to_kiro_result
from kiro.converters_core import (
    KiroPayloadResult,
    ThinkingConfig,
    UnifiedMessage,
    UnifiedTool,
    build_kiro_payload,
)
from kiro.converters_openai import build_kiro_payload as build_openai_payload
from kiro.converters_openai import build_kiro_payload_result as build_openai_payload_result
from kiro.models_anthropic import AnthropicMessagesRequest
from kiro.models_openai import ChatCompletionRequest
from kiro.streaming_anthropic import (
    collect_anthropic_response,
    stream_kiro_to_anthropic,
)
from kiro.streaming_core import KiroEvent, StreamResult
from kiro.streaming_openai import collect_stream_response, stream_kiro_to_openai
from kiro.tool_names import (
    KIRO_TOOL_NAME_MAX_LENGTH,
    ToolNameMapping,
    ToolNameTextRestorer,
    _base_alias,
    build_tool_name_mapping,
)


REAL_MCP_TOOL_NAME = (
    "mcp__awslabs_cloudwatch-applicationsignals-mcp-server__"
    "get_instrumentation_configuration_status"
)


class TestToolNameMapping:
    """Validate deterministic alias allocation and reverse lookup."""

    def test_preserves_names_up_to_64_characters(self):
        names = ["get_weather", "a" * KIRO_TOOL_NAME_MAX_LENGTH]

        mapping = build_tool_name_mapping(names)

        for name in names:
            assert mapping.to_kiro(name) == name
            assert mapping.to_original(name) == name

    def test_aliases_real_mcp_name_with_safe_64_character_name(self):
        mapping = build_tool_name_mapping([REAL_MCP_TOOL_NAME])

        alias = mapping.to_kiro(REAL_MCP_TOOL_NAME)

        assert len(alias) <= KIRO_TOOL_NAME_MAX_LENGTH
        assert re.fullmatch(r"[A-Za-z0-9_]+", alias)
        assert mapping.to_original(alias) == REAL_MCP_TOOL_NAME

    def test_mapping_is_independent_of_input_order_and_duplicates(self):
        first = "mcp__server__" + "same_prefix_" * 8 + "first"
        second = "mcp__server__" + "same_prefix_" * 8 + "second"

        mapping_a = build_tool_name_mapping([first, second, first])
        mapping_b = build_tool_name_mapping([second, first])

        assert dict(mapping_a.original_to_kiro) == dict(mapping_b.original_to_kiro)
        assert mapping_a.to_kiro(first) != mapping_a.to_kiro(second)

    def test_avoids_collision_with_existing_short_name(self):
        long_name = "mcp__server__" + "long_tool_name_" * 6
        base_alias = _base_alias(long_name)

        mapping = build_tool_name_mapping([base_alias, long_name])

        assert mapping.to_kiro(long_name) != base_alias
        assert mapping.to_kiro(long_name).endswith("_1")
        assert mapping.to_original(base_alias) == base_alias

    def test_resolves_forced_digest_collisions_deterministically(self, monkeypatch):
        shared_prefix = "mcp__server__" + "identical_readable_prefix_" * 4
        first = shared_prefix + "first"
        second = shared_prefix + "second"
        monkeypatch.setattr(
            "kiro.tool_names._tool_name_digest",
            lambda _name: "f" * 64,
        )

        mapping = build_tool_name_mapping([second, first])

        aliases = {mapping.to_kiro(first), mapping.to_kiro(second)}
        assert len(aliases) == 2
        assert all(len(alias) <= KIRO_TOOL_NAME_MAX_LENGTH for alias in aliases)
        assert any(alias.endswith("_1") for alias in aliases)

    def test_hashes_exact_unicode_name_and_uses_ascii_fallback_stem(self):
        composed = "é" * 65
        decomposed = ("é" * 65)

        mapping = build_tool_name_mapping([composed, decomposed])

        composed_alias = mapping.to_kiro(composed)
        decomposed_alias = mapping.to_kiro(decomposed)
        assert composed_alias != decomposed_alias
        assert composed_alias.startswith("kgw_tool_")
        assert re.fullmatch(r"[A-Za-z0-9_]+", composed_alias)

    def test_defensively_freezes_mapping_inputs(self):
        forward = {"original": "alias"}
        reverse = {"alias": "original"}
        mapping = ToolNameMapping(forward, reverse)

        forward["other"] = "changed"
        reverse["changed"] = "other"

        assert mapping.to_kiro("other") == "other"
        assert mapping.to_original("changed") == "changed"
        with pytest.raises(TypeError):
            mapping.original_to_kiro["new"] = "value"

    def test_unknown_names_pass_through_both_directions(self):
        mapping = build_tool_name_mapping([REAL_MCP_TOOL_NAME])

        assert mapping.to_kiro("unknown_tool") == "unknown_tool"
        assert mapping.to_original("unknown_alias") == "unknown_alias"

    def test_text_restorer_handles_alias_split_across_chunks(self):
        mapping = build_tool_name_mapping([REAL_MCP_TOOL_NAME])
        alias = mapping.to_kiro(REAL_MCP_TOOL_NAME)
        restorer = ToolNameTextRestorer(mapping)
        split_at = len(alias) // 2

        first = restorer.feed(f"[Called {alias[:split_at]}")
        second = restorer.feed(
            f'{alias[split_at:]} with args: {{"query": "errors"}}]'
        )
        final = restorer.flush()

        restored = first + second + final
        assert alias not in restored
        assert REAL_MCP_TOOL_NAME in restored
        assert restored == (
            f'[Called {REAL_MCP_TOOL_NAME} with args: {{"query": "errors"}}]'
        )


class TestRichConverterContracts:
    """Verify rich route converters and legacy dictionary wrappers."""

    def test_anthropic_rich_converter_exposes_mapping(self):
        request = AnthropicMessagesRequest(
            model="claude-sonnet-4",
            max_tokens=128,
            messages=[{"role": "user", "content": "Use the tool"}],
            tools=[{
                "name": REAL_MCP_TOOL_NAME,
                "description": "CloudWatch operation",
                "input_schema": {"type": "object"},
            }],
        )

        rich_result = anthropic_to_kiro_result(request, "conv-123", "")
        legacy_result = anthropic_to_kiro(request, "conv-123", "")
        alias = rich_result.tool_name_mapping.to_kiro(REAL_MCP_TOOL_NAME)

        assert isinstance(rich_result, KiroPayloadResult)
        assert isinstance(legacy_result, dict)
        assert len(alias) <= KIRO_TOOL_NAME_MAX_LENGTH
        assert rich_result.tool_name_mapping.to_original(alias) == REAL_MCP_TOOL_NAME
        assert legacy_result == rich_result.payload

    def test_openai_rich_converter_exposes_mapping(self):
        request = ChatCompletionRequest(
            model="claude-sonnet-4",
            messages=[{"role": "user", "content": "Use the tool"}],
            tools=[{
                "type": "function",
                "function": {
                    "name": REAL_MCP_TOOL_NAME,
                    "description": "CloudWatch operation",
                    "parameters": {"type": "object"},
                },
            }],
        )

        rich_result = build_openai_payload_result(request, "conv-123", "")
        legacy_result = build_openai_payload(request, "conv-123", "")
        alias = rich_result.tool_name_mapping.to_kiro(REAL_MCP_TOOL_NAME)

        assert isinstance(rich_result, KiroPayloadResult)
        assert isinstance(legacy_result, dict)
        assert len(alias) <= KIRO_TOOL_NAME_MAX_LENGTH
        assert rich_result.tool_name_mapping.to_original(alias) == REAL_MCP_TOOL_NAME
        assert legacy_result == rich_result.payload


class TestToolNameMappingPayloadIntegration:
    """Validate that definitions and historical calls share one alias."""

    def test_aliases_definition_and_history_without_changing_ids_or_inputs(self):
        messages = [
            UnifiedMessage(role="user", content="Use the tool"),
            UnifiedMessage(
                role="assistant",
                content="Calling it",
                tool_calls=[{
                    "id": "toolu_123",
                    "type": "function",
                    "function": {
                        "name": REAL_MCP_TOOL_NAME,
                        "arguments": '{"query": "errors"}',
                    },
                }],
            ),
            UnifiedMessage(
                role="user",
                content="Result",
                tool_results=[{
                    "tool_use_id": "toolu_123",
                    "content": "done",
                }],
            ),
            UnifiedMessage(role="assistant", content="Finished"),
            UnifiedMessage(role="user", content="Continue"),
        ]

        result = build_kiro_payload(
            messages=messages,
            system_prompt="",
            model_id="claude-sonnet-4",
            tools=[UnifiedTool(
                name=REAL_MCP_TOOL_NAME,
                description="CloudWatch operation",
                input_schema={"type": "object"},
            )],
            conversation_id="conv-123",
            profile_arn="",
            thinking_config=ThinkingConfig(enabled=False),
        )

        alias = result.tool_name_mapping.to_kiro(REAL_MCP_TOOL_NAME)
        current_tools = result.payload["conversationState"]["currentMessage"][
            "userInputMessage"
        ]["userInputMessageContext"]["tools"]
        history = result.payload["conversationState"]["history"]
        historical_use = history[1]["assistantResponseMessage"]["toolUses"][0]
        historical_result = history[2]["userInputMessage"][
            "userInputMessageContext"
        ]["toolResults"][0]

        assert current_tools[0]["toolSpecification"]["name"] == alias
        assert current_tools[0]["toolSpecification"]["description"] == "CloudWatch operation"
        assert historical_use == {
            "name": alias,
            "input": {"query": "errors"},
            "toolUseId": "toolu_123",
        }
        assert historical_result["toolUseId"] == "toolu_123"
        assert historical_result["content"] == [{"text": "done"}]
        assert result.tool_name_mapping.to_original(alias) == REAL_MCP_TOOL_NAME


class TestToolNameMappingResponses:
    """Verify original names across both API surfaces and response modes."""

    @staticmethod
    def _dependencies():
        response = AsyncMock()
        response.status_code = 200
        response.aclose = AsyncMock()
        model_cache = MagicMock()
        model_cache.get_max_input_tokens.return_value = 200000
        auth_manager = MagicMock()
        http_client = AsyncMock()
        return response, model_cache, auth_manager, http_client

    @staticmethod
    def _tool_event(alias: str) -> KiroEvent:
        return KiroEvent(
            type="tool_use",
            tool_use={
                "id": "toolu_123",
                "type": "function",
                "function": {
                    "name": alias,
                    "arguments": '{"query": "errors"}',
                },
            },
        )

    @pytest.mark.asyncio
    async def test_anthropic_streaming_restores_structured_tool_name(self):
        mapping = build_tool_name_mapping([REAL_MCP_TOOL_NAME])
        alias = mapping.to_kiro(REAL_MCP_TOOL_NAME)
        response, model_cache, auth_manager, _ = self._dependencies()

        async def events(*_args, **_kwargs):
            yield self._tool_event(alias)
            yield KiroEvent(type="context_usage", context_usage_percentage=1.0)

        chunks = []
        with patch("kiro.streaming_anthropic.parse_kiro_stream", events):
            with patch("kiro.streaming_anthropic.parse_bracket_tool_calls", return_value=[]):
                async for chunk in stream_kiro_to_anthropic(
                    response,
                    "claude-sonnet-4",
                    model_cache,
                    auth_manager,
                    tool_name_mapping=mapping,
                ):
                    chunks.append(chunk)

        tool_starts = [
            json.loads(chunk.split("data: ", 1)[1])
            for chunk in chunks
            if chunk.startswith("event: content_block_start")
            and '"type": "tool_use"' in chunk
        ]
        assert tool_starts[0]["content_block"]["name"] == REAL_MCP_TOOL_NAME
        assert tool_starts[0]["content_block"]["id"] == "toolu_123"

    @pytest.mark.asyncio
    async def test_anthropic_non_streaming_restores_structured_tool_name(self):
        mapping = build_tool_name_mapping([REAL_MCP_TOOL_NAME])
        alias = mapping.to_kiro(REAL_MCP_TOOL_NAME)
        response, model_cache, auth_manager, _ = self._dependencies()
        stream_result = StreamResult(
            content="",
            thinking_content="",
            tool_calls=[self._tool_event(alias).tool_use],
            usage=None,
            context_usage_percentage=1.0,
        )

        with patch(
            "kiro.streaming_anthropic.collect_stream_to_result",
            return_value=stream_result,
        ):
            result = await collect_anthropic_response(
                response,
                "claude-sonnet-4",
                model_cache,
                auth_manager,
                tool_name_mapping=mapping,
            )

        tool_block = next(block for block in result["content"] if block["type"] == "tool_use")
        assert tool_block["name"] == REAL_MCP_TOOL_NAME
        assert tool_block["id"] == "toolu_123"
        assert tool_block["input"] == {"query": "errors"}

    @pytest.mark.asyncio
    async def test_openai_streaming_restores_structured_tool_name(self):
        mapping = build_tool_name_mapping([REAL_MCP_TOOL_NAME])
        alias = mapping.to_kiro(REAL_MCP_TOOL_NAME)
        response, model_cache, auth_manager, http_client = self._dependencies()

        async def events(*_args, **_kwargs):
            yield self._tool_event(alias)
            yield KiroEvent(type="context_usage", context_usage_percentage=1.0)

        chunks = []
        with patch("kiro.streaming_openai.parse_kiro_stream", events):
            with patch("kiro.streaming_openai.parse_bracket_tool_calls", return_value=[]):
                async for chunk in stream_kiro_to_openai(
                    http_client,
                    response,
                    "claude-sonnet-4",
                    model_cache,
                    auth_manager,
                    tool_name_mapping=mapping,
                ):
                    chunks.append(chunk)

        payloads = [
            json.loads(chunk.removeprefix("data: ").strip())
            for chunk in chunks
            if chunk.startswith("data: {") and '"tool_calls"' in chunk
        ]
        tool_call = payloads[0]["choices"][0]["delta"]["tool_calls"][0]
        assert tool_call["function"]["name"] == REAL_MCP_TOOL_NAME
        assert tool_call["id"] == "toolu_123"

    @pytest.mark.asyncio
    async def test_openai_non_streaming_restores_structured_tool_name(self):
        mapping = build_tool_name_mapping([REAL_MCP_TOOL_NAME])
        alias = mapping.to_kiro(REAL_MCP_TOOL_NAME)
        response, model_cache, auth_manager, http_client = self._dependencies()

        async def events(*_args, **_kwargs):
            yield self._tool_event(alias)
            yield KiroEvent(type="context_usage", context_usage_percentage=1.0)

        with patch("kiro.streaming_openai.parse_kiro_stream", events):
            with patch("kiro.streaming_openai.parse_bracket_tool_calls", return_value=[]):
                result = await collect_stream_response(
                    http_client,
                    response,
                    "claude-sonnet-4",
                    model_cache,
                    auth_manager,
                    tool_name_mapping=mapping,
                )

        tool_call = result["choices"][0]["message"]["tool_calls"][0]
        assert tool_call["function"]["name"] == REAL_MCP_TOOL_NAME
        assert tool_call["id"] == "toolu_123"
        assert tool_call["function"]["arguments"] == '{"query": "errors"}'

    @pytest.mark.asyncio
    @pytest.mark.parametrize("api", ["anthropic", "openai"])
    async def test_streaming_restores_bracket_tool_name(self, api):
        mapping = build_tool_name_mapping([REAL_MCP_TOOL_NAME])
        alias = mapping.to_kiro(REAL_MCP_TOOL_NAME)
        response, model_cache, auth_manager, http_client = self._dependencies()
        bracket_text = f'[Called {alias} with args: {{"query": "errors"}}]'

        async def events(*_args, **_kwargs):
            yield KiroEvent(type="content", content=bracket_text)
            yield KiroEvent(type="context_usage", context_usage_percentage=1.0)

        if api == "anthropic":
            chunks = []
            with patch("kiro.streaming_anthropic.parse_kiro_stream", events):
                async for chunk in stream_kiro_to_anthropic(
                    response,
                    "claude-sonnet-4",
                    model_cache,
                    auth_manager,
                    tool_name_mapping=mapping,
                ):
                    chunks.append(chunk)
            tool_chunks = [
                chunk for chunk in chunks
                if chunk.startswith("event: content_block_start")
                and '"type": "tool_use"' in chunk
            ]
        else:
            chunks = []
            with patch("kiro.streaming_openai.parse_kiro_stream", events):
                async for chunk in stream_kiro_to_openai(
                    http_client,
                    response,
                    "claude-sonnet-4",
                    model_cache,
                    auth_manager,
                    tool_name_mapping=mapping,
                ):
                    chunks.append(chunk)
            tool_chunks = [
                chunk for chunk in chunks
                if chunk.startswith("data: {") and '"tool_calls"' in chunk
            ]

        assert tool_chunks
        assert REAL_MCP_TOOL_NAME in tool_chunks[0]

    @pytest.mark.asyncio
    async def test_concurrent_requests_keep_reverse_maps_isolated(self):
        alias = "kgw_shared_alias_" + "a" * 47
        first_name = "mcp__first_server__" + "first_tool_" * 6
        second_name = "mcp__second_server__" + "second_tool_" * 6
        first_mapping = ToolNameMapping(
            {first_name: alias},
            {alias: first_name},
        )
        second_mapping = ToolNameMapping(
            {second_name: alias},
            {alias: second_name},
        )

        async def events(*_args, **_kwargs):
            yield self._tool_event(alias)
            yield KiroEvent(type="context_usage", context_usage_percentage=1.0)

        async def collect(mapping):
            response, model_cache, auth_manager, _ = self._dependencies()
            chunks = []
            async for chunk in stream_kiro_to_anthropic(
                response,
                "claude-sonnet-4",
                model_cache,
                auth_manager,
                tool_name_mapping=mapping,
            ):
                chunks.append(chunk)
            return "".join(chunks)

        with patch("kiro.streaming_anthropic.parse_kiro_stream", events):
            with patch("kiro.streaming_anthropic.parse_bracket_tool_calls", return_value=[]):
                first_output, second_output = await asyncio.gather(
                    collect(first_mapping),
                    collect(second_mapping),
                )

        assert first_name in first_output
        assert second_name not in first_output
        assert second_name in second_output
        assert first_name not in second_output
