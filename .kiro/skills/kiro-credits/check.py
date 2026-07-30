#!/usr/bin/env python3
"""
Show current Kiro subscription credit usage — LIVE from the AWS API.

Calls the same endpoint the Kiro IDE uses under the hood:
    POST https://q.{api_region}.amazonaws.com/GetUsageLimits
    x-amz-target: AmazonCodeWhispererService.GetUsageLimits
    body: {origin, profileArn, resourceType: AGENTIC_REQUEST}

Note: runtime.{region}.kiro.dev only serves generateAssistantResponse; usage
and subscription operations remain on the legacy q.{region}.amazonaws.com
host. AWS announced deactivation of q.{region}.amazonaws.com on 2026-05-15
(see commit 07d24fc), but as of 2026-07-30 it's still serving
GetUsageLimits. If it ever goes away this script will need the new host.

Auth is reused from the kiro-gateway config: same KIRO_CREDS_FILE /
KIRO_CLI_DB_FILE / REFRESH_TOKEN the gateway uses.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import re
import sys
from pathlib import Path

# Skill lives at <repo>/.kiro/skills/kiro-credits/check.py — repo root is 3 levels up.
# When invoked via symlink from another repo, __file__ resolves to the real path
# inside kiro-gateway, so _REPO_ROOT is always correct.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# chdir to repo root so kiro.config's load_dotenv() finds .env regardless of
# the caller's working directory (e.g. when invoked via global symlink).
import os  # noqa: E402
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


def _build_auth() -> KiroAuthManager:
    load_dotenv()
    if KIRO_CLI_DB_FILE:
        return KiroAuthManager(sqlite_db=KIRO_CLI_DB_FILE, region=REGION)
    if KIRO_CREDS_FILE:
        return KiroAuthManager(creds_file=KIRO_CREDS_FILE, region=REGION)
    if REFRESH_TOKEN:
        return KiroAuthManager(refresh_token=REFRESH_TOKEN, profile_arn=PROFILE_ARN, region=REGION)
    raise SystemExit(
        "No Kiro credentials found. Set KIRO_CLI_DB_FILE, KIRO_CREDS_FILE, or "
        "REFRESH_TOKEN in your environment (see kiro-gateway/.env.example)."
    )


def _region_from_arn(arn: str | None) -> str | None:
    """arn:aws:codewhisperer:REGION:account:profile/id → REGION"""
    if not arn:
        return None
    parts = arn.split(":")
    if len(parts) < 4:
        return None
    region = parts[3]
    if not re.match(r"^[a-z]+-[a-z]+-\d+$", region):
        return None
    return region


async def _fetch_usage(auth: KiroAuthManager) -> dict:
    token = await auth.get_access_token()
    headers = get_kiro_headers(auth, token)
    headers["x-amz-target"] = "AmazonCodeWhispererService.GetUsageLimits"
    headers["Content-Type"] = "application/x-amz-json-1.0"

    api_region = _region_from_arn(auth.profile_arn) or "us-east-1"
    url = f"https://q.{api_region}.amazonaws.com/GetUsageLimits"

    body = {
        "origin": "AI_EDITOR",
        "profileArn": auth.profile_arn,
        "resourceType": "AGENTIC_REQUEST",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url, headers=headers, content=json.dumps(body).encode(), timeout=20.0
        )
        if resp.status_code != 200:
            raise SystemExit(
                f"GetUsageLimits failed: HTTP {resp.status_code}\n"
                f"URL: {url}\n"
                f"Body: {resp.text[:500]}"
            )
        return resp.json()


def _fmt_number(n: float | int) -> str:
    if isinstance(n, int) or (isinstance(n, float) and n == int(n)):
        return f"{int(n):,}"
    return f"{n:,.2f}"


def _print_report(data: dict) -> None:
    sub = data.get("subscriptionInfo") or {}
    title = sub.get("subscriptionTitle") or sub.get("type") or "Unknown plan"
    breakdowns = data.get("usageBreakdownList") or []

    if not breakdowns:
        print(f"\nPlan: {title}")
        print("  No usage breakdown returned by the API.")
        return

    for b in breakdowns:
        used = b.get("currentUsageWithPrecision") or b.get("currentUsage") or 0
        cap = b.get("usageLimitWithPrecision") or b.get("usageLimit") or 0
        pct = (used / cap * 100) if cap else 0
        overage_used = b.get("currentOveragesWithPrecision") or b.get("currentOverages") or 0
        overage_cap = b.get("overageCapWithPrecision") or b.get("overageCap") or 0
        overage_rate = b.get("overageRate")
        currency = b.get("currency") or "USD"
        name = b.get("displayNamePlural") or b.get("displayName") or "Credits"
        reset_ts = b.get("nextDateReset") or data.get("nextDateReset")

        bar_width = 30
        filled = int(round(bar_width * min(pct, 100) / 100))
        bar = "#" * filled + "-" * (bar_width - filled)

        print(f"\nPlan: {title}  (live from AWS)")
        print(f"  {name}:      {_fmt_number(used)} / {_fmt_number(cap)}  ({pct:.1f}%)")
        print(f"  [{bar}]")

        if overage_used > 0:
            overage_cost = overage_used * (overage_rate or 0)
            print(
                f"  Overage:      {_fmt_number(overage_used)} / {_fmt_number(overage_cap)}"
                f"  ({currency} {overage_cost:,.2f} @ {overage_rate}/credit)"
            )
        elif overage_cap:
            print(
                f"  Overage room: {_fmt_number(overage_cap)} available"
                f"  @ {overage_rate} {currency}/credit"
            )

        if reset_ts:
            # nextDateReset comes as epoch seconds (float)
            try:
                reset_dt = datetime.datetime.fromtimestamp(float(reset_ts)).astimezone()
                remaining = reset_dt - datetime.datetime.now().astimezone()
                days = remaining.days
                print(f"  Resets:       {reset_dt.strftime('%Y-%m-%d')}  ({days} days)")
            except (ValueError, TypeError, OSError):
                print(f"  Resets:       {reset_ts}")

    print()


def main() -> int:
    auth = _build_auth()
    try:
        data = asyncio.run(_fetch_usage(auth))
    except SystemExit:
        raise
    except Exception as e:
        print(f"Failed to fetch usage: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 2

    _print_report(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
