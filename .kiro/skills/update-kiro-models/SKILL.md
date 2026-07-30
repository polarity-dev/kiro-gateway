---
name: update-kiro-models
description: Probe Kiro's runtime endpoint to discover which model IDs are currently accepted, then propose a diff to kiro/config.py FALLBACK_MODELS. Use when the user asks to refresh, update, sync, verify, or audit the model list, or when they mention that Kiro has added or removed a model. Only invoke inside the kiro-gateway repo.
---

# Update Kiro's model list

Kiro's `runtime.{region}.kiro.dev` endpoint does **not** expose `/ListAvailableModels` (AWS limitation — see `kiro/account_manager.py:502`). The `FALLBACK_MODELS` list in `kiro/config.py` is therefore the effective source of truth for `/v1/models` and for gateway routing decisions. This skill keeps that list in sync with reality by actively probing the endpoint.

## How to run it

1. **Confirm the region with the user** if it's ambiguous. The probe hits whichever region `KIRO_API_REGION` (env) or the credentials file resolves to. Currently the user's env points at `eu-central-1`. Model availability can differ across regions, so a probe from `eu-central-1` is not authoritative for `us-east-1`.

2. **Run the probe** from the repo root:

   ```bash
   python3 scripts/probe_models.py --diff
   ```

   The script sends a minimal `generateAssistantResponse` request per candidate model, classifies the response as `WORKS` / `UNKNOWN` / `RATE_LIMITED` / `ERROR`, and prints a proposed diff against the current `FALLBACK_MODELS`.

3. **Read the diff** the script printed. Do NOT auto-apply. Show it to the user and ask which changes to commit. Rules of thumb:
   - `WORKS` / `RATE_LIMITED` → model is real, safe to keep or add.
   - `UNKNOWN` (Kiro replied `INVALID_MODEL_ID` or similar) → safe to remove.
   - `ERROR` → indeterminate. Re-run with `--concurrency 1` before proposing removal.

4. **Probe extra candidates** the user mentions but that aren't in the built-in candidate list:

   ```bash
   python3 scripts/probe_models.py --model claude-new-thing --model foo-bar --diff
   ```

5. **Apply the changes** by editing `kiro/config.py` — the `FALLBACK_MODELS` list around line 276. Also update the comment above the list with today's date and the region you probed from. `VALID_RUNTIME_MODEL_IDS` in `kiro/model_resolver.py` is derived automatically from `FALLBACK_MODELS`, so no separate update needed.

6. **Run the affected tests** before reporting done:

   ```bash
   python3 -m pytest tests/unit/test_config.py tests/unit/test_model_resolver.py tests/unit/test_account_manager.py -q
   ```

   These are the suites that consume `FALLBACK_MODELS`. They check structure and integration with `ModelResolver`, not specific model IDs, so additions or removals should not break them.

## Common gotchas

- **Enterprise AWS SSO OIDC needs `profileArn`.** The probe already handles this; if you see `HTTP 400 "profileArn is required for this request"` across the board, the auth manager did not resolve a profile ARN — check `.env` and `~/.aws/sso/cache/`.
- **`auto` is not a real model**, it's a Kiro router alias. Always keep it in the list. The probe reports it as `WORKS` because Kiro resolves it server-side.
- **Region-specific availability.** A model that fails in `eu-central-1` may work in `us-east-1`. If the user is on a different region than their probe run, warn them before removing anything.
- **Don't add unverified models.** Even if the user says "Kiro announced X", probe first — Kiro's marketing pages sometimes list models that haven't rolled out to all regions.

## When NOT to use this skill

- The user is asking about *Claude Code*'s model list, not the *gateway*'s. Claude Code sees whatever the gateway exposes via `/v1/models`; there's nothing to update on the Claude Code side.
- The user is outside this repo. This skill only makes sense in `kiro-gateway`.
