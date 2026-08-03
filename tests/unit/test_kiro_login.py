# -*- coding: utf-8 -*-

"""End-to-end command tests for direct IAM Identity Center login."""

from __future__ import annotations

import argparse
import io
import json
import stat
import sys
from pathlib import Path

import httpx
import pytest

from kiro import idc_login
from kiro.idc_bootstrap import IdcBootstrapError, OidcToken, SsoOidcDeviceClient
from scripts import kiro_login
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
        force=False,
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


@pytest.mark.asyncio
async def test_login_refuses_existing_output_before_network(
    tmp_path: Path,
) -> None:
    """Existing credentials require explicit --force before any OIDC request."""
    aws_config = tmp_path / "aws-config"
    aws_config.write_text(
        "[default]\nsso_start_url=https://example.awsapps.com/start\n"
        "sso_region=us-east-1\n",
        encoding="utf-8",
    )
    output = tmp_path / "credentials.json"
    output.write_text("original", encoding="utf-8")
    args = argparse.Namespace(
        aws_profile="default",
        aws_config=aws_config,
        q_profile=None,
        output=output,
        no_browser=True,
        force=False,
    )
    client = StubAsyncClient()

    with pytest.raises(IdcBootstrapError, match="--force"):
        await login(args, client=client)

    assert output.read_text(encoding="utf-8") == "original"
    assert client.requests == []


class TrackingStdout(io.StringIO):
    """Capture output and record every explicit flush."""

    def __init__(self, events: list[str]) -> None:
        """Initialize the stream with a shared event log."""
        super().__init__()
        self.events = events

    def flush(self) -> None:
        """Record the flush before delegating to StringIO."""
        self.events.append("flush")
        super().flush()


def _login_args(tmp_path: Path, *, no_browser: bool) -> argparse.Namespace:
    """Create deterministic direct-login arguments."""
    aws_config = tmp_path / "aws-config"
    aws_config.write_text(
        "[profile company]\n"
        "sso_start_url=https://example.awsapps.com/start\n"
        "sso_region=eu-west-1\n",
        encoding="utf-8",
    )
    return argparse.Namespace(
        aws_profile="company",
        aws_config=aws_config,
        q_profile=None,
        output=tmp_path / "credentials.json",
        no_browser=no_browser,
        force=False,
    )


@pytest.mark.asyncio
async def test_login_flushes_exact_approval_details_before_browser_and_polling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user sees the exact code before the browser or token polling starts."""
    events: list[str] = []
    stdout = TrackingStdout(events)
    monkeypatch.setattr(sys, "stdout", stdout)

    def open_browser(url: str) -> bool:
        assert events == ["flush"]
        assert "Code: ABCD-EFGH" in stdout.getvalue()
        assert "URL:  https://device.sso.example/?user_code=ABCD-EFGH" in stdout.getvalue()
        events.append("browser")
        return True

    async def token_result(self, registration, authorization):
        assert events == ["flush", "browser"]
        assert authorization.user_code == "ABCD-EFGH"
        events.append("poll")
        return OidcToken("access-token", "refresh-token", 3600)

    monkeypatch.setattr(idc_login.webbrowser, "open", open_browser)
    monkeypatch.setattr(SsoOidcDeviceClient, "poll_for_token", token_result)

    await login(_login_args(tmp_path, no_browser=False), client=StubAsyncClient())

    text = stdout.getvalue()
    assert events[:3] == ["flush", "browser", "poll"]
    assert "matches the Code above exactly" in text
    assert "expires in 600 seconds" in text
    for secret in ("device-code", "client-secret", "access-token", "refresh-token"):
        assert secret not in text


@pytest.mark.asyncio
async def test_no_browser_flushes_instructions_before_polling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manual browser mode keeps the same visible approval gate."""
    events: list[str] = []
    stdout = TrackingStdout(events)
    monkeypatch.setattr(sys, "stdout", stdout)

    def unexpected_browser(url: str) -> bool:
        raise AssertionError("--no-browser must not open a browser")

    async def token_result(self, registration, authorization):
        assert events == ["flush"]
        assert "Open the URL above in your browser." in stdout.getvalue()
        events.append("poll")
        return OidcToken("access-token", "refresh-token", 3600)

    monkeypatch.setattr(idc_login.webbrowser, "open", unexpected_browser)
    monkeypatch.setattr(SsoOidcDeviceClient, "poll_for_token", token_result)

    await login(_login_args(tmp_path, no_browser=True), client=StubAsyncClient())

    assert events[:2] == ["flush", "poll"]


@pytest.mark.asyncio
async def test_browser_open_failure_prints_and_flushes_manual_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A browser-launch failure leaves visible manual instructions."""
    events: list[str] = []
    stdout = TrackingStdout(events)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(idc_login.webbrowser, "open", lambda url: False)

    async def token_result(self, registration, authorization):
        assert events == ["flush", "flush"]
        assert "could not be opened automatically" in stdout.getvalue()
        events.append("poll")
        return OidcToken("access-token", "refresh-token", 3600)

    monkeypatch.setattr(SsoOidcDeviceClient, "poll_for_token", token_result)

    await login(_login_args(tmp_path, no_browser=False), client=StubAsyncClient())

    assert events[:3] == ["flush", "flush", "poll"]


@pytest.mark.asyncio
async def test_browser_open_exception_uses_manual_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A browser-controller exception does not discard the active device code."""
    def fail_to_open(url: str) -> bool:
        raise idc_login.webbrowser.Error("no browser controller")

    async def token_result(self, registration, authorization):
        return OidcToken("access-token", "refresh-token", 3600)

    monkeypatch.setattr(idc_login.webbrowser, "open", fail_to_open)
    monkeypatch.setattr(SsoOidcDeviceClient, "poll_for_token", token_result)

    await login(_login_args(tmp_path, no_browser=False), client=StubAsyncClient())


@pytest.mark.asyncio
async def test_agent_events_are_allowlisted_and_ordered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Agent mode emits only public JSONL before polling and no human output."""
    args = _login_args(tmp_path, no_browser=True)
    args.agent_events = True

    async def token_result(self, registration, authorization):
        lines = capsys.readouterr().out.splitlines()
        payloads = [json.loads(line) for line in lines]
        assert [payload["type"] for payload in payloads] == [
            "authorization_required",
            "waiting",
        ]
        assert payloads[0]["code"] == "ABCD-EFGH"
        assert payloads[0]["url"].endswith("user_code=ABCD-EFGH")
        return OidcToken("access-token", "refresh-token", 3600)

    monkeypatch.setattr(SsoOidcDeviceClient, "poll_for_token", token_result)

    await login(args, client=StubAsyncClient())

    output = capsys.readouterr().out
    for secret in ("device-code", "client-secret", "access-token", "refresh-token"):
        assert secret not in output


def test_agent_main_emits_cancelled_terminal_event(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Agent cancellation has one sanitized terminal event and exit 130."""
    def interrupt(coroutine):
        coroutine.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(kiro_login.asyncio, "run", interrupt)

    assert kiro_login.main(["--agent-events"]) == 130
    payloads = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert payloads == [
        {
            "category": "cancelled",
            "event": "KIRO_EVENT",
            "scope": "login",
            "type": "cancelled",
        }
    ]


def test_main_preserves_keyboard_interrupt_as_cancellation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl+C remains distinguishable from an authentication failure."""
    def interrupt(coroutine):
        coroutine.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(kiro_login.asyncio, "run", interrupt)

    assert kiro_login.main([]) == 130
    assert "Login cancelled." in capsys.readouterr().err
