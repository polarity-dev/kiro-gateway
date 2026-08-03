---
name: verify
description: Verify gateway changes through the live HTTP surface without confusing tests with runtime evidence.
---

# Verify Kiro Gateway at runtime

1. Start an isolated instance: `python3 main.py --host 127.0.0.1 --port <unused-port>`.
2. Wait for `Application startup complete`. Model discovery should report a non-zero model count.
3. Read `PROXY_API_KEY` through Python and call authenticated `GET /v1/models`; never print the key.
4. Check that every returned item has a non-empty string `id`, IDs are unique, repeat calls are stable, and missing/invalid auth returns 401.
5. Run `python3 scripts/sync_claude_models.py sync --check`. Inspect key names and counts only. Verify that the `/v1/models` ID set exactly equals `availableModels`, `model` is a member, and `enforceAvailableModels` is the boolean `true`.
6. In a new Claude Code session, open `/model`. It should contain Default plus gateway rows only; built-in model rows must be absent.
7. To observe display-ID forwarding without spending inference credits, run a second gateway whose `kiro.auth.get_kiro_api_host` points to a local capture server. Send a valid `/v1/messages` request with a synthetic `claude-kiro-<length>-...` row and inspect `conversationState.currentMessage.userInputMessage.modelId` for the raw Kiro ID.
8. Stop every gateway and capture-server process when finished.

Use `$CLAUDE_JOB_DIR/tmp` for temporary capture scripts and output. Tests remain network-isolated CI evidence; they are not runtime verification. User-level model enforcement is local configuration, not an administrative policy boundary.
