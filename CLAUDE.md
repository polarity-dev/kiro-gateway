# CLAUDE.md

Guidance for Claude Code (and other AI agents) working in this repo.

## First-time setup — read this first

This repo is both the **Kiro Gateway** and the **setup kit** for running Claude
Code against it locally. If the user asks you to set up the gateway, set up
Claude Code, or "get me running", follow the setup runbook:

**➡️ [`.kiro/steering/setup.md`](.kiro/steering/setup.md)** — the canonical,
step-by-step setup runbook. It is the single source of truth for setup. Do not
improvise a different flow.

You can also invoke it as a slash command: **`/setup-gateway`** (the skill at
`.kiro/skills/setup-gateway/`, shared with Kiro via the `.claude/skills`
symlink).

(Kiro loads the runbook automatically as always-on steering. Claude Code and
other tools should open and follow it when the task is setup-related.)

The short version: unless the user names another path, use the current
repository directory automatically. Run setup, `python3 main.py`, and
verification from that same working directory. Prefer
`Monitor` over `./setup.sh -y --aws-profile NAME --agent-events`; if unavailable,
hand `! ./setup.sh -y --aws-profile NAME` to the user. Approve only an exact code
match. Full rules are in the runbook.

## Working on the gateway code

For contributing to the gateway itself — architecture, conventions, testing
philosophy, common tasks — see **[`AGENTS.md`](AGENTS.md)**. Highlights:

- Run the server: `python3 main.py` (or `--port 9000`)
- Run tests: `pytest -v` (tests are fully network-isolated)
- Every change needs tests, English-only identifiers, and parity across the
  OpenAI + Anthropic surfaces and streaming + non-streaming paths.

## Secrets

`.env`, `credentials.json`, and `state.json` are gitignored. Never commit real
tokens, ARNs, or API keys, and never paste them into tracked files or commit
messages.
