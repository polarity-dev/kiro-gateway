---
name: setup-gateway
description: Set up Kiro Gateway + Claude Code from a fresh clone using AWS IAM Identity Center, without requiring Kiro IDE or Kiro CLI. Runs device login, discovers the Amazon Q profile, synchronizes the gateway port and Claude Code, verifies end to end, and offers optional global skills.
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

0. **Use the current repo and check preconditions.** Unless the user explicitly
   names another path, run setup, startup, and verification in the current
   gateway checkout; do not ask them to choose a checkout. Then verify
   macOS/Linux, Python 3.10+, an AWS shared-config profile containing IAM Identity
   Center `sso_start_url` and `sso_region`, and an assigned Amazon Q Developer
   subscription/profile.
1. **Claude Code.** `claude --version`; if missing,
   `curl -fsSL https://claude.ai/install.sh | bash`, then re-check.
2. **Gateway installer (safe streamed handoff).** Ask for the AWS profile name.
   From the current repo directory, prefer `Monitor` over
   `./setup.sh -y --aws-profile NAME --agent-events`. Stdout is then only
   allowlisted `KIRO_EVENT` JSONL while the same process keeps polling. Relay
   `authorization_required.code` and `.url`, require an exact browser match, and
   wait for the terminal setup event. Never monitor raw logs or use ordinary
   captured Bash. If `Monitor` is unavailable, ask the user to enter
   `! ./setup.sh -y --aws-profile NAME` in Claude Code (without `!` in a normal
   terminal). Add `--port PORT`, `--q-profile NAME_OR_ARN`, or `--no-browser`
   when applicable. On mismatch, interruption, denial, or expiry,
   cancel and rerun for a fresh code; never reuse the old one. Setup writes
   `.env`, discovers the Q profile and live catalog, and atomically synchronizes
   `~/.claude/settings.json`. The generated non-empty `availableModels` string
   list plus `enforceAvailableModels: true` hides Claude Code's built-in rows.
3. **Start gateway.** Run `python3 main.py` from the current repo directory
   (foreground; reads `SERVER_PORT` from its `.env`, default `4567`). Normal access-token expiry refreshes silently. Only
   an unrecoverable direct-IdC refresh starts one local device login; Docker,
   CI, services, direct Uvicorn, SQLite, and multi-account mode stay
   non-interactive. `--no-interactive-reauth` disables this recovery.
4. **Verify.** Run `./setup.sh --check-port` from the current repo directory,
   then compare authenticated `/v1/models`
   with `availableModels`, then
   have the user run `claude` in a new terminal and confirm `/model` contains
   only Default plus rows labelled `From gateway`.
5. **Offer the `kiro-credits` skill globally** — check live Kiro credits from
   any repo. Symlink into both `~/.claude/skills/` and `~/.kiro/skills/`.
6. **Offer the `enable-claude-code` skill globally** — convert any Kiro repo
   into a dual-mode Kiro+Claude Code repo. Also symlink into both dirs, so
   the user can run it from any repo in either IDE (see below).
7. **Offer to remove the Claude commit attribution.** See below.

## Changing or checking the port

Use `.env` as the only persistent source of truth:

- First setup with a custom port: `./setup.sh -y --aws-profile NAME --port PORT`.
- Existing setup: `./setup.sh --port PORT`; this preserves credentials and
  unrelated Claude settings, then prints a restart checklist.
- Read-only verification: `./setup.sh --check-port`.

Never fix a persistent mismatch by editing only `ANTHROPIC_BASE_URL` or by adding
`--port` to the zsh helper. The helper must run `python3 main.py` without a port
override so it follows `.env`. After changing the port, stop and restart the
running gateway, open a new Claude Code session, then run the check command.

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
- Never launch IAM Identity Center setup with an agent Bash/background tool that
  may hide live output. Hand `! ./setup.sh -y --aws-profile NAME` to the Claude
  Code user and require an exact terminal/browser code match before approval.
- Prefer direct IAM Identity Center setup over manual configuration; rerun it to
  obtain a fresh code after any cancellation, mismatch, denial, or expiry.
