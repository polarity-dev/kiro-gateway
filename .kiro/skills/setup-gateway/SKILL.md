---
name: setup-gateway
description: Set up Kiro Gateway + Claude Code on the user's machine from a fresh clone. Checks/installs Claude Code, runs the gateway installer, starts the server, verifies end to end, and offers to install the kiro-credits skill globally. Use when the user asks to "set up kiro-gateway", "set up Claude Code", "get me running", "configura il gateway", "installami claude code", or opens this repo for the first time and wants it working. macOS + Enterprise/IdC (Kiro IDE).
---

# Set up Kiro Gateway + Claude Code

This skill drives the first-time setup of this repo: it turns a fresh clone into
a working local Claude Code pointed at the user's Kiro subscription.

The authoritative, always-loaded runbook is
[`.kiro/steering/setup.md`](../../steering/setup.md). This skill is the
invocable entry point (`/setup-gateway`) — **follow the runbook step by step**,
running one command at a time and reading each result before continuing. Do not
improvise a different flow.

## Flow (summary — full detail in the runbook)

0. **Preconditions.** macOS (`uname` → `Darwin`), Python 3.10+, Kiro IDE logged
   in (`~/.aws/sso/cache/kiro-auth-token.json` exists), and at least one message
   sent in Kiro IDE (so the `profileArn` is in its logs). If one fails, tell the
   user how to fix it and stop.
1. **Claude Code.** `claude --version`; if missing,
   `curl -fsSL https://claude.ai/install.sh | bash`, then re-check.
2. **Gateway installer.** `./setup.sh -y` (non-interactive; writes `.env` and
   `~/.claude/settings.json`).
3. **Start gateway.** `python3 main.py` (foreground, `localhost:8000`).
4. **Verify.** `curl` the `/v1/models` endpoint, then have the user run `claude`
   in a new terminal and check `/model` lists models `From gateway`.
5. **Offer the credits skill.** See below — do this before wrapping up.

## Step 5 — Offer to install the kiro-credits skill globally

After the gateway works, offer to make the **`kiro-credits`** skill available in
every project, not just this repo. **Explain all of this to the user before
doing anything, and only proceed if they say yes:**

- **What it is:** a skill named `kiro-credits` that lives in this repo at
  `.kiro/skills/kiro-credits/`.
- **What it does:** shows their live Kiro subscription usage — credits used this
  month, monthly cap, plan tier, overage room, and reset date — by calling the
  same AWS endpoint the Kiro IDE uses. They invoke it just by asking, e.g.
  *"quanti crediti Kiro mi restano?"* or *"how much quota is left?"*.
- **Why it's useful outside this repo:** so they can check credits from any
  Claude Code session without first `cd`-ing into kiro-gateway.
- **Where it will be installed:** `~/.claude/skills/kiro-credits` — their global
  (user-level) Claude Code skills directory, so it's discoverable everywhere.
- **How (symlink, not copy) and why:** the skill's `check.py` imports this
  repo's `kiro/` Python package for auth, so it must run against the repo. A
  copy would be missing that package and would go stale; a symlink stays in sync
  and keeps working. It still needs to run from a machine where this repo is
  present.

If they agree, install it:

```bash
mkdir -p ~/.claude/skills
ln -sfn "$(pwd)/.kiro/skills/kiro-credits" ~/.claude/skills/kiro-credits
```

Then confirm: `ls -la ~/.claude/skills/kiro-credits` should show the symlink
resolving into the repo. Tell them they can now ask about Kiro credits from any
Claude Code session.

If they decline, leave it — it already works inside this repo. Mention they can
run `/setup-gateway` again or ask you later to install it.

## Non-negotiables

- Never commit secrets. `.env`, `credentials.json`, `state.json` are gitignored.
- Never hand-edit `profileArn` / `KIRO_API_REGION`; they come from `setup.sh`.
- Prefer `./setup.sh -y` over manual configuration — it is the single source of
  truth and is safe to re-run.
