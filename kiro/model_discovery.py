# -*- coding: utf-8 -*-

# Kiro Gateway
# https://github.com/jwadow/kiro-gateway
#
# Licensed under GNU AGPL v3+ (same as parent project).

"""Shared model discovery through Kiro's legacy Q endpoint."""

import json
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from kiro.auth import KiroAuthManager
from kiro.http_client import KiroHttpClient


LIST_AVAILABLE_MODELS_TARGET = "AmazonCodeWhispererService.ListAvailableModels"


class ModelDiscoveryError(RuntimeError):
    """Raised when Kiro's model catalog cannot be fetched or parsed."""


async def fetch_available_models(
    auth_manager: KiroAuthManager,
    http_client: Optional[KiroHttpClient] = None,
    shared_client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """Fetch the model catalog exposed by Kiro's legacy Q endpoint.

    Args:
        auth_manager: Authenticated Kiro account whose API region and credentials
            should be used.
        http_client: Optional preconfigured Kiro HTTP client. The caller retains
            ownership when this is provided.
        shared_client: Optional application-level HTTP client to reuse when this
            function creates its own Kiro HTTP client.

    Returns:
        Non-empty list of model metadata dictionaries containing ``modelId``.

    Raises:
        ModelDiscoveryError: If the request fails, returns a non-200 status, or
            contains malformed or empty model data.
    """
    params = {"origin": "AI_EDITOR"}
    if auth_manager.profile_arn:
        params["profileArn"] = auth_manager.profile_arn

    owns_http_client = http_client is None
    client = http_client or KiroHttpClient(
        auth_manager,
        shared_client=shared_client,
    )
    try:
        try:
            response = await client.request_with_retry(
                method="GET",
                url=f"{auth_manager.q_host}/ListAvailableModels",
                params=params,
                extra_headers={"x-amz-target": LIST_AVAILABLE_MODELS_TARGET},
            )
        except HTTPException as exc:
            raise ModelDiscoveryError(
                f"ListAvailableModels request failed: {exc.detail}"
            ) from exc

        if response.status_code != 200:
            raise ModelDiscoveryError(
                f"ListAvailableModels returned HTTP {response.status_code}"
            )

        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ModelDiscoveryError(
                "ListAvailableModels returned invalid JSON"
            ) from exc

        if not isinstance(payload, dict):
            raise ModelDiscoveryError(
                "ListAvailableModels returned a non-object response"
            )

        models = payload.get("models")
        if not isinstance(models, list):
            raise ModelDiscoveryError(
                "ListAvailableModels response is missing the models list"
            )

        valid_models = [
            model
            for model in models
            if isinstance(model, dict)
            and isinstance(model.get("modelId"), str)
            and model["modelId"]
        ]
        if not valid_models:
            raise ModelDiscoveryError(
                "ListAvailableModels returned no valid model IDs"
            )

        return valid_models
    finally:
        if owns_http_client:
            await client.close()
