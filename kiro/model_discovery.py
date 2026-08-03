# -*- coding: utf-8 -*-

# Kiro Gateway
# https://github.com/jwadow/kiro-gateway
#
# Licensed under GNU AGPL v3+ (same as parent project).

"""Shared model discovery through Kiro's legacy Q endpoint."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from kiro.auth import KiroAuthManager
from kiro.http_client import KiroHttpClient
from kiro.model_resolver import build_model_display_id, set_known_model_ids


LIST_AVAILABLE_MODELS_TARGET = "AmazonCodeWhispererService.ListAvailableModels"


class ModelDiscoveryError(RuntimeError):
    """Raised when Kiro's model catalog cannot be fetched or parsed."""


def validate_model_catalog(catalog: object) -> List[Dict[str, Any]]:
    """Return valid model metadata entries from a catalog-like value.

    Args:
        catalog: Value expected to contain Kiro model metadata dictionaries.

    Returns:
        Entries with a non-empty string ``modelId``. Invalid entries are ignored.
    """
    if not isinstance(catalog, list):
        return []
    return [
        model
        for model in catalog
        if isinstance(model, dict)
        and isinstance(model.get("modelId"), str)
        and model["modelId"]
    ]


def build_catalog_display_ids(catalog: object) -> List[str]:
    """Build deterministic Claude Code model IDs from Kiro metadata.

    Args:
        catalog: Kiro model metadata list.

    Returns:
        Sorted, deduplicated display IDs.

    Raises:
        ModelDiscoveryError: If the catalog has no valid model metadata.
    """
    models = validate_model_catalog(catalog)
    if not models:
        raise ModelDiscoveryError("Model catalog contains no valid model IDs")
    set_known_model_ids([model["modelId"] for model in models])
    return sorted({build_model_display_id(model) for model in models})


def load_state_model_catalog(state_path: Path) -> List[Dict[str, Any]]:
    """Load the union of last-known-good account catalogs from state.json.

    Args:
        state_path: Gateway state file written by ``AccountManager``.

    Returns:
        Valid model metadata from all persisted accounts, deduplicated by their
        complete metadata representation. Missing files return an empty list.

    Raises:
        ModelDiscoveryError: If the state file is unreadable or malformed.
    """
    if not state_path.exists():
        return []
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelDiscoveryError(f"Cannot read model state from {state_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ModelDiscoveryError(f"Model state in {state_path} must be a JSON object")

    accounts = payload.get("accounts", {})
    if not isinstance(accounts, dict):
        raise ModelDiscoveryError(f"Model state in {state_path} has invalid accounts")

    models: List[Dict[str, Any]] = []
    seen_model_ids: set[str] = set()
    for account in accounts.values():
        if not isinstance(account, dict):
            continue
        for model in validate_model_catalog(account.get("model_catalog", [])):
            model_id = model["modelId"]
            if model_id not in seen_model_ids:
                seen_model_ids.add(model_id)
                models.append(model)
    return models


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

        valid_models = validate_model_catalog(models)
        if not valid_models:
            raise ModelDiscoveryError(
                "ListAvailableModels returned no valid model IDs"
            )

        return valid_models
    finally:
        if owns_http_client:
            await client.close()
