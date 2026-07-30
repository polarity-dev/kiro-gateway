---
inclusion: always
---

# Kiro Gateway — Setup Runbook for AI Agents

This repo is two things at once:

1. The **Kiro Gateway** — a proxy that exposes Kiro (Amazon Q Developer) as an
   OpenAI/Anthropic-compatible API on `localhost`.
2. The **setup kit** for pointing Claude Code (and other AI coding tools) at
   that gateway, so a teammate can clone the repo and get a working local Claude
   Code in a few minutes.

**If the user asks you to "set up kiro-gateway", "set up Claude Code", "get me
running", or anything similar, follow the runbook below.** Do not improvise a
different flow — this one is tested and encodes hard-won knowledge about
Enterprise/IdC quirks.

## Who this is for

Anyone on **macOS** who signs in to Kiro IDE with an AWS IAM Identity Center
user (Enterprise / IdC) and has **Kiro IDE installed and logged in**. The
gateway then reuses Kiro IDE's own credentials — the user never pastes a token.

If the user is not on macOS, stop and tell them the automated path is macOS-only
today; point them at the manual [Configuration](../../README.md#-configuration)
section of the README.

## The runbook

Work through these steps in order. Run one command at a time, read its output,
and only continue if it succeeded. Explain what you are doing in plain language
as you go — the user may not know the internals.

### Step 0 — Preconditions (check, do not assume)

Verify all of these before touching anything. If one fails, tell the user how to
fix it and stop.

- **macOS.** Run `uname` — it must print `Darwin`.
- **Python 3.10+.** Run `python3 --version`.
- **Kiro IDE installed and logged in.** Check that
  `~/.aws/sso/cache/kiro-auth-token.json` exists.
- **At least one message sent in Kiro IDE.** This is what makes Kiro write the
  `profileArn` into its logs. If the user has never used Kiro IDE, ask them to
  send one message (anything) in it first — the installer cannot proceed without
  the ARN.

### Step 1 — Ensure Claude Code is installed

Check first:

```bash
claude --version
```

- If it prints a version (e.g. `2.1.211 (Claude Code)`), it is installed. Skip
  to Step 2.
- If it errors with `command not found`, install it with the official native
  installer:

  ```bash
  curl -fsSL https://claude.ai/install.sh | bash
  ```

  Then re-check `claude --version`. If the shell still cannot find `claude`, the
  install added `~/.local/bin` to a profile that the current shell has not
  reloaded — tell the user to open a new terminal, or to add
  `~/.local/bin` to their `PATH`.

Do not install via `sudo npm` — the official installer is the supported path and
auto-updates itself.

### Step 2 — Run the gateway installer

From the repo root:

```bash
./setup.sh -y
```

The `-y` flag runs it non-interactively so you (the agent) don't hang on
prompts. This single script:

- Installs Python dependencies from `requirements.txt` if missing.
- Locates the Kiro credentials file.
- Extracts the CodeWhisperer `profileArn` from Kiro IDE logs (the SSO refresh
  flow never returns it, so this is the only way to get it).
- Resolves the correct `KIRO_API_REGION` from the ARN (the API region usually
  differs from the SSO login region).
- Generates a random `PROXY_API_KEY` and writes `.env`.
- Configures `~/.claude/settings.json` to point Claude Code at
  `http://localhost:8000` using `ANTHROPIC_AUTH_TOKEN` (not `ANTHROPIC_API_KEY`
  — only the former skips the interactive OAuth browser login), enables
  gateway model discovery, and sets the initial model to `claude-auto · 1x`
  (Kiro's server-side auto-router). This is written as the top-level `model`
  key, not as `ANTHROPIC_MODEL`: the env var overrides `/model` on every launch
  (so the user's choice never sticks) and, worse, forces Claude Code to render
  any `claude-opus-4.*` id as the retired *Claude Opus 4* with a deprecation
  warning. If the runbook finds a legacy `ANTHROPIC_MODEL` in `env`, the
  installer drops it.

**If it fails with `Could not find profileArn`:** the user has not sent a
message in Kiro IDE yet, or Kiro hasn't logged one. Ask them to send a message
in Kiro IDE, then re-run `./setup.sh -y`.

**Never** write `.env`, `~/.claude/settings.json`, or the `profileArn`/region
by hand as a shortcut — the script's discovery logic exists precisely because
those values cannot be guessed. If the script fails, fix the precondition it
reports and re-run it.

### Step 3 — Start the gateway

The gateway is a foreground server. Start it in its own terminal:

```bash
python3 main.py
```

It listens on `http://localhost:8000`. Leave it running — Claude Code talks to
it. If port 8000 is busy, use `python3 main.py --port 9000` and update
`ANTHROPIC_BASE_URL` in `~/.claude/settings.json` to match.

Because the config lives in `~/.claude/settings.json` (not shell exports), the
user does **not** need to export anything or start the gateway from any
particular directory. Exports wouldn't reach Claude Code's background agents
anyway; the settings file does.

### Step 4 — Verify end to end

With the gateway running, confirm the two hops work:

```bash
# 1. Gateway is up and authenticated — lists the models your subscription grants
curl -s localhost:8000/v1/models -H "Authorization: Bearer $(grep -m1 '^PROXY_API_KEY=' .env | cut -d'"' -f2)" \
  | python3 -c "import sys,json; print('\n'.join(m['id'] for m in json.load(sys.stdin)['data']))"
```

If that prints a list of model IDs, the gateway and Kiro auth are working. Then
tell the user to open a **new terminal** and run `claude` — inside it, `/model`
should list models labelled `From gateway`.

**Heads-up on the picker names.** Every entry starts with `claude-` — even the
non-Claude ones like `claude-auto · 1x`, `claude-minimax-m2.5 · …`, and
`claude-qwen3-coder-next · …`. That prefix is cosmetic: Claude Code's gateway
model discovery
[silently drops any `/v1/models` entry whose id doesn't begin with
`claude` or `anthropic`](https://code.claude.com/docs/en/llm-gateway-protocol#model-discovery),
so we prefix the aliases in `MODEL_ALIASES` to get them into the picker. The
real Kiro modelId (`auto`, `minimax-m2.5`, …) is still what the gateway
forwards upstream — nothing changes on the wire.

### Step 5 — Offer to install the kiro-credits skill globally

Before wrapping up, offer to make the **`kiro-credits`** skill available in every
project, not just this repo. **Explain all of this to the user first, and only
proceed if they agree:**

- **What it is:** a skill named `kiro-credits` in this repo at
  `.kiro/skills/kiro-credits/`.
- **What it does:** shows their live Kiro subscription usage — credits used this
  month, monthly cap, plan tier, overage room, and reset date — from the same
  AWS endpoint the Kiro IDE uses. They invoke it just by asking, e.g. *"quanti
  crediti Kiro mi restano?"* or *"how much quota is left?"*.
- **Why it's useful outside this repo:** so they can check credits from any
  Claude Code session without `cd`-ing into kiro-gateway first.
- **Where it will be installed:** `~/.claude/skills/kiro-credits` — their global
  (user-level) Claude Code skills directory, discoverable everywhere.
- **How, and why a symlink not a copy:** the skill's `check.py` imports this
  repo's `kiro/` Python package for auth, so it must run against the repo. A
  copy would lack that package and go stale; a symlink stays in sync. It still
  requires this repo to be present on the machine.

If they agree:

```bash
mkdir -p ~/.claude/skills ~/.kiro/skills
ln -sfn "$(pwd)/.kiro/skills/kiro-credits" ~/.claude/skills/kiro-credits
ln -sfn "$(pwd)/.kiro/skills/kiro-credits" ~/.kiro/skills/kiro-credits
ls -la ~/.claude/skills/kiro-credits ~/.kiro/skills/kiro-credits   # both should resolve into the repo
```

Symlink into **both** `~/.claude/skills/` and `~/.kiro/skills/` — the skill has
to be reachable regardless of which IDE the user opens next. Kiro reads
`~/.kiro/skills/`, Claude Code reads `~/.claude/skills/`; a single source
folder in the repo, two entry points.

If they decline, leave it — it already works inside this repo.

### Step 6 — Offer to install the enable-claude-code skill globally

Same pattern as Step 5, for a different skill. **Explain first, only proceed if
they agree:**

- **What it is:** a skill named `enable-claude-code` in this repo at
  `.kiro/skills/enable-claude-code/`.
- **What it does:** takes any Kiro repo that today uses only `.kiro/steering/`
  and converts it into a dual-mode repo that runs natively on both Kiro and
  Claude Code. Migrates trigger-based steering files to `.kiro/skills/<name>/SKILL.md`,
  creates the `.claude/skills` and `.mcp.json` symlinks, writes `CLAUDE.md` as
  entrypoint, cleans up `#skill-name` refs. Follows the exact pattern applied to
  `polarity-marketing-and-sales`.
- **Why globally:** the user will run this from *other* repos — repos that
  aren't dual-mode yet. If the skill lived only inside `kiro-gateway`, they
  couldn't invoke it from the repo they're trying to migrate. Global install
  means "open any Kiro repo, ask *rendi questa repo compatibile con Claude
  Code*, and it works."
- **Why both `~/.kiro/skills/` and `~/.claude/skills/`:** the user might open
  the target repo in Kiro (they've never migrated anything yet) or in Claude
  Code (they've already migrated some repos and are working from there). The
  skill has to be reachable from either IDE. A single source folder in this
  repo, two symlink entry points — no duplication.
- **Symlink, not copy:** the skill's `templates/` are versioned with this repo.
  A symlink stays in sync when the repo is updated; a copy would rot.

If they agree:

```bash
mkdir -p ~/.claude/skills ~/.kiro/skills
ln -sfn "$(pwd)/.kiro/skills/enable-claude-code" ~/.claude/skills/enable-claude-code
ln -sfn "$(pwd)/.kiro/skills/enable-claude-code" ~/.kiro/skills/enable-claude-code
ls -la ~/.claude/skills/enable-claude-code ~/.kiro/skills/enable-claude-code
```

Tell them: from now on, in any Kiro repo they open (in either IDE), they can
ask *"rendi questa repo compatibile con Claude Code"* / *"convert this repo to
dual-mode"* and the skill runs. It's a guided flow: they'll review the
classification of each steering file before anything gets moved.

If they decline, leave it — it still works when invoked inside this repo.

### Step 7 — Offer to remove the Claude attribution from commits

By default Claude Code appends a `Co-Authored-By: Claude <noreply@anthropic.com>`
trailer to every commit it makes, plus a "Generated with Claude Code" line to PR
descriptions. Many people don't want that in their project history. Offer to turn
it off. **Explain it first and only proceed if they agree:**

- **What changes:** their global `~/.claude/settings.json` gains an `attribution`
  block with `commit` and `pr` set to `""`. Empty string means "no attribution
  text".
- **Scope:** user-level, so it applies to every project and every future session,
  not just this repo.
- **What it does *not* do:** commits that already carry the trailer keep it. This
  only affects commits made from now on. Rewriting existing history is a
  separate, deliberate operation — do not do it as part of setup.
- **Why `attribution` and not `includeCoAuthoredBy`:** the latter is deprecated
  in the settings schema. `attribution` is the current field and also covers PR
  descriptions.

If they agree, **merge** the key in. Do **not** overwrite the file — it already
holds the gateway config (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`) written
in Step 2, and clobbering it breaks their setup:

```bash
python3 - <<'PY'
import json, pathlib, shutil

path = pathlib.Path.home() / ".claude" / "settings.json"
data = {}
if path.exists():
    shutil.copy(path, path.with_name("settings.json.bak"))
    data = json.loads(path.read_text())

attribution = data.setdefault("attribution", {})
attribution["commit"] = ""
attribution["pr"] = ""

path.write_text(json.dumps(data, indent=2) + "\n")
print("attribution ->", json.dumps(attribution))
PY
```

Then confirm nothing else was lost — the gateway keys must still be present
(print key *names* only, never token values):

```bash
python3 -c "import json,pathlib; d=json.loads((pathlib.Path.home()/'.claude/settings.json').read_text()); print('top-level:', sorted(d)); print('env keys:', sorted(d.get('env', {})))"
```

The change applies to new sessions. If they decline, leave it — it is purely
cosmetic and changes nothing about how the gateway works.

## After setup — what the user should know

Tell the user, in plain language:

- **To use Claude Code:** make sure the gateway is running (`python3 main.py` in
  this repo), then run `claude` in any terminal. That's it.
- **Optional shell helper.** Offer to add this to their `~/.zshrc` so they can
  start the gateway from anywhere by typing `kiro-gateway`:

  ```bash
  # Kiro Gateway
  kiro-gateway() {
    local gw_dir="$HOME/repo/kiro-gateway"   # adjust to their clone location
    (cd "$gw_dir" && python3 main.py)
  }
  ```

  Only add it if they say yes, and adjust `gw_dir` to where they actually cloned
  the repo.
- **Credits.** Once the `kiro-credits` skill is installed (Step 5), they can ask
  "quanti crediti Kiro mi restano?" from any session and you'll run it.

## Troubleshooting quick reference

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Could not find profileArn in Kiro IDE logs` | No message sent in Kiro IDE yet | Send one message in Kiro IDE, re-run `./setup.sh -y` |
| Claude Code opens a browser login page | `ANTHROPIC_API_KEY` set instead of `ANTHROPIC_AUTH_TOKEN` | Re-run `./setup.sh -y`; it sets the correct one |
| `runtime.<region>.kiro.dev does not resolve` | Network blocking the endpoint | Set `VPN_PROXY_URL` in `.env` (see README → VPN/Proxy Support) |
| `403 User is not authorized` | Calling Kiro API directly, not through gateway | Point the client at `localhost:8000`, not at Kiro |
| Port 8000 already in use | Another process on 8000 | `python3 main.py --port 9000` and update `ANTHROPIC_BASE_URL` |

## Non-negotiables

- **Do not commit secrets.** `.env`, `credentials.json`, and `state.json` are
  gitignored — keep it that way. Never paste real tokens or ARNs into files that
  are tracked by git, into commit messages, or into chat.
- **Do not hand-edit discovered values.** `profileArn` and `KIRO_API_REGION`
  come from `setup.sh`. If it can't find them, fix the precondition, don't guess.
- **Prefer `./setup.sh -y` over manual steps.** It is the single source of truth
  for configuration and is safe to re-run (it backs up `.env` to `.env.bak`).
