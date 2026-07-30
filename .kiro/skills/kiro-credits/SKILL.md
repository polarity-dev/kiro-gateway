---
name: kiro-credits
description: Show current Kiro subscription credit usage — how many credits used this month, monthly cap, plan tier, overage room, reset date. Data comes live from the AWS API (same endpoint the Kiro IDE uses). Use when the user asks how much Kiro quota they have left, current credit consumption, monthly cap, or plan usage. Triggers on English phrasings ("kiro credits", "how much quota left", "credit usage", "monthly cap", "am I close to the cap") and Italian phrasings ("crediti Kiro", "quanti crediti", "come siamo messi a crediti", "consumo mensile", "quota rimanente", "cap mensile", "quanto ho consumato").
---

# Check Kiro credit usage

## What it does

Runs `check.py` next to this file. Calls the same undocumented endpoint the
Kiro IDE uses under the hood:

    POST https://q.{api_region}.amazonaws.com/GetUsageLimits
    x-amz-target: AmazonCodeWhispererService.GetUsageLimits
    body: {origin: "AI_EDITOR", profileArn, resourceType: "AGENTIC_REQUEST"}

Auth reuses the kiro-gateway's own config (`KIRO_CREDS_FILE`,
`KIRO_CLI_DB_FILE`, or `REFRESH_TOKEN`), so if the gateway can talk to Kiro,
this skill can too.

Output: plan tier (e.g. "KIRO PRO MAX"), credits used vs cap, percentage bar,
overage room and rate, next reset date. Data is live at query time.

## How to run it

```bash
python3 .claude/skills/kiro-credits/check.py
```

Show the output to the user as-is — it already renders a progress bar and
formats numbers. Do not paraphrase.

## Prerequisites

- Must be run from within the kiro-gateway repo (the script imports
  `kiro.auth` and `kiro.config` to reuse the gateway's auth logic).
- `.env` or `credentials.json` must be configured (same as running the
  gateway itself — if `python3 main.py` works, this script works).

## Important non-obvious fact

`q.{region}.amazonaws.com` was deactivated by AWS on 2026-05-15 for the
streaming `generateAssistantResponse` operation — that's why commit `07d24fc`
moved the gateway to `runtime.{region}.kiro.dev`. But **usage/subscription
operations are still served by the legacy host** as of 2026-07-30. If AWS
eventually kills it entirely, this skill will start returning HTTP errors and
will need a new host. First place to check would be Kiro IDE's own
`q-client.log` for the new endpoint.

## Sharing with the team

This skill lives in the repo (`.claude/skills/kiro-credits/`), so anyone who
clones the repo gets it automatically. Colleagues just need to:

1. Pull the repo.
2. Have their own `.env` / `credentials.json` set up (same as running the
   gateway).
3. Ask Claude Code from within the repo: "quanti crediti Kiro mi restano?" —
   Claude will auto-invoke this skill.

If they want it available outside the repo, they can symlink it globally:

    mkdir -p ~/.claude/skills
    ln -s "$(pwd)/.claude/skills/kiro-credits" ~/.claude/skills/kiro-credits

But note: the script imports from the `kiro/` Python package, so it still
needs to run from a directory where that package is importable. Symlinking
just makes the skill definition discoverable everywhere.
