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

5. **Apply the changes** by editing `kiro/config.py`. Three lists have to move together — if you touch one and skip the others the picker breaks (see [Why three lists](#why-three-lists-must-move-together) below):

   - **`FALLBACK_MODELS`** — the raw Kiro `modelId`s the gateway will send upstream. `VALID_RUNTIME_MODEL_IDS` in `kiro/model_resolver.py` is derived from this list, so no separate update there.
   - **`MODEL_ALIASES`** — the human-readable rows shown in `/model`. Keys **must** start with `claude-` (yes, even for `auto`, `minimax-*`, `qwen3-*` — see the comment above the dict) or Claude Code's gateway model discovery silently drops them from the picker. Format is `"claude-<id> · <rate>x · <context>"` for the display key, and the raw Kiro `modelId` as the value. Rate multipliers and context windows come from the same probe run — look at `tokenLimits` / `rateCard` in the raw response, or ask the user to grab them from `q.{region}.amazonaws.com/ListAvailableModels` in Kiro IDE if the runtime endpoint didn't return them.
   - **`HIDDEN_FROM_LIST`** — the raw `modelId`s that should NOT appear as bare entries in `/v1/models`. Every modelId in `FALLBACK_MODELS` belongs here, so the picker only shows the pretty alias, not both.

   Also update the comment above `FALLBACK_MODELS` with today's date and the region you probed from.

   When **adding** a model: append it to all three lists. When **removing**: delete from all three. Missing any of the three produces a subtle bug (either the model doesn't show up, or shows up twice, or shows up under the wrong name).

### Why three lists must move together

The gateway exposes models to Claude Code through `/v1/models`, and Claude Code's picker applies two filters we don't control: it drops any id that doesn't start with `claude`/`anthropic`, and it doesn't render duplicates. Our three lists are the levers that make a Kiro model land as exactly one pretty picker row:

| List | Purpose | Rule |
|---|---|---|
| `FALLBACK_MODELS` | modelIds the gateway accepts and forwards | Raw Kiro ids, one per real model. Backs `VALID_RUNTIME_MODEL_IDS`. |
| `MODEL_ALIASES` | keys become the picker rows | Must start with `claude-` (client filter). Value is the raw modelId. |
| `HIDDEN_FROM_LIST` | modelIds to suppress from `/v1/models` | Every raw id from `FALLBACK_MODELS`, so only the alias row is left. |

Skip `MODEL_ALIASES` → the model works when typed but never appears in `/model`.
Skip `HIDDEN_FROM_LIST` → the model appears twice (once raw, once pretty).
Skip `FALLBACK_MODELS` → the alias resolves to a modelId the gateway thinks doesn't exist.

6. **Check the user's `~/.claude/settings.json` for a stale `model` key.** The
   top-level `model` value (written by `setup.sh` as the initial `/model`
   selection, then updated when the user hits Enter in the `/model` picker) is
   the *display name* — e.g. `"claude-sonnet-5 · 1.3x · 1M"`. If your changes
   renamed or removed the alias the user currently has pinned there, Claude
   Code will fall through to its default on the next launch and the user will
   think their preference got lost.

   Read the file, grab the `model` value, and see whether it still matches a
   key in the new `MODEL_ALIASES`. If it doesn't:

   - **Same modelId, new display key** (rate or context changed) → offer to
     rewrite `model` to the new key so the user's choice is preserved.
   - **modelId removed** (Kiro retired the model) → offer to reset `model` to
     `"claude-auto · 1x"`.
   - Never rewrite silently. Show the user what's stale and what you'd change
     it to first.

   Skip this step when only `FALLBACK_MODELS` moved (adding/removing a model
   the user hadn't pinned). It matters when the alias display keys change.

7. **Run the affected tests** before reporting done:

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
