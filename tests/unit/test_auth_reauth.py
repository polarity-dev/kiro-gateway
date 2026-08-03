# -*- coding: utf-8 -*-

"""Focused tests for direct-IdC refresh failure classification."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from kiro.auth import KiroAuthManager, RefreshFailureKind, TokenRefreshError


def _write_direct_credentials(path: Path, *, client_expires_at: int) -> None:
    path.write_text(
        json.dumps(
            {
                "authMode": "direct_idc",
                "accessToken": "expired-access",
                "refreshToken": "refresh-secret",
                "expiresAt": "2020-01-01T00:00:00+00:00",
                "profileArn": "arn:aws:codewhisperer:eu-central-1:123:profile/test",
                "region": "eu-west-1",
                "apiRegion": "eu-central-1",
                "clientId": "client-id",
                "clientSecret": "client-secret",
                "clientSecretExpiresAt": client_expires_at,
                "startUrl": "https://example.awsapps.com/start",
            }
        ),
        encoding="utf-8",
    )


def test_invalid_grant_is_sanitized_reauth_required(tmp_path: Path) -> None:
    """Direct IdC invalid_grant is actionable without retaining response secrets."""
    credentials = tmp_path / "credentials.json"
    _write_direct_credentials(credentials, client_expires_at=4102444800)
    manager = KiroAuthManager(creds_file=str(credentials))
    request = httpx.Request("POST", "https://oidc.eu-west-1.amazonaws.com/token")
    response = httpx.Response(
        400,
        request=request,
        json={"error": "invalid_grant", "error_description": "secret-provider-detail"},
    )

    with pytest.raises(TokenRefreshError) as caught:
        manager._raise_direct_idc_http_error(response)

    assert caught.value.kind is RefreshFailureKind.REAUTH_REQUIRED
    assert caught.value.provider_code == "invalidgrant"
    assert "secret-provider-detail" not in str(caught.value)


@pytest.mark.asyncio
async def test_expired_client_registration_requires_reauth_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An expired direct client registration fails before creating an HTTP client."""
    credentials = tmp_path / "credentials.json"
    expired = int(datetime.now(timezone.utc).timestamp()) - 1
    _write_direct_credentials(credentials, client_expires_at=expired)
    manager = KiroAuthManager(creds_file=str(credentials))

    def unexpected_client(*args, **kwargs):
        raise AssertionError("expired registration must not perform network I/O")

    monkeypatch.setattr("kiro.auth.httpx.AsyncClient", unexpected_client)

    with pytest.raises(TokenRefreshError) as caught:
        await manager.get_access_token()

    assert caught.value.kind is RefreshFailureKind.REAUTH_REQUIRED
    assert caught.value.provider_code == "expiredclientregistration"
