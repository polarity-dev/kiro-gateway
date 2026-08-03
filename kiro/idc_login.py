# -*- coding: utf-8 -*-

"""Reusable IAM Identity Center login orchestration and safe events."""

from __future__ import annotations

import json
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import httpx

from kiro.idc_bootstrap import (
    AwsSsoProfile,
    IdcBootstrapError,
    QDeveloperProfile,
    SsoOidcDeviceClient,
    build_credentials,
    list_available_profiles,
    load_aws_sso_profile,
    select_profile,
    write_credentials,
)


@dataclass(frozen=True)
class IdcLoginEvent:
    """One allowlisted event emitted during direct IAM Identity Center login."""

    name: str
    code: Optional[str] = None
    url: Optional[str] = None
    expires_in: Optional[int] = None
    category: Optional[str] = None

    def payload(self) -> dict[str, object]:
        """Return only public fields suitable for an agent event stream."""
        data: dict[str, object] = {"type": self.name}
        if self.code is not None:
            data["code"] = self.code
        if self.url is not None:
            data["url"] = self.url
        if self.expires_in is not None:
            data["expiresIn"] = self.expires_in
        if self.category is not None:
            data["category"] = self.category
        return data


EventSink = Callable[[IdcLoginEvent], None]


def emit_agent_event(event: IdcLoginEvent, *, scope: str = "login") -> None:
    """Write one allowlisted JSONL event for a streaming agent channel."""
    payload = {"event": "KIRO_EVENT", "scope": scope, **event.payload()}
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True), flush=True)


def build_event_sink(
    *,
    agent_events: bool = False,
    no_browser: bool = False,
    scope: str = "login",
) -> EventSink:
    """Build a human or allowlisted agent renderer for IdC events."""
    def render(event: IdcLoginEvent) -> None:
        if event.name == "authorization_required":
            if agent_events:
                emit_agent_event(event, scope=scope)
            else:
                instruction = (
                    "Open the URL above in your browser."
                    if no_browser
                    else "The browser will open after these instructions are visible."
                )
                print(
                    "\nAWS IAM Identity Center authorization required:\n"
                    f"  Code: {event.code}\n"
                    f"  URL:  {event.url}\n\n"
                    "Approve only if the browser code matches the Code above exactly.\n"
                    "If it differs, is missing, or unexpected, cancel and retry.\n"
                    f"{instruction}\n"
                    f"The code expires in {event.expires_in} seconds. "
                    "Waiting for approval; press Ctrl+C to cancel.\n",
                    flush=True,
                )
            if not no_browser:
                try:
                    opened = webbrowser.open(event.url or "")
                except (webbrowser.Error, OSError):
                    opened = False
                if not opened and not agent_events:
                    print(
                        "The browser could not be opened automatically; open the URL above "
                        "and confirm the exact Code shown.",
                        flush=True,
                    )
        elif event.name == "waiting" and agent_events:
            emit_agent_event(event, scope=scope)

    return render


@dataclass(frozen=True)
class IdcLoginResult:
    """Non-secret result metadata from a successful direct login."""

    output: Path
    profile: QDeveloperProfile


async def run_idc_login(
    *,
    aws_profile: str,
    aws_config: Path,
    q_profile: Optional[str],
    output: Path,
    force: bool,
    event_sink: EventSink,
    client: Optional[httpx.AsyncClient] = None,
) -> IdcLoginResult:
    """Authorize, discover the selected Q profile, and persist credentials."""
    target = output.expanduser()
    if target.exists() and not force:
        raise IdcBootstrapError(
            f"Credential output already exists: {target}. Pass --force to replace it."
        )

    sso_profile = load_aws_sso_profile(aws_profile, aws_config)
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    try:
        oidc = SsoOidcDeviceClient(sso_profile.region, http_client)
        registration = await oidc.register_client()
        authorization = await oidc.start_device_authorization(
            registration, sso_profile.start_url
        )
        event_sink(
            IdcLoginEvent(
                "authorization_required",
                code=authorization.user_code,
                url=authorization.verification_uri_complete,
                expires_in=authorization.expires_in,
            )
        )
        event_sink(IdcLoginEvent("waiting"))

        token = await oidc.poll_for_token(registration, authorization)
        profiles = await list_available_profiles(token.access_token, http_client)
        selected_profile = select_profile(profiles, q_profile)
        credentials = build_credentials(
            sso_profile, registration, token, selected_profile
        )
        write_credentials(target, credentials)
        return IdcLoginResult(target, selected_profile)
    finally:
        if owns_client:
            await http_client.aclose()


async def reauthenticate_direct_credentials(
    output: Path,
    *,
    event_sink: EventSink,
    client: Optional[httpx.AsyncClient] = None,
) -> IdcLoginResult:
    """Reauthorize an existing direct-IdC file without changing Q profiles."""
    target = output.expanduser()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IdcBootstrapError(f"Cannot read direct IdC credentials: {exc}") from exc
    required = ("startUrl", "region", "profileArn")
    if not isinstance(data, dict) or any(
        not isinstance(data.get(key), str) or not data[key] for key in required
    ):
        raise IdcBootstrapError(
            "Direct IdC credentials lack startUrl, region, or profileArn; rerun setup"
        )

    profile = AwsSsoProfile(
        str(data.get("awsProfile") or "direct-idc"),
        data["startUrl"],
        data["region"],
    )
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    try:
        oidc = SsoOidcDeviceClient(profile.region, http_client)
        registration = await oidc.register_client()
        authorization = await oidc.start_device_authorization(
            registration, profile.start_url
        )
        event_sink(
            IdcLoginEvent(
                "authorization_required",
                code=authorization.user_code,
                url=authorization.verification_uri_complete,
                expires_in=authorization.expires_in,
            )
        )
        event_sink(IdcLoginEvent("waiting"))
        token = await oidc.poll_for_token(registration, authorization)
        profiles = await list_available_profiles(token.access_token, http_client)
        selected = select_profile(profiles, data["profileArn"])
        write_credentials(
            target,
            build_credentials(profile, registration, token, selected),
        )
        return IdcLoginResult(target, selected)
    finally:
        if owns_client:
            await http_client.aclose()
