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

If ListAvailableModels is unreachable, it falls back to a static candidate list.

Usage (from the repo root):
    python scripts/probe_models.py                    # discover + probe
    python scripts/probe_models.py --no-discover      # skip discovery, use static list
    python scripts/probe_models.py --model foo-bar    # probe extra model (merged with discovery)
    python scripts/probe_models.py --json > out.json  # machine-readable output
    python scripts/probe_models.py --diff             # print a proposed
                                                      # FALLBACK_MODELS diff

Auth is loaded via KiroAuthManager, so the same .env / credentials.json /
kiro-cli SQLite that runs the gateway also runs this script.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
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
    FALLBACK_MODELS,
    KIRO_CREDS_FILE,
    KIRO_CLI_DB_FILE,
    REFRESH_TOKEN,
    PROFILE_ARN,
    REGION,
)
from kiro.utils import get_kiro_headers  # noqa: E402


# Static fallback candidates — used only when ListAvailableModels discovery
# fails (network error, auth issue, etc.). Keep this roughly in sync with
# FALLBACK_MODELS but it doesn't need to be exact — it's a safety net.
_STATIC_CANDIDATES: List[str] = sorted({
    "auto",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-opus-4.8",
    "claude-opus-4.7",
    "claude-opus-4.6",
    "claude-sonnet-4.6",
    "claude-opus-4.5",
    "claude-sonnet-4.5",
    "claude-sonnet-4",
    "claude-haiku-4.5",
    "minimax-m2.5",
    "minimax-m2.1",
    "qwen3-coder-next",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
})


# Legacy Q endpoint — the only one that exposes ListAvailableModels.
# runtime.{region}.kiro.dev does NOT have it (returns UnknownOperationException).
_Q_HOST_TEMPLATE = "https://q.{region}.amazonaws.com"


async def _discover_candidates(auth: KiroAuthManager) -> Optional[List[str]]:
    """
    Call ListAvailableModels on the legacy Q endpoint to dynamically discover
    all model IDs the account has access to. Returns None on failure.
    """
    # Extract region from api_host (https://runtime.{region}.kiro.dev)
    # to build the legacy Q endpoint URL.
    import re as _re
    match = _re.search(r"runtime\.([^.]+)\.", auth.api_host)
    if not match:
        print(
            f"⚠️  Cannot extract region from api_host ({auth.api_host}) — "
            f"falling back to static candidates.",
            file=sys.stderr,
        )
        return None
    region = match.group(1)
    q_host = _Q_HOST_TEMPLATE.format(region=region)
    url = f"{q_host}/ListAvailableModels"
    token = await auth.get_access_token()
    headers = get_kiro_headers(auth, token)
    # The legacy endpoint requires x-amz-target header
    headers["x-amz-target"] = "AmazonCodeWhispererService.ListAvailableModels"

    params = {"origin": "AI_EDITOR"}
    if auth.profile_arn:
        params["profileArn"] = auth.profile_arn

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, params=params, timeout=15.0)
            if resp.status_code != 200:
                print(
                    f"⚠️  ListAvailableModels returned HTTP {resp.status_code} — "
                    f"falling back to static candidates.",
                    file=sys.stderr,
                )
                return None
            data = resp.json()
            models = data.get("models", [])
            ids = [m["modelId"] for m in models if "modelId" in m]
            if ids:
                # Always include "auto" — it's a router alias that won't show
                # up in ListAvailableModels but always works.
                if "auto" not in ids:
                    ids.append("auto")
                return sorted(set(ids))
            return None
    except Exception as exc:
        print(
            f"⚠️  ListAvailableModels failed ({exc.__class__.__name__}: {exc}) — "
            f"falling back to static candidates.",
            file=sys.stderr,
        )
        return None


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
    deleting a valid model from FALLBACK_MODELS.
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


