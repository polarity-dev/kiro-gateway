# -*- coding: utf-8 -*-

"""Tests for direct IAM Identity Center bootstrap without Kiro."""

from __future__ import annotations

import json
import stat
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from kiro.idc_bootstrap import (
    AwsProfileError,
    AwsSsoProfile,
    BootstrapCredentials,
    DeviceAuthorization,
    DeviceAuthorizationError,
    OIDC_SCOPES,
    OidcRegistration,
    OidcToken,
    ProfileDiscoveryError,
    QDeveloperProfile,
    SsoOidcDeviceClient,
    build_credentials,
    list_available_profiles,
    load_aws_sso_profile,
    select_profile,
    write_credentials,
)


US_ARN_ONE = "arn:aws:codewhisperer:us-east-1:123456789012:profile/abcdefghijkl"
US_ARN_TWO = "arn:aws:codewhisperer:us-east-1:123456789012:profile/mnopqrstuvwx"
US_ARN_PARTIAL = "arn:aws:codewhisperer:us-east-1:123456789012:profile/partialprof1"
EU_ARN_COMPLETE = "arn:aws:codewhisperer:eu-central-1:123456789012:profile/completeprof"
EU_ARN = "arn:aws:codewhisperer:eu-central-1:123456789012:profile/europeprofil"


class StubAsyncClient:
    """Minimal async HTTP client that never touches the network."""

    def __init__(self, handler):
        """Store the deterministic request handler."""
        self._handler = handler

    async def __aenter__(self):
        """Enter the asynchronous context manager."""
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        """Exit without external resources to release."""
        return None

    async def post(self, url: str, *, json=None, headers=None) -> httpx.Response:
        """Build an HTTP request and dispatch it to the local handler."""
        request = httpx.Request("POST", url, json=json, headers=headers)
        response = self._handler(request)
        response.request = request
        return response


def _async_client(handler) -> StubAsyncClient:
    """Build an isolated HTTP client backed by a local handler."""
    return StubAsyncClient(handler)


class TestAwsSsoProfileLoading:
    """AWS shared config parsing and validation tests."""

    def test_loads_modern_sso_session_profile(self, tmp_path: Path) -> None:
        """Modern AWS profiles resolve SSO data through sso_session."""
        config = tmp_path / "config"
        config.write_text(
            "[profile company]\n"
            "sso_session = workforce\n"
            "sso_account_id = 123456789012\n"
            "sso_role_name = Developer\n"
            "\n"
            "[sso-session workforce]\n"
            "sso_start_url = https://example.awsapps.com/start/\n"
            "sso_region = eu-west-1\n",
            encoding="utf-8",
        )

        profile = load_aws_sso_profile("company", config)

        assert profile == AwsSsoProfile(
            name="company",
            start_url="https://example.awsapps.com/start",
            region="eu-west-1",
        )

    def test_loads_legacy_inline_default_profile(self, tmp_path: Path) -> None:
        """Legacy inline SSO settings remain supported for default."""
        config = tmp_path / "config"
        config.write_text(
            "[default]\nsso_start_url=https://example.awsapps.com/start\n"
            "sso_region=us-east-1\n",
            encoding="utf-8",
        )

        profile = load_aws_sso_profile("default", config)

        assert profile.region == "us-east-1"
        assert profile.start_url == "https://example.awsapps.com/start"

    @pytest.mark.parametrize(
        "content, message",
        [
            ("[profile company]\nsso_session=missing\n", r"missing \[sso-session missing\]"),
            ("[profile company]\nsso_start_url=http://invalid\nsso_region=us-east-1\n", "HTTPS"),
            ("[profile company]\nsso_start_url=https://example.awsapps.com/start\nsso_region=bad\n", "valid AWS region"),
        ],
    )
    def test_rejects_incomplete_or_unsafe_profiles(
        self, tmp_path: Path, content: str, message: str
    ) -> None:
        """Malformed profile configuration fails with actionable guidance."""
        config = tmp_path / "config"
        config.write_text(content, encoding="utf-8")

        with pytest.raises(AwsProfileError, match=message):
            load_aws_sso_profile("company", config)


