# -*- coding: utf-8 -*-

# Kiro Gateway
# https://github.com/jwadow/kiro-gateway
#
# Licensed under GNU AGPL v3+ (same as parent project).

"""
Probe which model IDs are actually accepted by Kiro's runtime endpoint.

The script first calls ListAvailableModels on the legacy Q endpoint
(q.{region}.amazonaws.com) to dynamically discover all available model IDs.
It then probes each discovered model against the runtime endpoint
(runtime.{region}.kiro.dev) to confirm it actually works, classifying the
outcome:

    WORKS         status 200, first byte received
    UNKNOWN       status 400/403 with a body suggesting the model does not exist
    RATE_LIMITED  status 429 (model exists, quota exhausted)
    ERROR         other status, transport error, or timeout

If ListAvailableModels is unreachable, discovery mode exits without probing.

Usage (from the repo root):
    python scripts/probe_models.py                    # discover + probe
    python scripts/probe_models.py --model foo-bar    # probe only explicit IDs
    python scripts/probe_models.py --json > out.json  # machine-readable output

Auth is loaded via KiroAuthManager, so the same .env / credentials.json /
kiro-cli SQLite that runs the gateway also runs this script.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Make the sibling `kiro` package importable when running as a script.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

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
from kiro.model_discovery import (  # noqa: E402
    ModelDiscoveryError,
    fetch_available_models,
)
from kiro.utils import get_kiro_headers  # noqa: E402


async def _discover_candidates(auth: KiroAuthManager) -> Optional[List[str]]:
    """Discover model IDs available to the authenticated Kiro account.

    Args:
        auth: Authenticated Kiro account.

    Returns:
        Sorted model IDs including the Kiro ``auto`` router, or ``None`` when
        discovery fails and the caller should use static candidates.
    """
    try:
        models = await fetch_available_models(auth)
    except ModelDiscoveryError as exc:
        print(
            f"⚠️  ListAvailableModels failed ({exc}) — "
            "falling back to static candidates.",
            file=sys.stderr,
        )
        return None

    model_ids = {model["modelId"] for model in models}
    model_ids.add("auto")
    return sorted(model_ids)


# Classification of a single probe attempt.
STATUS_WORKS = "WORKS"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_RATE_LIMITED = "RATE_LIMITED"
STATUS_ERROR = "ERROR"


@dataclass
class ProbeResult:
    model_id: str
    status: str
    http_status: Optional[int] = None
    detail: str = ""
    body_snippet: str = ""

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "status": self.status,
            "http_status": self.http_status,
            "detail": self.detail,
            "body_snippet": self.body_snippet,
        }


def _build_payload(model_id: str, profile_arn: Optional[str]) -> dict:
    """
    Minimal generateAssistantResponse payload. We ask for a one-token reply
    to keep the probe cheap; we still abort the stream as soon as the first
    byte arrives.
    """
    payload = {
        "conversationState": {
            "agentContinuationId": str(uuid.uuid4()),
            "agentTaskType": "vibe",
            "chatTriggerType": "MANUAL",
            "conversationId": str(uuid.uuid4()),
            "currentMessage": {
                "userInputMessage": {
                    "content": "hi",
                    "modelId": model_id,
                    "origin": "AI_EDITOR",
                    "userInputMessageContext": {"tools": []},
                }
            },
            "history": [],
        }
    }
    if profile_arn:
        payload["profileArn"] = profile_arn
    return payload


def _classify_error(status_code: int, body: str) -> str:
    """
    Map an HTTP status + response body to one of our four buckets.

    Kiro does not have a single canonical "unknown model" error code, so we
    look for known substrings in the body. When in doubt we default to ERROR
    rather than UNKNOWN, because a wrong classification here would mean
    incorrectly declaring a valid discovered model unavailable.
    """
    body_lower = body.lower()
    if status_code == 429:
        return STATUS_RATE_LIMITED
    if status_code in (400, 403, 404):
        markers = (
            "invalid_model_id",          # canonical Kiro reason code
            "invalid model id",
            "modelnotfound",
            "model not found",
            "model_not_found",
            "unknown model",
            "invalid model",
            "unsupported model",
            "no such model",
            "model does not exist",
        )
        if any(m in body_lower for m in markers):
            return STATUS_UNKNOWN
    return STATUS_ERROR


async def _probe_one(
    client: httpx.AsyncClient,
    auth: KiroAuthManager,
    model_id: str,
    timeout: float,
) -> ProbeResult:
    token = await auth.get_access_token()
    headers = get_kiro_headers(auth, token)
    url = f"{auth.api_host}/generateAssistantResponse"

    # Send profileArn whenever we have one. Enterprise AWS SSO OIDC (kiro-cli
    # with an explicit profile ARN, as here) *requires* it — the runtime
    # endpoint replies "profileArn is required for this request" otherwise.
    # Builder-ID personal accounts have no profile_arn so this stays None.
    payload = _build_payload(model_id, auth.profile_arn)

    try:
        async with client.stream(
            "POST", url, headers=headers, json=payload, timeout=timeout
        ) as resp:
            if resp.status_code == 200:
                # Grab at most 512 bytes then bail — we only need to confirm
                # the model was accepted, not consume the whole response.
                async for _chunk in resp.aiter_bytes(chunk_size=512):
                    break
                return ProbeResult(
                    model_id=model_id,
                    status=STATUS_WORKS,
                    http_status=200,
                    detail="first-byte received",
                )

            body_bytes = await resp.aread()
            body = body_bytes.decode("utf-8", errors="replace")
            snippet = body[:300].replace("\n", " ").strip()
            status = _classify_error(resp.status_code, body)
            return ProbeResult(
                model_id=model_id,
                status=status,
                http_status=resp.status_code,
                detail=f"HTTP {resp.status_code}",
                body_snippet=snippet,
            )

    except httpx.TimeoutException as exc:
        return ProbeResult(
            model_id=model_id,
            status=STATUS_ERROR,
            detail=f"timeout: {exc.__class__.__name__}",
        )
    except httpx.HTTPError as exc:
        return ProbeResult(
            model_id=model_id,
            status=STATUS_ERROR,
            detail=f"transport error: {exc.__class__.__name__}: {exc}",
        )


def _build_auth_manager() -> KiroAuthManager:
    """
    Build an auth manager from the same env the gateway uses. We deliberately
    do NOT read credentials.json here — that file is for the multi-account
    system; single-account probing uses the top-level env vars.
    """
    load_dotenv()

    if KIRO_CLI_DB_FILE:
        return KiroAuthManager(sqlite_db=KIRO_CLI_DB_FILE, region=REGION)
    if KIRO_CREDS_FILE:
        return KiroAuthManager(creds_file=KIRO_CREDS_FILE, region=REGION)
    if REFRESH_TOKEN:
        return KiroAuthManager(
            refresh_token=REFRESH_TOKEN,
            profile_arn=PROFILE_ARN,
            region=REGION,
        )
    raise SystemExit(
        "No credentials found. Set KIRO_CLI_DB_FILE, KIRO_CREDS_FILE, or "
        "REFRESH_TOKEN in the environment (see .env.example)."
    )


def _print_report(results: List[ProbeResult], api_host: str) -> None:
    by_status: dict[str, List[ProbeResult]] = {}
    for r in results:
        by_status.setdefault(r.status, []).append(r)

    print()
    print(f"Endpoint: {api_host}")
    print(f"Probed {len(results)} models\n")

    order = [STATUS_WORKS, STATUS_RATE_LIMITED, STATUS_UNKNOWN, STATUS_ERROR]
    for status in order:
        bucket = by_status.get(status, [])
        if not bucket:
            continue
        print(f"[{status}]  {len(bucket)}")
        for r in sorted(bucket, key=lambda x: x.model_id):
            line = f"  {r.model_id:<28}  {r.detail}"
            print(line)
            if r.body_snippet and status != STATUS_WORKS:
                print(f"    body: {r.body_snippet}")
        print()


async def _run(candidates: List[str], concurrency: int, timeout: float, discover: bool) -> tuple[List[ProbeResult], List[str]]:
    """
    Run probes. Returns (results, candidates_used).
    If discover=True, attempts ListAvailableModels first to build candidate list.
    """
    auth = _build_auth_manager()
    # Warm up the token once so parallel probes don't race on refresh.
    await auth.get_access_token()

    discovered: List[str] = []
    if discover:
        discovered_ids = await _discover_candidates(auth)
        if discovered_ids:
            discovered = discovered_ids
            candidates = discovered_ids

    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(limits=limits, http2=False) as client:
        sem = asyncio.Semaphore(concurrency)

        async def _guarded(model_id: str) -> ProbeResult:
            async with sem:
                return await _probe_one(client, auth, model_id, timeout)

        results = await asyncio.gather(*(_guarded(m) for m in candidates))
    return results, discovered


def _select_candidates(
    requested_models: Optional[List[str]],
    no_discover: bool,
) -> tuple[List[str], bool]:
    """Select probe candidates and whether catalog discovery should run.

    Args:
        requested_models: Explicit ``--model`` values, if any.
        no_discover: Whether catalog discovery was disabled explicitly.

    Returns:
        Candidate model IDs and the discovery flag. Explicit model requests are
        always isolated from catalog discovery.
    """
    candidates = requested_models or []
    discover = not no_discover and not requested_models
    return list(candidates), discover


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        action="append",
        dest="models",
        help="Probe only this model ID (repeatable); skips catalog discovery.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Parallel probes (default: 4)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Per-request timeout in seconds (default: 20)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a human report",
    )
    args = parser.parse_args()

    # Explicit --model runs are intentionally isolated: probing one candidate
    # must not fan out into live requests for the entire account catalog.
    base_candidates, discover = _select_candidates(args.models, False)

    results, discovered = asyncio.run(
        _run(base_candidates, args.concurrency, args.timeout, discover)
    )

    # Rebuild an auth manager just to read the resolved api_host for the
    # header of the report. Cheap — it doesn't refresh a token.
    api_host = _build_auth_manager().api_host

    if args.json:
        json.dump(
            {
                "api_host": api_host,
                "discovered_from_q_endpoint": discovered,
                "results": [r.to_dict() for r in results],
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        if discovered:
            print(f"\n✓ Discovered {len(discovered)} models from ListAvailableModels (q.amazonaws.com)")
        _print_report(results, api_host)

    # Exit non-zero if any WORKS-candidate probe failed with ERROR — makes it
    # easy to gate CI or a skill on a clean run.
    if any(r.status == STATUS_ERROR for r in results):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
