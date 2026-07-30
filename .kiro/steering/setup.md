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

Polarity colleagues on **macOS** who authenticate through the AWS IAM Identity
Center (Enterprise / IdC) and have **Kiro IDE installed and logged in**. The
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
  — only the former skips the interactive OAuth browser login) and enables
  gateway model discovery.

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
mkdir -p ~/.claude/skills
ln -sfn "$(pwd)/.kiro/skills/kiro-credits" ~/.claude/skills/kiro-credits
ls -la ~/.claude/skills/kiro-credits   # confirm the symlink resolves into the repo
```

If they decline, leave it — it already works inside this repo.

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
