# -*- coding: utf-8 -*-

"""Tests for shared Kiro model discovery."""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from kiro.auth import KiroAuthManager
from kiro.model_discovery import (
    LIST_AVAILABLE_MODELS_TARGET,
    ModelDiscoveryError,
    fetch_available_models,
)


@pytest.fixture
def discovery_auth() -> Mock:
    """Create a minimal auth manager mock for model discovery."""
    auth = Mock(spec=KiroAuthManager)
    auth.q_host = "https://q.eu-central-1.amazonaws.com"
    auth.profile_arn = "arn:aws:codewhisperer:eu-central-1:123:profile/test"
    return auth


@pytest.mark.asyncio
async def test_fetch_available_models_uses_legacy_contract(discovery_auth: Mock) -> None:
    """Verify the endpoint, target header, profile ARN, and model filtering."""
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "models": [
            {"modelId": "gpt-5.6-sol", "tokenLimits": {"maxInputTokens": 200000}},
            {"modelId": ""},
            {"displayName": "missing id"},
            "invalid entry",
        ]
    }
    client = Mock()
    client.request_with_retry = AsyncMock(return_value=response)
    client.close = AsyncMock()

    models = await fetch_available_models(discovery_auth, http_client=client)

    assert models == [
        {"modelId": "gpt-5.6-sol", "tokenLimits": {"maxInputTokens": 200000}}
    ]
    client.request_with_retry.assert_awaited_once_with(
        method="GET",
        url="https://q.eu-central-1.amazonaws.com/ListAvailableModels",
        params={
            "origin": "AI_EDITOR",
            "profileArn": discovery_auth.profile_arn,
        },
        extra_headers={"x-amz-target": LIST_AVAILABLE_MODELS_TARGET},
    )
    client.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_available_models_omits_missing_profile_arn(
    discovery_auth: Mock,
) -> None:
    """Verify personal accounts do not send an empty profileArn parameter."""
    discovery_auth.profile_arn = None
    response = Mock(status_code=200)
    response.json.return_value = {"models": [{"modelId": "auto"}]}
    client = Mock()
    client.request_with_retry = AsyncMock(return_value=response)
    client.close = AsyncMock()

    await fetch_available_models(discovery_auth, http_client=client)

    assert client.request_with_retry.await_args.kwargs["params"] == {
        "origin": "AI_EDITOR"
    }


@pytest.mark.asyncio
async def test_fetch_available_models_closes_owned_client(
    discovery_auth: Mock,
) -> None:
    """Verify internally-created Kiro HTTP clients are always closed."""
    response = Mock(status_code=200)
    response.json.return_value = {"models": [{"modelId": "auto"}]}
    client = Mock()
    client.request_with_retry = AsyncMock(return_value=response)
    client.close = AsyncMock()

    with patch("kiro.model_discovery.KiroHttpClient", return_value=client):
        await fetch_available_models(discovery_auth)

    client.close.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 403, 404, 500, 529])
async def test_fetch_available_models_rejects_non_200_status(
    discovery_auth: Mock,
    status_code: int,
) -> None:
    """Verify every non-success response becomes an actionable discovery error."""
    response = Mock(status_code=status_code)
    client = Mock()
    client.request_with_retry = AsyncMock(return_value=response)
    client.close = AsyncMock()

    with pytest.raises(ModelDiscoveryError, match=f"HTTP {status_code}"):
        await fetch_available_models(discovery_auth, http_client=client)


@pytest.mark.asyncio
async def test_fetch_available_models_wraps_retry_failure(discovery_auth: Mock) -> None:
    """Verify network retry exhaustion is normalized for gateway fallback logic."""
    client = Mock()
    client.request_with_retry = AsyncMock(
        side_effect=HTTPException(status_code=502, detail="DNS failed")
    )
    client.close = AsyncMock()

    with pytest.raises(ModelDiscoveryError, match="DNS failed"):
        await fetch_available_models(discovery_auth, http_client=client)


@pytest.mark.asyncio
async def test_fetch_available_models_rejects_invalid_json(discovery_auth: Mock) -> None:
    """Verify malformed JSON cannot silently replace the model cache."""
    response = Mock(status_code=200)
    response.json.side_effect = json.JSONDecodeError("bad", "{", 0)
    client = Mock()
    client.request_with_retry = AsyncMock(return_value=response)
    client.close = AsyncMock()

    with pytest.raises(ModelDiscoveryError, match="invalid JSON"):
        await fetch_available_models(discovery_auth, http_client=client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload, message",
    [
        ([], "non-object"),
        ({}, "missing the models list"),
        ({"models": {}}, "missing the models list"),
        ({"models": []}, "no valid model IDs"),
        ({"models": [{"modelId": None}]}, "no valid model IDs"),
    ],
)
async def test_fetch_available_models_rejects_malformed_schema(
    discovery_auth: Mock,
    payload: object,
    message: str,
) -> None:
    """Verify malformed and empty catalogs preserve the previous/fallback cache."""
    response = Mock(status_code=200)
    response.json.return_value = payload
    client = Mock()
    client.request_with_retry = AsyncMock(return_value=response)
    client.close = AsyncMock()

    with pytest.raises(ModelDiscoveryError, match=message):
        await fetch_available_models(discovery_auth, http_client=client)


def test_state_catalog_deduplicates_by_model_identity(tmp_path) -> None:
    """Account-specific metadata variants produce one global picker row."""
    from kiro.model_discovery import load_state_model_catalog

    state = tmp_path / "state.json"
    state.write_text(
        json.dumps({
            "accounts": {
                "a": {"model_catalog": [{"modelId": "gpt-x", "rateMultiplier": 1}]},
                "b": {"model_catalog": [{"modelId": "gpt-x", "rateMultiplier": 2}]},
            }
        }),
        encoding="utf-8",
    )

    assert load_state_model_catalog(state) == [
        {"modelId": "gpt-x", "rateMultiplier": 1}
    ]
