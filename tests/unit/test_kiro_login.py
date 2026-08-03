# -*- coding: utf-8 -*-

"""End-to-end command tests for direct IAM Identity Center login."""

from __future__ import annotations

import argparse
import json
import stat
from pathlib import Path

import httpx
import pytest

from kiro.idc_bootstrap import OidcToken, SsoOidcDeviceClient
from scripts.kiro_login import login


class StubAsyncClient:
    """Deterministic async client used to isolate command tests."""

    def __init__(self) -> None:
        """Initialize captured requests."""
        self.requests: list[httpx.Request] = []

    async def post(self, url: str, *, json=None, headers=None) -> httpx.Response:
        """Return one response for each bootstrap operation."""
        request = httpx.Request("POST", url, json=json, headers=headers)
        self.requests.append(request)
        if request.url.path == "/client/register":
            response = httpx.Response(
                200,
                json={
                    "clientId": "client-id",
                    "clientSecret": "client-secret",
                    "clientSecretExpiresAt": 2000000000,
                },
            )
        elif request.url.path == "/device_authorization":
            response = httpx.Response(
                200,
                json={
                    "deviceCode": "device-code",
                    "userCode": "ABCD-EFGH",
                    "verificationUri": "https://device.sso.example/",
                    "verificationUriComplete": "https://device.sso.example/?user_code=ABCD-EFGH",
                    "expiresIn": 600,
                    "interval": 1,
                },
            )
        else:
            response = httpx.Response(
                200,
                json={
                    "profiles": [
                        {
                            "arn": "arn:aws:codewhisperer:us-east-1:123456789012:profile/abcdefghijkl",
                            "profileName": "Engineering",
                        }
                    ]
                    if request.url.host == "q.us-east-1.amazonaws.com"
                    else []
                },
            )
        response.request = request
        return response


@pytest.mark.asyncio
async def test_login_writes_gateway_credentials_without_kiro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AWS config, OIDC, profile discovery, and persistence form one working flow."""
    aws_config = tmp_path / "aws-config"
    aws_config.write_text(
        "[profile company]\n"
        "sso_session=workforce\n"
        "[sso-session workforce]\n"
        "sso_start_url=https://example.awsapps.com/start\n"
        "sso_region=eu-west-1\n",
        encoding="utf-8",
    )
    output = tmp_path / "credentials.json"
    args = argparse.Namespace(
        aws_profile="company",
        aws_config=aws_config,
        q_profile=None,
        output=output,
        no_browser=True,
    )
    client = StubAsyncClient()

    async def token_result(self, registration, authorization):
        return OidcToken("access-token", "refresh-token", 3600)

    monkeypatch.setattr(SsoOidcDeviceClient, "poll_for_token", token_result)

    result = await login(args, client=client)

    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["accessToken"] == "access-token"
    assert data["refreshToken"] == "refresh-token"
    assert data["clientId"] == "client-id"
    assert data["profileArn"].endswith("profile/abcdefghijkl")
    assert data["region"] == "eu-west-1"
    assert data["apiRegion"] == "us-east-1"
    assert stat.S_IMODE(result.stat().st_mode) == 0o600
    profile_requests = [
        request
        for request in client.requests
        if request.url.host and request.url.host.startswith("q.")
    ]
    assert len(profile_requests) == 2
    assert all(request.headers["authorization"] == "Bearer access-token" for request in profile_requests)
