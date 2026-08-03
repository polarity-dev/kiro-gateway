# -*- coding: utf-8 -*-

"""IAM Identity Center bootstrap for Kiro Gateway.

This module implements the AWS SSO OIDC device authorization flow and the
Amazon Q Developer ``ListAvailableProfiles`` discovery operation. It produces
credentials that can be consumed directly by :class:`kiro.auth.KiroAuthManager`.
"""

from __future__ import annotations

import asyncio
import configparser
import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional, Sequence
from urllib.parse import urlparse

import httpx


OIDC_SCOPES: tuple[str, ...] = (
    "codewhisperer:completions",
    "codewhisperer:analysis",
    "codewhisperer:conversations",
)
OIDC_CLIENT_NAME = "Kiro Gateway"
OIDC_CLIENT_TYPE = "public"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
PROFILE_TARGET = "AmazonCodeWhispererService.ListAvailableProfiles"
PROFILE_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("us-east-1", "https://q.us-east-1.amazonaws.com/"),
    ("eu-central-1", "https://q.eu-central-1.amazonaws.com/"),
)
_REGION_PATTERN = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-\d+$")


class IdcBootstrapError(RuntimeError):
    """Base error for actionable IAM Identity Center bootstrap failures."""


class AwsProfileError(IdcBootstrapError):
    """Raised when an AWS CLI profile lacks usable IAM Identity Center data."""


class DeviceAuthorizationError(IdcBootstrapError):
    """Raised when the OIDC device authorization flow cannot complete."""


class ProfileDiscoveryError(IdcBootstrapError):
    """Raised when Amazon Q profile discovery fails or returns invalid data."""


@dataclass(frozen=True)
class AwsSsoProfile:
    """IAM Identity Center settings resolved from an AWS CLI profile."""

    name: str
    start_url: str
    region: str


@dataclass(frozen=True)
class OidcRegistration:
    """AWS SSO OIDC public-client registration."""

    client_id: str
    client_secret: str
    client_secret_expires_at: Optional[int]


