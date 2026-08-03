---
name: update-kiro-models
description: Refresh Claude Code's available model allowlist from Kiro's dynamic catalog. Use when the user asks to refresh, update, sync, verify, or audit the model list, or when Kiro adds/removes a model. Only invoke inside the kiro-gateway repo.
---

# Synchronize Kiro models with Claude Code

Kiro's `ListAvailableModels` response is the source of truth. The gateway stores
last-known-good metadata in `state.json`, derives Claude-compatible IDs with
`build_model_display_id()`, and exposes those strings from `/v1/models`. Do not
add model IDs to `kiro/config.py` or recreate static fallback/alias lists.

## Refresh the local Claude Code catalog

From the repository root, run:

```bash
python3 scripts/sync_claude_models.py sync
```

The command tries sources in this order:

1. live `q.{region}.amazonaws.com/ListAvailableModels` discovery;
2. the non-empty catalog persisted in `state.json`;
3. the existing non-empty `availableModels` in `~/.claude/settings.json`.

It reports the source and count without printing credentials. It writes the
exact gateway display IDs as a non-empty `string[]`, sets
`enforceAvailableModels: true`, and updates `model` only when needed. If rate or
context metadata changed, the selection follows the same underlying Kiro
`modelId`; if the model disappeared, it prefers the real `auto` entry when
present, otherwise the first sorted allowed ID. When `auto` exists, the command
also maps Claude Code's virtual Default/Haiku tier to **Kiro Auto** while keeping
the explicit Haiku gateway row selectable.

The command never writes an enforced empty list. Malformed settings or a total
lack of safe catalog sources return an error and leave the file untouched.

## Verify without changing settings

```bash
python3 scripts/sync_claude_models.py sync --check
```

Exit code 0 means settings already match the resolved catalog; exit code 1 means
they drifted; exit code 2 means validation or discovery failed.

For live verification, start the gateway and compare the authenticated
`/v1/models` IDs with `availableModels`:

- both lists must be non-empty string arrays;
- their sets must match exactly;
- `model` must be in the allowlist;
- `enforceAvailableModels` must be `true`.

Then start a new Claude Code session and check `/model`: it should show only
Default plus gateway rows, with no built-in model rows.

## Runtime probing

`python3 scripts/probe_models.py` remains a diagnostic check against
`generateAssistantResponse`. It does not define the picker catalog and does not
update configuration. Explicit `--model` arguments can test announced IDs that
are not yet in discovery.

## Important boundaries

- `settings.json` is static. Run this skill again when Kiro changes its catalog;
  the gateway server does not silently mutate editor-global configuration.
- User-level `enforceAvailableModels` is a local restriction, not an
  organization security boundary. Administrative enforcement requires Claude
  Code managed/policy settings.
- Keep `ANTHROPIC_MODEL` out of the settings `env` block: it outranks the saved
  `/model` selection. Use the top-level `model` key managed by the sync command.
- Never print auth tokens, proxy keys, or complete settings values.

## Tests

```bash
python3 -m pytest \
  tests/unit/test_model_discovery.py \
  tests/unit/test_model_resolver.py \
  tests/unit/test_claude_settings.py \
  tests/unit/test_sync_claude_models.py \
  tests/unit/test_account_manager.py \
  tests/unit/test_routes_openai.py -q
```
