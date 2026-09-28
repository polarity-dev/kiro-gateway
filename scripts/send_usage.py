#!/usr/bin/env python3
"""
Push the current Kiro token usage to the kiro-tracker API.

Reads live usage from the same AWS endpoint the Kiro IDE uses
(GetUsageLimits, via the gateway's auth), then POSTs a snapshot to the
kiro-tracker ingest endpoint:

    POST {TRACKER_API}/usage
    x-api-key: <api key>
    { "userId": <identity center userId>, "tokenAvailable": <int>, "tokenSpent": <int> }

The tracker keys each user by their personal IAM Identity Center userId (read
from the GetUsageLimits response). This is what distinguishes users: the profile
ARN is the org subscription's ARN and is identical for everyone on the same
subscription, so it cannot be used as the per-user key.

Configuration (env vars, with sensible fallbacks):
    KIRO_TRACKER_API   Base URL of the tracker API (e.g.
                       https://xxxx.execute-api.eu-west-1.amazonaws.com/production).
                       Required.
    KIRO_TRACKER_KEY   The x-api-key value. If unset, the script tries to read
                       it from 1Password:
                           op read "op://Shared/kiro-tracker API key/credential"
    KIRO_TRACKER_RESOURCE_TYPE  Usage resource type to report. Default
                       AGENTIC_REQUEST (same as the credits skill).

Auth to Kiro is reused from the gateway config: same KIRO_CREDS_FILE /
KIRO_CLI_DB_FILE / REFRESH_TOKEN the gateway uses.

Exit codes: 0 ok, 2 failure (with a message on stderr).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Repo root is two levels up: <repo>/scripts/send_usage.py
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# chdir to repo root so kiro.config's load_dotenv() finds .env regardless of
# the caller's working directory.
os.chdir(_REPO_ROOT)

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from kiro.auth import KiroAuthManager  # noqa: E402
from kiro.config import (  # noqa: E402
    KIRO_CREDS_FILE,
    KIRO_CLI_DB_FILE,
    REFRESH_TOKEN,
    PROFILE_ARN,
    REGION,
)
from kiro.utils import get_kiro_headers  # noqa: E402

_OP_SECRET_REF = "op://Shared/kiro-tracker API key/credential"
_DEFAULT_RESOURCE_TYPE = "AGENTIC_REQUEST"


def _build_auth() -> KiroAuthManager:
    """Build the Kiro auth manager from the gateway config (same as check.py)."""
    load_dotenv()
    if KIRO_CLI_DB_FILE:
        return KiroAuthManager(sqlite_db=KIRO_CLI_DB_FILE, profile_arn=PROFILE_ARN, region=REGION)
    if KIRO_CREDS_FILE:
        return KiroAuthManager(creds_file=KIRO_CREDS_FILE, profile_arn=PROFILE_ARN, region=REGION)
    if REFRESH_TOKEN:
        return KiroAuthManager(refresh_token=REFRESH_TOKEN, profile_arn=PROFILE_ARN, region=REGION)
    raise SystemExit(
        "No Kiro credentials found. Set KIRO_CLI_DB_FILE, KIRO_CREDS_FILE, or "
        "REFRESH_TOKEN in your environment (see kiro-gateway/.env.example)."
    )


def _region_from_arn(arn: str | None) -> str | None:
    """arn:aws:codewhisperer:REGION:account:profile/id -> REGION."""
    if not arn:
        return None
    parts = arn.split(":")
    if len(parts) < 4:
        return None
    region = parts[3]
    if not re.match(r"^[a-z]+-[a-z]+-\d+$", region):
        return None
    return region


async def _fetch_usage(auth: KiroAuthManager, resource_type: str) -> dict:
    """Call GetUsageLimits and return the parsed JSON (same call as check.py)."""
    token = await auth.get_access_token()
    headers = get_kiro_headers(auth, token)
    headers["x-amz-target"] = "AmazonCodeWhispererService.GetUsageLimits"
    headers["Content-Type"] = "application/x-amz-json-1.0"

    api_region = _region_from_arn(auth.profile_arn) or "us-east-1"
    url = f"https://q.{api_region}.amazonaws.com/GetUsageLimits"
    body = {
        "origin": "AI_EDITOR",
        "profileArn": auth.profile_arn,
        "resourceType": resource_type,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url, headers=headers, content=json.dumps(body).encode(), timeout=20.0
        )
        if resp.status_code != 200:
            raise SystemExit(
                f"GetUsageLimits failed: HTTP {resp.status_code}\n"
                f"URL: {url}\nBody: {resp.text[:500]}"
            )
        return resp.json()


def _extract_usage(data: dict) -> tuple[int, int]:
    """
    Pull (tokenSpent, tokenAvailable) as integers from a GetUsageLimits response.

    Uses the first usage breakdown, preferring the high-precision fields. The
    tracker API requires non-negative integers, so precise float values are
    rounded to the nearest integer.

    Args:
        data: Parsed GetUsageLimits JSON.

    Returns:
        (token_spent, token_available) as ints.

    Raises:
        SystemExit: If the response has no usage breakdown.
    """
    breakdowns = data.get("usageBreakdownList") or []
    if not breakdowns:
        raise SystemExit("GetUsageLimits returned no usageBreakdownList; nothing to send.")
    b = breakdowns[0]
    used = b.get("currentUsageWithPrecision")
    if used is None:
        used = b.get("currentUsage") or 0
    cap = b.get("usageLimitWithPrecision")
    if cap is None:
        cap = b.get("usageLimit") or 0
    return int(round(float(used))), int(round(float(cap)))


def _extract_user_id(data: dict) -> str | None:
    """
    Pull the personal IAM Identity Center userId from a GetUsageLimits response.

    Unlike the profile ARN (which is the org subscription's ARN and therefore
    identical for every user on the same subscription), this ``userId`` is the
    caller's personal Identity Center identity. It has the shape
    ``<identitystore>.<uuid>``, e.g.
    ``d-93674552ed.e2f534a4-4081-700e-8184-086e15cb58f7``, is stable across
    sessions, and uniquely distinguishes each person. It is the key the tracker
    uses to separate users.

    The userId lives under the ``userInfo`` object in the GetUsageLimits
    response: ``{"userInfo": {"userId": "d-....<uuid>"}}``.

    Args:
        data: Parsed GetUsageLimits JSON.

    Returns:
        The userId string, or None if the response does not carry one.
    """
    user_info = data.get("userInfo")
    if isinstance(user_info, dict):
        user_id = user_info.get("userId")
        if isinstance(user_id, str) and user_id.strip():
            return user_id.strip()
    # Some responses may carry it top-level; accept that too as a fallback.
    user_id = data.get("userId")
    if isinstance(user_id, str) and user_id.strip():
        return user_id.strip()
    return None


def _resolve_api_key() -> str:
    """
    Resolve the tracker API key: env var first, then 1Password via `op read`.

    Returns:
        The x-api-key value.

    Raises:
        SystemExit: If neither source yields a key.
    """
    key = os.environ.get("KIRO_TRACKER_KEY", "").strip()
    if key:
        return key
    try:
        out = subprocess.run(
            ["op", "read", _OP_SECRET_REF],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except FileNotFoundError:
        raise SystemExit(
            "KIRO_TRACKER_KEY not set and the 1Password CLI ('op') is not "
            "installed. Set KIRO_TRACKER_KEY or install/sign in to `op`."
        )
    except subprocess.TimeoutExpired:
        raise SystemExit("Timed out reading the API key from 1Password.")
    if out.returncode != 0 or not out.stdout.strip():
        raise SystemExit(
            "Could not read the API key from 1Password "
            f"({_OP_SECRET_REF}). Sign in to `op` or set KIRO_TRACKER_KEY. "
            f"Details: {out.stderr.strip()[:200]}"
        )
    return out.stdout.strip()


def _post_snapshot(api_base: str, api_key: str, user_id: str, spent: int, available: int) -> dict:
    """
    POST one usage snapshot to the tracker ingest endpoint.

    Args:
        api_base: Tracker API base URL (trailing slashes are trimmed).
        api_key: x-api-key value.
        user_id: Personal IAM Identity Center userId identifying the user.
        spent: Tokens spent (int >= 0).
        available: Tokens available (int >= 0).

    Returns:
        The parsed JSON response body.

    Raises:
        SystemExit: On any non-201 response or transport error.
    """
    url = api_base.rstrip("/") + "/usage"
    payload = {"userId": user_id, "tokenAvailable": available, "tokenSpent": spent}
    try:
        resp = httpx.post(
            url,
            headers={"x-api-key": api_key, "content-type": "application/json"},
            content=json.dumps(payload).encode(),
            timeout=20.0,
        )
    except httpx.HTTPError as e:
        raise SystemExit(f"Failed to reach tracker API at {url}: {e}")
    if resp.status_code != 201:
        raise SystemExit(
            f"Ingest failed: HTTP {resp.status_code}\nURL: {url}\n"
            f"Body: {resp.text[:500]}"
        )
    return resp.json()


def main() -> int:
    api_base = os.environ.get("KIRO_TRACKER_API", "").strip()
    if not api_base:
        print(
            "KIRO_TRACKER_API is not set. Set it to the tracker ApiEndpoint, e.g.\n"
            "  export KIRO_TRACKER_API='https://xxxx.execute-api.eu-west-1.amazonaws.com/production'",
            file=sys.stderr,
        )
        return 2

    resource_type = os.environ.get("KIRO_TRACKER_RESOURCE_TYPE", _DEFAULT_RESOURCE_TYPE)

    try:
        auth = _build_auth()
        data = asyncio.run(_fetch_usage(auth, resource_type))
        spent, available = _extract_usage(data)
        # The personal userId is the identity key. The profile ARN is the org
        # subscription's ARN (shared by all users), so it cannot distinguish
        # users; we only fall back to it if the userId is unexpectedly absent.
        user_id = _extract_user_id(data) or auth.profile_arn
        if not user_id:
            print(
                "GetUsageLimits returned no userId and no profile ARN is available; "
                "cannot identify the user.",
                file=sys.stderr,
            )
            return 2
        api_key = _resolve_api_key()
        result = _post_snapshot(api_base, api_key, user_id, spent, available)
    except SystemExit as e:
        # SystemExit carries our user-facing message.
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - top-level guard, report and exit
        print(f"Unexpected error: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 2

    print(
        f"Sent snapshot: {result.get('tokenSpent')} / {result.get('tokenAvailable')} "
        f"tokens at {result.get('recordedAt')} for {result.get('userId')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