class TestSsoOidcDeviceClient:
    """AWS SSO OIDC wire contract and polling tests."""

    @pytest.mark.asyncio
    async def test_register_and_start_device_authorization(self) -> None:
        """Registration and device authorization use AWS JSON field names."""
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/client/register":
                return httpx.Response(
                    200,
                    json={
                        "clientId": "client-id",
                        "clientSecret": "client-secret",
                        "clientSecretExpiresAt": 2000000000,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "deviceCode": "device-code",
                    "userCode": "ABCD-EFGH",
                    "verificationUri": "https://device.sso.example/",
                    "verificationUriComplete": "https://device.sso.example/?user_code=ABCD-EFGH",
                    "expiresIn": 600,
                    "interval": 5,
                },
            )

        async with _async_client(handler) as client:
            oidc = SsoOidcDeviceClient("eu-west-1", client)
            registration = await oidc.register_client()
            authorization = await oidc.start_device_authorization(
                registration, "https://example.awsapps.com/start"
            )

        assert registration.client_id == "client-id"
        assert authorization.user_code == "ABCD-EFGH"
        assert requests[0].url == "https://oidc.eu-west-1.amazonaws.com/client/register"
        assert json.loads(requests[0].content) == {
            "clientName": "Kiro Gateway",
            "clientType": "public",
            "scopes": list(OIDC_SCOPES),
        }
        assert json.loads(requests[1].content)["startUrl"] == "https://example.awsapps.com/start"

    @pytest.mark.asyncio
    async def test_registration_rejects_non_object_json_response(self) -> None:
        """A successful HTTP response with the wrong JSON shape fails cleanly."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=["unexpected"])

        async with _async_client(handler) as client:
            oidc = SsoOidcDeviceClient("us-east-1", client)
            with pytest.raises(DeviceAuthorizationError, match="invalid response object"):
                await oidc.register_client()

    @pytest.mark.asyncio
    async def test_device_authorization_rejects_invalid_numeric_fields(self) -> None:
        """Malformed expiry or interval values never reach the polling loop."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "deviceCode": "device",
                    "userCode": "code",
                    "verificationUri": "https://verify",
                    "verificationUriComplete": "https://verify?code=code",
                    "expiresIn": "not-an-integer",
                    "interval": 5,
                },
            )

        async with _async_client(handler) as client:
            oidc = SsoOidcDeviceClient("us-east-1", client)
            with pytest.raises(DeviceAuthorizationError, match="invalid expiresIn"):
                await oidc.start_device_authorization(
                    OidcRegistration("client", "secret", None),
                    "https://example.awsapps.com/start",
                )

    @pytest.mark.asyncio
    async def test_poll_handles_pending_and_slow_down(self) -> None:
        """Polling honors pending and increases delay after slow_down."""
        responses = iter(
            [
                httpx.Response(400, json={"error": "authorization_pending"}),
                httpx.Response(400, json={"error": "slow_down"}),
                httpx.Response(
                    200,
                    json={
                        "accessToken": "access-token",
                        "refreshToken": "refresh-token",
                        "expiresIn": 3600,
                    },
                ),
            ]
        )
        sleeps: list[float] = []
        clock = [0.0]

        def handler(request: httpx.Request) -> httpx.Response:
            response = next(responses)
            response.request = request
            return response

        async def sleep(delay: float) -> None:
            sleeps.append(delay)
            clock[0] += delay

        async with _async_client(handler) as client:
            oidc = SsoOidcDeviceClient("us-east-1", client)
            token = await oidc.poll_for_token(
                OidcRegistration("client", "secret", None),
                DeviceAuthorization("device", "code", "https://verify", "https://verify", 60, 2),
                sleep=sleep,
                monotonic=lambda: clock[0],
            )

        assert token == OidcToken("access-token", "refresh-token", 3600)
        assert sleeps == [2, 2, 7]

    @pytest.mark.asyncio
    async def test_poll_stops_at_device_code_expiration(self) -> None:
        """Polling never sends a token request after the device code expires."""
        requests: list[httpx.Request] = []
        clock = [0.0]

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(400, json={"error": "authorization_pending"})

        async def sleep(delay: float) -> None:
            clock[0] += delay

        async with _async_client(handler) as client:
            oidc = SsoOidcDeviceClient("us-east-1", client)
            with pytest.raises(DeviceAuthorizationError, match="timed out"):
                await oidc.poll_for_token(
                    OidcRegistration("client", "secret", None),
                    DeviceAuthorization("device", "code", "https://verify", "https://verify", 3, 5),
                    sleep=sleep,
                    monotonic=lambda: clock[0],
                )

        assert requests == []
        assert clock[0] == 3

    @pytest.mark.asyncio
    async def test_poll_rejects_denial_without_leaking_body(self) -> None:
        """A denied login raises a concise actionable error."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "access_denied", "secret": "do-not-log"})

        clock = [0.0]

        async def sleep(delay: float) -> None:
            clock[0] += delay

        async with _async_client(handler) as client:
            oidc = SsoOidcDeviceClient("us-east-1", client)
            with pytest.raises(DeviceAuthorizationError, match="denied") as error:
                await oidc.poll_for_token(
                    OidcRegistration("client", "secret", None),
                    DeviceAuthorization("device", "code", "https://verify", "https://verify", 10, 1),
                    sleep=sleep,
                    monotonic=lambda: clock[0],
                )

        assert "do-not-log" not in str(error.value)


class TestProfileDiscovery:
    """Bearer profile discovery, pagination, and selection tests."""

    @pytest.mark.asyncio
    async def test_discovers_paginated_profiles_and_deduplicates(self) -> None:
        """All pages and regions are read with the verified bearer contract."""
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            body = json.loads(request.content)
            if request.url.host == "q.us-east-1.amazonaws.com" and "nextToken" not in body:
                return httpx.Response(
                    200,
                    json={
                        "profiles": [{"arn": US_ARN_ONE, "profileName": "One"}],
                        "nextToken": "page-2",
                    },
                )
            if request.url.host == "q.us-east-1.amazonaws.com":
                return httpx.Response(
                    200,
                    json={"profiles": [{"arn": US_ARN_TWO, "profileName": "Two"}]},
                )
            return httpx.Response(
                200,
                json={"profiles": [{"arn": US_ARN_ONE, "profileName": "One"}]},
            )

        async with _async_client(handler) as client:
            profiles = await list_available_profiles("access-token", client)

        assert [profile.arn for profile in profiles] == [US_ARN_ONE, US_ARN_TWO]
        assert profiles[0].region == "us-east-1"
        assert all(request.headers["authorization"] == "Bearer access-token" for request in requests)
        assert all(
            request.headers["x-amz-target"]
            == "AmazonCodeWhispererService.ListAvailableProfiles"
            for request in requests
        )
        assert json.loads(requests[1].content)["nextToken"] == "page-2"

    @pytest.mark.asyncio
    async def test_discards_partial_region_when_later_page_fails(self) -> None:
        """A failed later page cannot leave an incomplete regional profile set."""
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if request.url.host == "q.us-east-1.amazonaws.com":
                if "nextToken" not in body:
                    return httpx.Response(
                        200,
                        json={
                            "profiles": [{"arn": US_ARN_PARTIAL, "profileName": "Partial"}],
                            "nextToken": "next",
                        },
                    )
                return httpx.Response(503, json={"__type": "InternalServerException"})
            return httpx.Response(
                200,
                json={"profiles": [{"arn": EU_ARN_COMPLETE, "profileName": "Complete"}]},
            )

        async with _async_client(handler) as client:
            profiles = await list_available_profiles("access-token", client)

        assert profiles == [QDeveloperProfile(EU_ARN_COMPLETE, "Complete", "eu-central-1")]

    @pytest.mark.asyncio
    async def test_tolerates_one_failed_region_when_another_has_profiles(self) -> None:
        """A regional outage does not hide profiles returned elsewhere."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host.startswith("q.us-east-1"):
                return httpx.Response(503, json={"__type": "InternalServerException"})
            return httpx.Response(
                200,
                json={"profiles": [{"arn": EU_ARN, "profileName": "Europe"}]},
            )

        async with _async_client(handler) as client:
            profiles = await list_available_profiles("access-token", client)

        assert profiles == [QDeveloperProfile(EU_ARN, "Europe", "eu-central-1")]

    @pytest.mark.asyncio
    async def test_reports_missing_subscription_when_all_regions_are_empty(self) -> None:
        """An assigned user with no profiles receives administrator guidance."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"profiles": []})

        async with _async_client(handler) as client:
            with pytest.raises(ProfileDiscoveryError, match="administrator"):
                await list_available_profiles("access-token", client)

    def test_selects_only_profile_or_explicit_arn(self) -> None:
        """Single profiles auto-select; multiple profiles require a selector."""
        first = QDeveloperProfile("arn:first", "Shared", "us-east-1")
        second = QDeveloperProfile("arn:second", "Shared", "eu-central-1")

        assert select_profile([first]) is first
        assert select_profile([first, second], "arn:second") is second
        with pytest.raises(ProfileDiscoveryError, match="Multiple"):
            select_profile([first, second])
        with pytest.raises(ProfileDiscoveryError, match="ambiguous"):
            select_profile([first, second], "Shared")


class TestCredentialPersistence:
    """Credential compatibility and secret-file safety tests."""

    def test_builds_gateway_compatible_regions_and_expiration(self) -> None:
        """SSO and API regions remain separate in the persisted document."""
        credentials = build_credentials(
            AwsSsoProfile("company", "https://example.awsapps.com/start", "eu-west-1"),
            OidcRegistration("client", "secret", 2000000000),
            OidcToken("access", "refresh", 3600),
            QDeveloperProfile("arn:profile", "Profile", "us-east-1"),
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        assert credentials.region == "eu-west-1"
        assert credentials.apiRegion == "us-east-1"
        assert credentials.expiresAt == "2026-01-01T01:00:00+00:00"
        assert credentials.scopes == list(OIDC_SCOPES)

    def test_writes_atomically_with_owner_only_permissions(self, tmp_path: Path) -> None:
        """Persisted access, refresh, and client secrets are protected by mode 0600."""
        path = tmp_path / "nested" / "credentials.json"
        credentials = BootstrapCredentials(
            accessToken="access",
            refreshToken="refresh",
            expiresAt="2026-01-01T01:00:00+00:00",
            profileArn="arn:profile",
            region="eu-west-1",
            apiRegion="us-east-1",
            clientId="client",
            clientSecret="secret",
            scopes=list(OIDC_SCOPES),
            startUrl="https://example.awsapps.com/start",
        )

        write_credentials(path, credentials)

        assert json.loads(path.read_text(encoding="utf-8"))["clientSecret"] == "secret"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert not list(path.parent.glob("*.tmp"))

    def test_rejects_symlink_output(self, tmp_path: Path) -> None:
        """Credential persistence cannot be redirected through a symlink."""
        destination = tmp_path / "destination.json"
        destination.write_text("safe", encoding="utf-8")
        link = tmp_path / "credentials.json"
        link.symlink_to(destination)
        credentials = BootstrapCredentials(
            "access", "refresh", "expires", "arn", "eu-west-1", "us-east-1",
            "client", "secret", list(OIDC_SCOPES), "https://example.awsapps.com/start"
        )

        with pytest.raises(Exception, match="symlink"):
            write_credentials(link, credentials)

        assert destination.read_text(encoding="utf-8") == "safe"
