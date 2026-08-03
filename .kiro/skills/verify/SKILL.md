---
name: verify
description: Verify gateway changes through the live HTTP surface without confusing tests with runtime evidence.
---

# Verify Kiro Gateway at runtime

1. Start an isolated instance: `python3 main.py --host 127.0.0.1 --port <unused-port>`.
2. Wait for `Application startup complete` in the server output. Model discovery should log the number of models loaded.
3. Read `PROXY_API_KEY` through Python code and call `GET /v1/models` with `Authorization: Bearer`; never print the key.
4. Capture the response body and check expected aliases, raw-ID suppression, duplicates, repeat-call stability, and 401 responses for missing/invalid auth.
5. To observe alias forwarding without spending inference credits, run a second gateway process whose `kiro.auth.get_kiro_api_host` points to a local capture server. Send a valid request through `/v1/messages`; inspect the captured `conversationState.currentMessage.userInputMessage.modelId`.
6. Stop every gateway and capture-server process when finished.

Use `$CLAUDE_JOB_DIR/tmp` for temporary capture scripts and output. Tests remain network-isolated CI evidence; they are not runtime verification.
