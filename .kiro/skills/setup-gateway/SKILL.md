---
name: setup-gateway
description: Set up Kiro Gateway + Claude Code on the user's machine from a fresh clone. Checks/installs Claude Code, runs the gateway installer, starts the server, verifies end to end, and offers to install two global skills (kiro-credits for live subscription usage, enable-claude-code for converting Kiro repos to dual-mode). Use when the user asks to "set up kiro-gateway", "set up Claude Code", "get me running", "configura il gateway", "installami claude code", or opens this repo for the first time and wants it working. macOS + Enterprise/IdC (Kiro IDE).
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
5. **Offer the `kiro-credits` skill globally** — check live Kiro credits from
   any repo. Symlink into both `~/.claude/skills/` and `~/.kiro/skills/`.
6. **Offer the `enable-claude-code` skill globally** — convert any Kiro repo
   into a dual-mode Kiro+Claude Code repo. Also symlink into both dirs, so
   the user can run it from any repo in either IDE (see below).
7. **Offer to remove the Claude commit attribution.** See below.

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
  session without first `cd`-ing into kiro-gateway.
- **Where it will be installed:** `~/.claude/skills/kiro-credits` **and**
  `~/.kiro/skills/kiro-credits` — the user-level dirs Claude Code and Kiro
  scan for global skills. Both symlinks point to the same folder in the repo,
  so the skill works regardless of which IDE the user opens.
- **How (symlink, not copy) and why:** the skill's `check.py` imports this
  repo's `kiro/` Python package for auth, so it must run against the repo. A
  copy would be missing that package and would go stale; a symlink stays in sync
  and keeps working. It still needs to run from a machine where this repo is
  present.

If they agree, install it:

```bash
mkdir -p ~/.claude/skills ~/.kiro/skills
ln -sfn "$(pwd)/.kiro/skills/kiro-credits" ~/.claude/skills/kiro-credits
ln -sfn "$(pwd)/.kiro/skills/kiro-credits" ~/.kiro/skills/kiro-credits
```

Then confirm both resolve into the repo:
`ls -la ~/.claude/skills/kiro-credits ~/.kiro/skills/kiro-credits`. Tell them
they can now ask about Kiro credits from any IDE session.

If they decline, leave it — it already works inside this repo. Mention they can
run `/setup-gateway` again or ask you later to install it.

## Step 6 — Offer to install the enable-claude-code skill globally

Same pattern, different skill. **Explain first, only proceed if they agree:**

- **What it is:** a skill named `enable-claude-code` in this repo at
  `.kiro/skills/enable-claude-code/`.
- **What it does:** converts any Kiro-only repo (with `.kiro/steering/` and
  optional MCP config) into a dual-mode repo that runs natively on both Kiro
  and Claude Code. Migrates the trigger-based steering files to
  `.kiro/skills/<name>/SKILL.md`, creates the `.claude/skills` and `.mcp.json`
  symlinks, writes `CLAUDE.md` as entrypoint, cleans up stale `#skill-name`
  refs, updates the README. It follows the exact pattern applied to
  `polarity-marketing-and-sales` (commit range `fecaf55..eaca5ec`).
- **Why globally, not just here:** the user runs this from *other* repos — the
  ones they want to migrate. If it lived only inside `kiro-gateway`, they'd
  have to `cd` in first, which defeats the point.
- **Why symlink into both `~/.claude/skills/` and `~/.kiro/skills/`:** the
  target repo might get opened in Kiro (user hasn't migrated anything yet) or
  in Claude Code (user has already migrated other repos and is working from
  there). The skill has to be reachable from either IDE. Same source folder,
  two symlink entry points — no duplication.
- **Symlink, not copy:** the skill ships with `templates/` versioned alongside
  it. A symlink follows updates to the repo; a copy would rot.

If they agree, install it:

```bash
mkdir -p ~/.claude/skills ~/.kiro/skills
ln -sfn "$(pwd)/.kiro/skills/enable-claude-code" ~/.claude/skills/enable-claude-code
ln -sfn "$(pwd)/.kiro/skills/enable-claude-code" ~/.kiro/skills/enable-claude-code
```

Confirm both resolve into the repo. Tell them: from any Kiro repo they open,
in either IDE, they can now ask *"rendi questa repo compatibile con Claude
Code"* / *"convert this repo to dual-mode"* and the skill will drive a guided
migration.

If they decline, leave it — the skill still works when invoked from inside
this repo.

## Step 7 — Offer to remove the Claude attribution from commits

Claude Code appends `Co-Authored-By: Claude <noreply@anthropic.com>` to every
commit it makes, and a "Generated with Claude Code" line to PR descriptions. Many
people don't want that in their project history. Offer to turn it off — **explain
it first and only proceed if they agree:**

- **What changes:** their global `~/.claude/settings.json` gains an `attribution`
  block with `commit` and `pr` set to `""` (empty = no attribution text).
- **Scope:** user-level, so every project and every future session.
- **What it does *not* do:** existing commits keep their trailer. This affects
  new commits only — rewriting history is a separate, deliberate operation, never
  part of setup.
- **Why this field:** `includeCoAuthoredBy` is deprecated in the settings schema;
  `attribution` is current and also covers PR bodies.

The exact merge script is in the runbook
([`.kiro/steering/setup.md`](../../steering/setup.md), Step 6) — use it.

**Critical:** *merge* the key, never overwrite the file. `~/.claude/settings.json`
already holds the gateway config (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`)
written in Step 2; clobbering it breaks their setup. After writing, confirm the
gateway keys survived — print key *names* only, never token values.

If they decline, leave it — purely cosmetic, changes nothing functionally.

## Non-negotiables

- Never commit secrets. `.env`, `credentials.json`, `state.json` are gitignored.
- Never hand-edit `profileArn` / `KIRO_API_REGION`; they come from `setup.sh`.
- Prefer `./setup.sh -y` over manual configuration — it is the single source of
  truth and is safe to re-run.