def _print_diff(results: List[ProbeResult]) -> None:
    """
    Print a proposed FALLBACK_MODELS diff against kiro/config.py.

    A model is proposed for KEEP/ADD when it returned WORKS or RATE_LIMITED
    (both indicate the model is recognized — quota exhaustion is not our
    concern here). UNKNOWN triggers a REMOVE proposal. ERROR is left alone
    with a warning, since we cannot tell whether the model exists or not.
    """
    current_ids = {m["modelId"] for m in FALLBACK_MODELS}
    accepted: set[str] = set()
    unknown: set[str] = set()
    ambiguous: set[str] = set()

    for r in results:
        if r.status in (STATUS_WORKS, STATUS_RATE_LIMITED):
            accepted.add(r.model_id)
        elif r.status == STATUS_UNKNOWN:
            unknown.add(r.model_id)
        else:
            ambiguous.add(r.model_id)

    to_add = sorted(accepted - current_ids)
    to_remove = sorted(current_ids & unknown)
    to_keep = sorted(current_ids & accepted)
    ambiguous_in_current = sorted(current_ids & ambiguous)

    print("\nProposed FALLBACK_MODELS diff")
    print("=" * 60)

    if to_add:
        print("\n+ ADD (probe returned 200 or 429, not in current list):")
        for m in to_add:
            print(f"    + {{'modelId': '{m}'}},")
    if to_remove:
        print("\n- REMOVE (probe returned model-not-found, in current list):")
        for m in to_remove:
            print(f"    - {{'modelId': '{m}'}},")
    if not to_add and not to_remove:
        print("\n  (no changes proposed)")

    if to_keep:
        print(f"\n  KEEP: {len(to_keep)} models unchanged")
    if ambiguous_in_current:
        print(
            f"\n  AMBIGUOUS: {len(ambiguous_in_current)} models in the current "
            "list returned a non-classifiable error. Not proposing removal — "
            "re-run probe or inspect manually:"
        )
        for m in ambiguous_in_current:
            print(f"    ? {m}")


async def _run(candidates: List[str], concurrency: int, timeout: float, discover: bool) -> tuple[List[ProbeResult], List[str]]:
    """
    Run probes. Returns (results, candidates_used).
    If discover=True, attempts ListAvailableModels first to build candidate list.
    """
    auth = _build_auth_manager()
    # Warm up the token once so parallel probes don't race on refresh.
    await auth.get_access_token()

    # Dynamic discovery: merge ListAvailableModels output with any explicit candidates
    discovered: List[str] = []
    if discover:
        discovered_ids = await _discover_candidates(auth)
        if discovered_ids:
            discovered = discovered_ids
            # Merge: explicit candidates + discovered, deduped
            candidates = sorted(set(candidates) | set(discovered))

    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(limits=limits, http2=False) as client:
        sem = asyncio.Semaphore(concurrency)

        async def _guarded(model_id: str) -> ProbeResult:
            async with sem:
                return await _probe_one(client, auth, model_id, timeout)

        results = await asyncio.gather(*(_guarded(m) for m in candidates))
    return results, discovered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        action="append",
        dest="models",
        help="Probe a specific model ID (repeatable). Combined with discovery results.",
    )
    parser.add_argument(
        "--no-discover",
        action="store_true",
        help="Skip ListAvailableModels discovery, use only --model or static fallback list.",
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
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Print a proposed FALLBACK_MODELS diff after the report",
    )
    args = parser.parse_args()

    # Build candidate list:
    # - With --model: use those as the base (discovery will merge if enabled)
    # - Without --model and --no-discover: use static fallback as base
    # - Without --model and with discover: discovery replaces static list
    base_candidates = args.models or _STATIC_CANDIDATES
    discover = not args.no_discover

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
        if args.diff:
            _print_diff(results)

    # Exit non-zero if any WORKS-candidate probe failed with ERROR — makes it
    # easy to gate CI or a skill on a clean run.
    if any(r.status == STATUS_ERROR for r in results):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