@dataclass(frozen=True)
class DeviceAuthorization:
    """Device authorization instructions returned by AWS SSO OIDC."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class OidcToken:
    """Access and refresh tokens returned by AWS SSO OIDC."""

    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True)
class QDeveloperProfile:
    """An Amazon Q Developer profile available to the signed-in user."""

    arn: str
    profile_name: str
    region: str
    description: Optional[str] = None


@dataclass(frozen=True)
class BootstrapCredentials:
    """Credential document persisted for ``KiroAuthManager``."""

    accessToken: str
    refreshToken: str
    expiresAt: str
    profileArn: str
    region: str
    apiRegion: str
    clientId: str
    clientSecret: str
    scopes: list[str]
    startUrl: str
    clientSecretExpiresAt: Optional[int] = None


def _validate_region(region: str, source: str) -> str:
    """Validate and return an AWS region name.

    Args:
        region: Candidate AWS region.
        source: Human-readable setting source for error messages.

    Returns:
        The validated region.

    Raises:
        AwsProfileError: If the region is empty or malformed.
    """
    value = region.strip()
    if not _REGION_PATTERN.fullmatch(value):
        raise AwsProfileError(f"{source} must be a valid AWS region, got {region!r}")
    return value


def _profile_region_from_arn(arn: str) -> str:
    """Extract and validate the service region embedded in a profile ARN."""
    parts = arn.split(":")
    if len(parts) < 6 or parts[0] != "arn" or parts[2] not in {"codewhisperer", "transform"}:
        raise ProfileDiscoveryError(f"ListAvailableProfiles returned an invalid profile ARN: {arn!r}")
    region = parts[3]
    if not _REGION_PATTERN.fullmatch(region):
        raise ProfileDiscoveryError(f"ListAvailableProfiles returned an invalid profile ARN region: {arn!r}")
    return region


def _validate_start_url(start_url: str, source: str) -> str:
    """Validate and normalize an IAM Identity Center start URL.

    Args:
        start_url: Candidate start URL.
        source: Human-readable setting source for error messages.

    Returns:
        The normalized HTTPS URL without a trailing slash.

    Raises:
        AwsProfileError: If the URL is not an absolute HTTPS URL.
    """
    value = start_url.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise AwsProfileError(f"{source} must be an absolute HTTPS URL")
    return value


def load_aws_sso_profile(
    profile_name: str,
    config_path: Path = Path("~/.aws/config"),
) -> AwsSsoProfile:
    """Resolve IAM Identity Center settings from an AWS CLI profile.

    Both modern ``sso_session`` references and legacy inline ``sso_start_url`` /
    ``sso_region`` profile fields are supported.

    Args:
        profile_name: AWS CLI profile name, including ``default`` when desired.
        config_path: AWS shared config file path.

    Returns:
        Resolved IAM Identity Center profile settings.

    Raises:
        AwsProfileError: If the file, profile, session, URL, or region is invalid.
    """
    path = config_path.expanduser()
    parser = configparser.RawConfigParser()
    try:
        loaded = parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError) as exc:
        raise AwsProfileError(f"Cannot read AWS config file {path}: {exc}") from exc
    if not loaded:
        raise AwsProfileError(f"AWS config file not found: {path}")

    section = "default" if profile_name == "default" else f"profile {profile_name}"
    if not parser.has_section(section):
        raise AwsProfileError(f"AWS profile {profile_name!r} was not found in {path}")

    values = parser[section]
    session_name = values.get("sso_session", "").strip()
    if session_name:
        session_section = f"sso-session {session_name}"
        if not parser.has_section(session_section):
            raise AwsProfileError(
                f"AWS profile {profile_name!r} references missing [{session_section}]"
            )
        session_values = parser[session_section]
        start_url = session_values.get("sso_start_url", values.get("sso_start_url", ""))
        region = session_values.get("sso_region", values.get("sso_region", ""))
    else:
        start_url = values.get("sso_start_url", "")
        region = values.get("sso_region", "")

    if not start_url or not region:
        raise AwsProfileError(
            f"AWS profile {profile_name!r} must define sso_start_url and sso_region "
            "directly or through sso_session"
        )
    return AwsSsoProfile(
        name=profile_name,
        start_url=_validate_start_url(start_url, "sso_start_url"),
        region=_validate_region(region, "sso_region"),
    )


def _oidc_base_url(region: str) -> str:
    """Build the regional AWS SSO OIDC endpoint."""
    return f"https://oidc.{_validate_region(region, 'SSO region')}.amazonaws.com"


def _error_code(response: httpx.Response) -> str:
    """Extract a normalized AWS JSON error code without exposing response data."""
    try:
        data = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "unknown_error"
    if not isinstance(data, dict):
        return "unknown_error"
    raw = data.get("error") or data.get("__type") or data.get("code") or "unknown_error"
    return str(raw).split("#")[-1].split(":")[-1].lower()


def _response_object(
    response: httpx.Response,
    operation: str,
    error_type: type[IdcBootstrapError],
) -> dict:
    """Decode an AWS JSON response without exposing its potentially sensitive body."""
    try:
        data = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise error_type(f"{operation} returned an invalid JSON response") from exc
    if not isinstance(data, dict):
        raise error_type(f"{operation} returned an invalid response object")
    return data


def _positive_integer(
    data: dict,
    field: str,
    default: int,
    operation: str,
) -> int:
    """Read a positive integer from an AWS response with an actionable error."""
    value = data.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DeviceAuthorizationError(
            f"{operation} response contains an invalid {field}"
        )
    return value


def _require_string(data: dict, field: str, operation: str) -> str:
    """Read a required non-empty string from an AWS response."""
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise DeviceAuthorizationError(f"{operation} response is missing {field}")
    return value


class SsoOidcDeviceClient:
    """Minimal asynchronous client for the AWS SSO OIDC device flow."""

    def __init__(self, region: str, client: httpx.AsyncClient):
        """Initialize the OIDC client.

        Args:
            region: IAM Identity Center region.
            client: Injected HTTP client.
        """
        self.region = _validate_region(region, "SSO region")
        self.client = client
        self.base_url = _oidc_base_url(region)

    async def register_client(
        self,
        scopes: Sequence[str] = OIDC_SCOPES,
    ) -> OidcRegistration:
        """Register a public OIDC client for the requested Amazon Q scopes."""
        response = await self.client.post(
            f"{self.base_url}/client/register",
            json={
                "clientName": OIDC_CLIENT_NAME,
                "clientType": OIDC_CLIENT_TYPE,
                "scopes": list(scopes),
            },
            headers={"Content-Type": "application/json"},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DeviceAuthorizationError(
                f"OIDC client registration failed ({response.status_code}, {_error_code(response)})"
            ) from exc
        data = _response_object(response, "RegisterClient", DeviceAuthorizationError)
        expires_at = data.get("clientSecretExpiresAt")
        return OidcRegistration(
            client_id=_require_string(data, "clientId", "RegisterClient"),
            client_secret=_require_string(data, "clientSecret", "RegisterClient"),
            client_secret_expires_at=expires_at if isinstance(expires_at, int) else None,
        )

    async def start_device_authorization(
        self,
        registration: OidcRegistration,
        start_url: str,
    ) -> DeviceAuthorization:
        """Start device authorization for an IAM Identity Center portal."""
        response = await self.client.post(
            f"{self.base_url}/device_authorization",
            json={
                "clientId": registration.client_id,
                "clientSecret": registration.client_secret,
                "startUrl": _validate_start_url(start_url, "start URL"),
            },
            headers={"Content-Type": "application/json"},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DeviceAuthorizationError(
                f"Device authorization failed ({response.status_code}, {_error_code(response)})"
            ) from exc
        data = _response_object(response, "StartDeviceAuthorization", DeviceAuthorizationError)
        complete_uri = data.get("verificationUriComplete") or data.get("verificationUri")
        if not isinstance(complete_uri, str) or not complete_uri:
            raise DeviceAuthorizationError(
                "StartDeviceAuthorization response is missing verificationUriComplete"
            )
        return DeviceAuthorization(
            device_code=_require_string(data, "deviceCode", "StartDeviceAuthorization"),
            user_code=_require_string(data, "userCode", "StartDeviceAuthorization"),
            verification_uri=_require_string(data, "verificationUri", "StartDeviceAuthorization"),
            verification_uri_complete=complete_uri,
            expires_in=_positive_integer(data, "expiresIn", 600, "StartDeviceAuthorization"),
            interval=_positive_integer(data, "interval", 5, "StartDeviceAuthorization"),
        )

    async def poll_for_token(
        self,
        registration: OidcRegistration,
        authorization: DeviceAuthorization,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> OidcToken:
        """Poll CreateToken until the user authorizes or the device code expires."""
        deadline = monotonic() + authorization.expires_in
        interval = authorization.interval
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            await sleep(min(interval, remaining))
            if monotonic() >= deadline:
                break
            response = await self.client.post(
                f"{self.base_url}/token",
                json={
                    "clientId": registration.client_id,
                    "clientSecret": registration.client_secret,
                    "deviceCode": authorization.device_code,
                    "grantType": DEVICE_GRANT_TYPE,
                },
                headers={"Content-Type": "application/json"},
            )
            if response.is_success:
                data = _response_object(response, "CreateToken", DeviceAuthorizationError)
                refresh_token = _require_string(data, "refreshToken", "CreateToken")
                return OidcToken(
                    access_token=_require_string(data, "accessToken", "CreateToken"),
                    refresh_token=refresh_token,
                    expires_in=_positive_integer(data, "expiresIn", 3600, "CreateToken"),
                )

            code = _error_code(response)
            if code in {"authorization_pending", "authorizationpendingexception"}:
                continue
            if code in {"slow_down", "slowdownexception"}:
                interval += 5
                continue
            if code in {"access_denied", "accessdeniedexception"}:
                raise DeviceAuthorizationError("Device authorization was denied")
            if code in {"expired_token", "expiredtokenexception"}:
                raise DeviceAuthorizationError("Device authorization code expired; run login again")
            raise DeviceAuthorizationError(
                f"CreateToken failed ({response.status_code}, {code})"
            )
        raise DeviceAuthorizationError("Device authorization timed out before approval")


async def list_available_profiles(
    access_token: str,
    client: httpx.AsyncClient,
    endpoints: Sequence[tuple[str, str]] = PROFILE_ENDPOINTS,
) -> list[QDeveloperProfile]:
    """Discover all Amazon Q Developer profiles available to an IdC token.

    Regional failures are tolerated when at least one region returns profiles,
    matching the AWS Toolkit behavior. Pagination loops and malformed responses
    fail safely.
    """
    if not access_token:
        raise ProfileDiscoveryError("An access token is required for profile discovery")

    profiles: list[QDeveloperProfile] = []
    failures: list[str] = []
    for region, endpoint in endpoints:
        next_token: Optional[str] = None
        seen_tokens: set[str] = set()
        regional_profiles: list[QDeveloperProfile] = []
        try:
            while True:
                payload: dict[str, object] = {"maxResults": 10}
                if next_token:
                    if next_token in seen_tokens:
                        raise ProfileDiscoveryError(
                            f"Profile discovery returned a repeated pagination token in {region}"
                        )
                    seen_tokens.add(next_token)
                    payload["nextToken"] = next_token

                response = await client.post(
                    endpoint,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/x-amz-json-1.0",
                        "X-Amz-Target": PROFILE_TARGET,
                    },
                )
                response.raise_for_status()
                data = _response_object(response, "ListAvailableProfiles", ProfileDiscoveryError)
                raw_profiles = data.get("profiles")
                if not isinstance(raw_profiles, list):
                    raise ProfileDiscoveryError(
                        f"ListAvailableProfiles response in {region} is missing profiles"
                    )
                for raw in raw_profiles:
                    if not isinstance(raw, dict):
                        raise ProfileDiscoveryError(
                            f"ListAvailableProfiles returned an invalid profile in {region}"
                        )
                    arn = raw.get("arn")
                    name = raw.get("profileName")
                    if not isinstance(arn, str) or not arn or not isinstance(name, str) or not name:
                        raise ProfileDiscoveryError(
                            f"ListAvailableProfiles returned an incomplete profile in {region}"
                        )
                    description = raw.get("description")
                    regional_profiles.append(
                        QDeveloperProfile(
                            arn=arn,
                            profile_name=name,
                            region=_profile_region_from_arn(arn),
                            description=description if isinstance(description, str) else None,
                        )
                    )
                token = data.get("nextToken")
                if token is None:
                    break
                if not isinstance(token, str) or not token:
                    raise ProfileDiscoveryError(
                        f"ListAvailableProfiles returned an invalid nextToken in {region}"
                    )
                next_token = token
            profiles.extend(regional_profiles)
        except (httpx.HTTPError, json.JSONDecodeError, UnicodeDecodeError, ProfileDiscoveryError) as exc:
            failures.append(f"{region}: {exc}")

    unique = {profile.arn: profile for profile in profiles}
    result = sorted(unique.values(), key=lambda profile: (profile.profile_name.lower(), profile.arn))
    if result:
        return result
    if failures:
        failed_regions = ", ".join(region for region, _ in endpoints)
        raise ProfileDiscoveryError(
            f"Could not list Q Developer profiles in {failed_regions}. "
            "Verify the subscription assignment and requested OIDC scopes."
        )
    raise ProfileDiscoveryError(
        "This IAM Identity Center user has no Amazon Q Developer profiles. "
        "Ask an administrator to assign the subscription and profile."
    )


def select_profile(
    profiles: Sequence[QDeveloperProfile],
    selector: Optional[str] = None,
) -> QDeveloperProfile:
    """Select one discovered profile by ARN/name or unambiguous default."""
    if not profiles:
        raise ProfileDiscoveryError("No Amazon Q Developer profiles are available")
    if selector:
        matches = [
            profile
            for profile in profiles
            if profile.arn == selector or profile.profile_name == selector
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ProfileDiscoveryError(f"No Q Developer profile matches {selector!r}")
        raise ProfileDiscoveryError(
            f"Q Developer profile name {selector!r} is ambiguous; select by ARN"
        )
    if len(profiles) == 1:
        return profiles[0]
    choices = ", ".join(f"{p.profile_name} ({p.arn})" for p in profiles)
    raise ProfileDiscoveryError(
        "Multiple Q Developer profiles are available; pass --q-profile with a name or ARN. "
        f"Choices: {choices}"
    )


def build_credentials(
    sso_profile: AwsSsoProfile,
    registration: OidcRegistration,
    token: OidcToken,
    q_profile: QDeveloperProfile,
    scopes: Sequence[str] = OIDC_SCOPES,
    now: Optional[datetime] = None,
) -> BootstrapCredentials:
    """Build the gateway credential document from bootstrap results."""
    issued_at = now or datetime.now(timezone.utc)
    expires_at = (issued_at + timedelta(seconds=token.expires_in)).isoformat()
    return BootstrapCredentials(
        accessToken=token.access_token,
        refreshToken=token.refresh_token,
        expiresAt=expires_at,
        profileArn=q_profile.arn,
        region=sso_profile.region,
        apiRegion=q_profile.region,
        clientId=registration.client_id,
        clientSecret=registration.client_secret,
        scopes=list(scopes),
        startUrl=sso_profile.start_url,
        clientSecretExpiresAt=registration.client_secret_expires_at,
    )


def write_credentials(path: Path, credentials: BootstrapCredentials) -> None:
    """Write credentials atomically with owner-only permissions.

    Existing files are replaced atomically. Symlinks are rejected so a crafted
    output path cannot redirect secrets to an unintended target.
    """
    target = path.expanduser()
    if target.is_symlink():
        raise IdcBootstrapError(f"Refusing to write credentials through symlink: {target}")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor: Optional[int] = None
    temporary_path: Optional[Path] = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            json.dump(asdict(credentials), stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
        target.chmod(0o600)
    except OSError as exc:
        raise IdcBootstrapError(f"Cannot securely write credentials to {target}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
