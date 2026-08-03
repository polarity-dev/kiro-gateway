<div align="center">

# 👻 Kiro Gateway

**Proxy gateway for Kiro API (Amazon Q Developer / AWS CodeWhisperer)**

🇬🇧 English • [🇷🇺 Русский](docs/ru/README.md) • [🇨🇳 中文](docs/zh/README.md) • [🇪🇸 Español](docs/es/README.md) • [🇮🇩 Indonesia](docs/id/README.md) • [🇧🇷 Português](docs/pt/README.md) • [🇯🇵 日本語](docs/ja/README.md) • [🇰🇷 한국어](docs/ko/README.md)

Made with ❤️ by [@Jwadow](https://github.com/jwadow)

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com/)
[![Sponsor](https://img.shields.io/badge/💖_Sponsor-Support_Development-ff69b4)](#-support-the-project)

*Use Claude models from Kiro with Claude Code, OpenCode, OpenClaw, Claw Code, Codex app, Cursor, Cline, Roo Code, Kilo Code, Obsidian, OpenAI SDK, LangChain, Continue and other OpenAI or Anthropic compatible tools*

[Models](#-supported-models) • [Features](#-features) • [Quick Start](#-quick-start) • [Configuration](#%EF%B8%8F-configuration) • [💖 Sponsor](#-support-the-project)

</div>

---

## 🍴 About This Fork

> This is a **Polarity fork** of [Jwadow/kiro-gateway](https://github.com/Jwadow/kiro-gateway), maintained at
> [polarity-dev/kiro-gateway](https://github.com/polarity-dev/kiro-gateway).
>
> It adds support for **Kiro IDE Enterprise accounts (AWS IAM Identity Center)**, which the upstream
> project does not fully cover. See [Enterprise / IdC Setup](#-enterprise--idc-setup) for the
> automated installer and [Fork Changes](#-fork-changes) for what differs from upstream.

---

## 🚀 Enterprise / IdC Setup

**This repo is both the gateway and the setup kit for running Claude Code against it locally.**
Cloning it gets you everything you need to point Claude Code (and other AI coding tools) at your
Kiro subscription.

### 🤖 Easiest path: let your AI tool do it

If you have **Kiro IDE** (or any AI coding assistant) open in this repo, just ask it:

> *"Set up kiro-gateway and Claude Code for me."*

or run the slash command **`/setup-gateway`**.

The repo ships an always-on setup runbook ([`.kiro/steering/setup.md`](.kiro/steering/setup.md),
referenced from [`CLAUDE.md`](CLAUDE.md) and [`AGENTS.md`](AGENTS.md)) that walks any agent through
the whole flow: checking/installing Claude Code, running the installer, starting the gateway,
verifying it end to end, and offering to install the `kiro-credits` skill globally. Kiro loads it
automatically; Claude Code, Cursor, and Codex pick it up from `CLAUDE.md` / `AGENTS.md`.

### 🛠️ Manual path

**AWS IAM Identity Center users can bootstrap the gateway without installing Kiro IDE or Kiro CLI.**
The only interactive step is approving the standard device authorization code in a browser.

First ensure your AWS CLI config contains an SSO profile (modern `sso_session` and legacy inline
SSO settings are both supported), then run:

```bash
git clone https://github.com/polarity-dev/kiro-gateway.git
cd kiro-gateway
./setup.sh --aws-profile company   # use your AWS CLI profile name
python3 main.py
```

Add `-y` to accept setup prompts automatically. Browser approval is still required. If your user
has multiple Amazon Q Developer profiles, select one by name or ARN:

```bash
./setup.sh -y --aws-profile company --q-profile Engineering
```

Then run `claude` from any terminal. No environment exports or Kiro installation are needed.

> **Prerequisites:** Python 3.10+, an AWS CLI profile containing `sso_start_url` and `sso_region`
> (directly or through `sso_session`), an assigned Amazon Q Developer subscription/profile, and
> [Claude Code](https://code.claude.com/docs/en/setup).

### What setup.sh does

With `--aws-profile`, the installer:

1. registers a public AWS SSO OIDC client with the Amazon Q scopes;
2. starts device authorization and asks you to approve the code in a browser;
3. obtains refreshable OIDC credentials;
4. calls the bearer-authenticated `ListAvailableProfiles` operation in the supported Q regions;
5. stores the selected `profileArn`, SSO region, and API region in an owner-only (`0600`)
   credential file at `~/.aws/sso/cache/kiro-gateway-auth.json`;
6. generates a random `PROXY_API_KEY`, writes `.env`, and atomically synchronizes Claude Code.

The SSO region and Q API region are stored separately because they commonly differ. The generated
credential file is refreshed directly by the gateway; Kiro IDE and Kiro CLI are not involved.

Running `./setup.sh` without `--aws-profile` preserves the legacy path that reuses an existing
Kiro IDE credential file and, when necessary, recovers `profileArn` from its logs.

The script is safe to re-run: it backs up `.env` to `.env.bak` and prompts before overwriting it.

### Troubleshooting

**`AWS profile '…' was not found` / missing SSO settings**
Configure the named profile in `~/.aws/config` with `sso_session`, or with inline `sso_start_url`
and `sso_region`, then retry.

**`This IAM Identity Center user has no Amazon Q Developer profiles`**
Ask the AWS administrator to assign the Amazon Q Developer subscription and profile to the user,
then retry after the assignment has propagated.

**`Multiple Q Developer profiles are available`**
Re-run with `--q-profile NAME_OR_ARN` using one of the choices printed by the command.

**`runtime.<region>.kiro.dev does not resolve`**
Your network is blocking the endpoint. Set `VPN_PROXY_URL` in `.env` — see
[VPN/Proxy Support](#-vpnproxy-support).

**Claude Code opens a browser login page**
`ANTHROPIC_AUTH_TOKEN` must be set, not `ANTHROPIC_API_KEY`. Only the former bypasses Claude
Code's own OAuth flow. `setup.sh` configures this correctly.

### Shell helper (optional)

`setup.sh` writes the Claude Code configuration to `~/.claude/settings.json`, so `claude` works
from any terminal without exporting anything. The only recurring task is starting the gateway.

Add this to `~/.zshrc` to avoid typing the path each time:

```bash
# Kiro Gateway
kiro-gateway() {
  local gw_dir="$HOME/repo/kiro-gateway"   # adjust to your clone location
  local port
  port=$(grep -m1 '^SERVER_PORT=' "$gw_dir/.env" 2>/dev/null | cut -d'"' -f2)
  (cd "$gw_dir" && python3 main.py --port "${port:-4567}")
}
```

Then `kiro-gateway` starts it in the foreground; stop it with Ctrl+C.

> **Why no `export` lines?** Environment variables do not cross terminal sessions, so exporting
> them in the window running the gateway would not reach the window running `claude`. Putting them
> in `~/.claude/settings.json` avoids the problem and also reaches Claude Code's background
> agents, which shell exports do not.

If you prefer environment variables over the settings file, export these instead — but note they
apply only to the shell you set them in:

```bash
export ANTHROPIC_BASE_URL="http://localhost:4567"
export ANTHROPIC_AUTH_TOKEN="<your PROXY_API_KEY from .env>"
```

### Selecting a model

Model discovery is enabled by `setup.sh`, so `/model` inside Claude Code lists the models your
subscription grants, labelled `From gateway`. To inspect them directly:

```bash
curl -s localhost:4567/v1/models -H "Authorization: Bearer $PROXY_API_KEY" \
  | python3 -c "import sys,json; print('\n'.join(m['id'] for m in json.load(sys.stdin)['data']))"
```

The synchronizer selects Kiro's `auto` router and maps Claude Code's virtual
**Default** row to **Kiro Auto**. Haiku remains separately selectable through its
explicit gateway row. Choose any other persistent model with `/model` or the
top-level `model` setting. Do not set `ANTHROPIC_MODEL`: it has higher precedence
and overrides the saved picker choice. When Kiro adds or removes models, refresh
the static local allowlist with:

```bash
python3 scripts/sync_claude_models.py sync
```

Use `--check` to detect drift without writing. The user-level allowlist is local
configuration, not an administrative policy boundary.

---

## 🔧 Fork Changes

Changes in this fork that are not yet in upstream:

**`setup.sh`** — Automated installer for Enterprise / IdC accounts, described above.

**Support for `role: "system"` in the Anthropic endpoint** — Claude Code sends the system prompt
as a message inside the `messages` array, while the Anthropic API specifies it as a separate
top-level `system` field. Upstream rejects these requests with an HTTP 422 validation error.
This fork accepts them:

- `kiro/models_anthropic.py` — `AnthropicMessage.role` accepts `"system"` alongside
  `"user"` and `"assistant"`
- `kiro/converters_anthropic.py` — `build_kiro_payload_anthropic()` filters system messages out
  of the array and merges their text into the system prompt before building the Kiro payload

> **Note:** this change currently covers the Anthropic endpoint only. Upstream's contribution
> guidelines require feature parity across both the OpenAI and Anthropic surfaces plus test
> coverage for streaming and non-streaming paths, so it is not yet suitable for a pull request.

### Syncing with upstream

```bash
git fetch upstream
git merge upstream/main
```

Expect conflicts in `kiro/models_anthropic.py` and `kiro/converters_anthropic.py`, since both
carry fork-specific changes.

---

## 🤖 Available Models

Model availability is discovered from Kiro for the authenticated subscription and
region; this repository intentionally contains no authoritative static model list.
Inspect the current gateway catalog with authenticated `GET /v1/models`, or run
`python3 scripts/sync_claude_models.py sync` to refresh Claude Code's picker.

Non-Claude Kiro IDs are exposed through reversible `claude-kiro-<length>-...`
rows, then decoded back to their raw Kiro `modelId` before inference.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🔌 **OpenAI-compatible API** | Works with any OpenAI-compatible tool |
| 🔌 **Anthropic-compatible API** | Native `/v1/messages` endpoint |
| 🔀 **Multi-Account Support** | Intelligent failover between multiple accounts |
| 🌐 **VPN/Proxy Support** | HTTP/SOCKS5 proxy for restricted networks |
| 🧠 **Extended Thinking** | Reasoning is exclusive to our project |
| 👁️ **Vision Support** | Send images to model |
| 🔍 **Web Search** | Search the web for current information |
| 🛠️ **Tool Calling** | Supports function calling |
| 💬 **Full message history** | Passes complete conversation context |
| 📡 **Streaming** | Full SSE streaming support |
| 🔄 **Retry Logic** | Automatic retries on errors (403, 429, 5xx) |
| 📋 **Extended model list** | Including versioned models |
| 🔐 **Smart token management** | Automatic refresh before expiration |

---

## 🚀 Quick Start

**Choose your deployment method:**
- 🐍 **Native Python** - Full control, easy debugging
- 🐳 **Docker** - Isolated environment, easy deployment → [jump to Docker](#-docker-deployment)

### Prerequisites

- Python 3.10+
- One of the following:
  - [Kiro IDE](https://kiro.dev/) with logged in account, OR
  - [Kiro CLI](https://kiro.dev/cli/) with AWS SSO (AWS IAM Identity Center, OIDC) - free Builder ID or corporate account

### Installation

```bash
# Clone the repository (requires Git)
git clone https://github.com/Jwadow/kiro-gateway.git
cd kiro-gateway

# Or download ZIP: Code → Download ZIP → extract → open kiro-gateway folder

# Install dependencies
pip install -r requirements.txt

# Configure (see Configuration section)
cp .env.example .env
# Copy and edit .env with your credentials

# Start the server
python main.py

# Or with custom port (if 4567 is busy)
python main.py --port 9000
```

The server will be available at `http://localhost:4567`

---

## ⚙️ Configuration

> 💡 **Advanced users:** Looking for multi-account support? See [Account System](#-account-system-advanced) below.

### Option 1: JSON Credentials File (Kiro IDE / Enterprise)

Specify the path to the credentials file:

Works with:
- **Kiro IDE** (standard) - for personal accounts
- **Enterprise** - for corporate accounts with SSO

```env
KIRO_CREDS_FILE="~/.aws/sso/cache/kiro-auth-token.json"

# Password to protect YOUR proxy server (make up any secure string)
# You'll use this as api_key when connecting to your gateway
PROXY_API_KEY="my-super-secret-password-123"
```

<details>
<summary>📄 JSON file format</summary>

```json
{
  "accessToken": "eyJ...",
  "refreshToken": "eyJ...",
  "expiresAt": "2025-01-12T23:00:00.000Z",
  "profileArn": "arn:aws:codewhisperer:us-east-1:...",
  "region": "us-east-1",
  "clientIdHash": "abc123..."  // Optional: for corporate SSO setups
}
```

> **Note:** If you have two JSON files in `~/.aws/sso/cache/` (e.g., `kiro-auth-token.json` and a file with a hash name), use `kiro-auth-token.json` in `KIRO_CREDS_FILE`. The gateway will automatically load the other file.

</details>

### Option 2: Environment Variables (.env file)

Create a `.env` file in the project root:

```env
# Required
REFRESH_TOKEN="your_kiro_refresh_token"

# Password to protect YOUR proxy server (make up any secure string)
PROXY_API_KEY="my-super-secret-password-123"

# Optional
PROFILE_ARN="arn:aws:codewhisperer:us-east-1:..."
KIRO_REGION="us-east-1"
```

### Option 3: AWS SSO Credentials (kiro-cli / Enterprise)

If you use `kiro-cli` or Kiro IDE with AWS SSO (AWS IAM Identity Center), the gateway will automatically detect and use the appropriate authentication.

Works with both free Builder ID accounts and corporate accounts.

```env
KIRO_CREDS_FILE="~/.aws/sso/cache/your-sso-cache-file.json"

# Password to protect YOUR proxy server
PROXY_API_KEY="my-super-secret-password-123"

# Enterprise profileArn is discovered automatically by scripts/kiro_login.py.
# Legacy files that omit it may require PROFILE_ARN in .env.
```

<details>
<summary>📄 AWS SSO JSON file format</summary>

AWS SSO credentials files (from `~/.aws/sso/cache/`) contain:

```json
{
  "accessToken": "eyJ...",
  "refreshToken": "eyJ...",
  "expiresAt": "2025-01-12T23:00:00.000Z",
  "region": "us-east-1",
  "clientId": "...",
  "clientSecret": "..."
}
```

**Note:** direct Enterprise bootstrap discovers `profileArn` automatically. Builder ID accounts may not have an Enterprise profile ARN; legacy corporate files that omit it may require `PROFILE_ARN` in `.env`.

</details>

<details>
<summary>🔍 How it works</summary>

The gateway automatically detects the authentication type based on the credentials file:

- **Kiro Desktop Auth** (default): Used when `clientId` and `clientSecret` are NOT present
  - Endpoint: `https://prod.{region}.auth.desktop.kiro.dev/refreshToken`
  
- **AWS SSO (OIDC)**: Used when `clientId` and `clientSecret` ARE present
  - Endpoint: `https://oidc.{region}.amazonaws.com/token`

No additional configuration is needed — just point to your credentials file!

</details>

### Option 4: kiro-cli SQLite Database

If you use `kiro-cli` and prefer to use its SQLite database directly:

```env
KIRO_CLI_DB_FILE="~/.local/share/kiro-cli/data.sqlite3"

# Password to protect YOUR proxy server
PROXY_API_KEY="my-super-secret-password-123"

# Enterprise profileArn is discovered automatically by scripts/kiro_login.py.
# Legacy files that omit it may require PROFILE_ARN in .env.
```

<details>
<summary>📄 Database locations</summary>

| CLI Tool | Database Path |
|----------|---------------|
| kiro-cli | `~/.local/share/kiro-cli/data.sqlite3` |
| amazon-q-developer-cli | `~/.local/share/amazon-q/data.sqlite3` |

The gateway reads credentials from the `auth_kv` table which stores:
- `kirocli:odic:token` or `codewhisperer:odic:token` — access token, refresh token, expiration
- `kirocli:odic:device-registration` or `codewhisperer:odic:device-registration` — client ID and secret

Both key formats are supported for compatibility with different kiro-cli versions.

</details>

### Getting Credentials

**For Kiro IDE users:**
- Log in to Kiro IDE and use Option 1 above (JSON credentials file)
- The credentials file is created automatically after login

**For Kiro CLI users:**
- Log in with `kiro-cli login` and use Option 3 or Option 4 above
- No manual token extraction needed!

<details>
<summary>🔧 Advanced: Manual token extraction</summary>

If you need to manually extract the refresh token (e.g., for debugging), you can intercept Kiro IDE traffic:
- Look for requests to: `prod.us-east-1.auth.desktop.kiro.dev/refreshToken`

</details>

---

## 🔀 Account System (Advanced)

Account System is a way to manage multiple Kiro accounts with automatic failover. In the future, this system will replace `.env` file for credential configuration, but currently it's optional and intended for those who want to use multiple accounts.

### Why You Need This

If you have multiple Kiro accounts, the gateway can automatically switch between them when account is temporarily unavailable.

The system works with a single account too — just without switching.

### How to Enable

Add to your `.env`:

```env
ACCOUNT_SYSTEM=true
```

**What happens:**
- On first startup, your credentials from `.env` are automatically migrated to `credentials.json` (one-time)
- After that, all account and region settings from `.env` are ignored
- Account management only through `credentials.json`

<details>
<summary>📄 Configuration Examples</summary>

**Single account:**
```json
[
  {
    "type": "json",
    "path": "~/.aws/sso/cache/kiro-auth-token.json"
  }
]
```

**Multiple accounts:**
```json
[
  {
    "type": "json",
    "path": "~/.aws/sso/cache/kiro-auth-token.json"
  },
  {
    "type": "sqlite",
    "path": "~/.local/share/kiro-cli/data.sqlite3"
  },
  {
    "type": "refresh_token",
    "refresh_token": "eyJhbGc...",
    "profile_arn": "arn:aws:codewhisperer:us-east-1:..."
  }
]
```

**Folder with files:**
```json
[
  {
    "type": "json",
    "path": "C:\\MyAccs\\kiro67"
  }
]
```

The gateway will scan all files in the folder and add them as separate accounts.

</details>

### How Failover Works

When one account returns an error (429 rate limit, 402 quota exceeded), the gateway automatically tries the next account from the list. If an account fails several times in a row, the gateway temporarily stops using it and periodically checks if it has recovered.

For a single account, failover doesn't work — you get the original error from Kiro API.

For complete configuration examples (including per-account region settings), see [`credentials.json.example`](credentials.json.example).

---

## 🐳 Docker Deployment

> **Docker-based deployment.** Prefer native Python? See [Quick Start](#-quick-start) above.

### Quick Start

```bash
# 1. Clone and configure
git clone https://github.com/Jwadow/kiro-gateway.git
cd kiro-gateway
cp .env.example .env
# Edit .env with your credentials

# 2. Run with docker-compose
docker-compose up -d

# 3. Check status
docker-compose logs -f
curl http://localhost:4567/health
```

### Docker Run (Without Compose)

<details>
<summary>🔹 Using Environment Variables</summary>

```bash
docker run -d \
  -p 4567:4567 \
  -e PROXY_API_KEY="my-super-secret-password-123" \
  -e REFRESH_TOKEN="your_refresh_token" \
  --name kiro-gateway \
  ghcr.io/jwadow/kiro-gateway:latest
```

</details>

<details>
<summary>🔹 Using Credentials File</summary>

**Linux/macOS:**
```bash
docker run -d \
  -p 4567:4567 \
  -v ~/.aws/sso/cache:/home/kiro/.aws/sso/cache:ro \
  -e KIRO_CREDS_FILE=/home/kiro/.aws/sso/cache/kiro-auth-token.json \
  -e PROXY_API_KEY="my-super-secret-password-123" \
  --name kiro-gateway \
  ghcr.io/jwadow/kiro-gateway:latest
```

**Windows (PowerShell):**
```powershell
docker run -d `
  -p 4567:4567 `
  -v ${HOME}/.aws/sso/cache:/home/kiro/.aws/sso/cache:ro `
  -e KIRO_CREDS_FILE=/home/kiro/.aws/sso/cache/kiro-auth-token.json `
  -e PROXY_API_KEY="my-super-secret-password-123" `
  --name kiro-gateway `
  ghcr.io/jwadow/kiro-gateway:latest
```

</details>

<details>
<summary>🔹 Using .env File</summary>

```bash
docker run -d -p 4567:4567 --env-file .env --name kiro-gateway ghcr.io/jwadow/kiro-gateway:latest
```

</details>

### Docker Compose Configuration

Edit `docker-compose.yml` and uncomment volume mounts for your OS:

```yaml
volumes:
  # Kiro IDE credentials (choose your OS)
  - ~/.aws/sso/cache:/home/kiro/.aws/sso/cache:ro              # Linux/macOS
  # - ${USERPROFILE}/.aws/sso/cache:/home/kiro/.aws/sso/cache:ro  # Windows
  
  # kiro-cli database (choose your OS)
  - ~/.local/share/kiro-cli:/home/kiro/.local/share/kiro-cli  # Linux/macOS
  # - ${USERPROFILE}/.local/share/kiro-cli:/home/kiro/.local/share/kiro-cli  # Windows
  
  # Debug logs (optional)
  - ./debug_logs:/app/debug_logs
```

### Management Commands

```bash
docker-compose logs -f      # View logs
docker-compose restart      # Restart
docker-compose down         # Stop
docker-compose pull && docker-compose up -d  # Update
```

<details>
<summary>🔧 Building from Source</summary>

```bash
docker build -t kiro-gateway .
docker run -d -p 4567:4567 --env-file .env kiro-gateway
```

</details>

---

## 🌐 VPN/Proxy Support

**For users in China, corporate networks, or regions with connectivity issues to AWS services.**

The gateway supports routing all Kiro API requests through a VPN or proxy server. This is essential if you experience connection problems to AWS endpoints or need to use a corporate proxy.

### Configuration

Add to your `.env` file:

```env
# HTTP proxy
VPN_PROXY_URL=http://127.0.0.1:7890

# SOCKS5 proxy
VPN_PROXY_URL=socks5://127.0.0.1:1080

# With authentication (corporate proxies)
VPN_PROXY_URL=http://username:password@proxy.company.com:8080

# Without protocol (defaults to http://)
VPN_PROXY_URL=192.168.1.100:8080
```

### Supported Protocols

- ✅ **HTTP** — Standard proxy protocol
- ✅ **HTTPS** — Secure proxy connections
- ✅ **SOCKS5** — Advanced proxy protocol (common in VPN software)
- ✅ **Authentication** — Username/password embedded in URL

### When You Need This

| Situation | Solution |
|-----------|----------|
| Connection timeouts to AWS | Use VPN/proxy to route traffic |
| Corporate network restrictions | Configure your company's proxy |
| Regional connectivity issues | Use a VPN service with proxy support |
| Privacy requirements | Route through your own proxy server |

### Popular VPN Software with Proxy Support

Most VPN clients provide a local proxy server you can use:
- **Sing-box** — Modern VPN client with HTTP/SOCKS5 proxy
- **Clash** — Usually runs on `http://127.0.0.1:7890`
- **V2Ray** — Configurable SOCKS5/HTTP proxy
- **Shadowsocks** — SOCKS5 proxy support
- **Corporate VPN** — Check your IT department for proxy settings

Leave `VPN_PROXY_URL` empty (default) if you don't need proxy support.

---

## 📡 API Reference

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check |
| `/health` | GET | Detailed health check |
| `/v1/models` | GET | List available models |
| `/v1/chat/completions` | POST | OpenAI Chat Completions API |
| `/v1/messages` | POST | Anthropic Messages API |

---

## 💡 Usage Examples

### OpenAI API

<details>
<summary>🔹 Simple cURL Request</summary>

```bash
curl http://localhost:4567/v1/chat/completions \
  -H "Authorization: Bearer my-super-secret-password-123" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

> **Note:** Replace `my-super-secret-password-123` with the `PROXY_API_KEY` you set in your `.env` file.

</details>

<details>
<summary>🔹 Streaming Request</summary>

```bash
curl http://localhost:4567/v1/chat/completions \
  -H "Authorization: Bearer my-super-secret-password-123" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "What is 2+2?"}
    ],
    "stream": true
  }'
```

</details>

<details>
<summary>🛠️ With Tool Calling</summary>

```bash
curl http://localhost:4567/v1/chat/completions \
  -H "Authorization: Bearer my-super-secret-password-123" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "messages": [{"role": "user", "content": "What is the weather in London?"}],
    "tools": [{
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get weather for a location",
        "parameters": {
          "type": "object",
          "properties": {
            "location": {"type": "string", "description": "City name"}
          },
          "required": ["location"]
        }
      }
    }]
  }'
```

</details>

<details>
<summary>🐍 Python OpenAI SDK</summary>

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:4567/v1",
    api_key="my-super-secret-password-123"  # Your PROXY_API_KEY from .env
)

response = client.chat.completions.create(
    model="claude-sonnet-4-5",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"}
    ],
    stream=True
)

for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

</details>

<details>
<summary>🦜 LangChain</summary>

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:4567/v1",
    api_key="my-super-secret-password-123",  # Your PROXY_API_KEY from .env
    model="claude-sonnet-4-5"
)

response = llm.invoke("Hello, how are you?")
print(response.content)
```

</details>

### Anthropic API

<details>
<summary>🔹 Simple cURL Request</summary>

```bash
curl http://localhost:4567/v1/messages \
  -H "x-api-key: my-super-secret-password-123" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

> **Note:** Anthropic API uses `x-api-key` header instead of `Authorization: Bearer`. Both are supported.

</details>

<details>
<summary>🔹 With System Prompt</summary>

```bash
curl http://localhost:4567/v1/messages \
  -H "x-api-key: my-super-secret-password-123" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 1024,
    "system": "You are a helpful assistant.",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

> **Note:** In Anthropic API, `system` is a separate field, not a message.

</details>

<details>
<summary>📡 Streaming</summary>

```bash
curl http://localhost:4567/v1/messages \
  -H "x-api-key: my-super-secret-password-123" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 1024,
    "stream": true,
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

</details>

<details>
<summary>🐍 Python Anthropic SDK</summary>

```python
import anthropic

client = anthropic.Anthropic(
    api_key="my-super-secret-password-123",  # Your PROXY_API_KEY from .env
    base_url="http://localhost:4567"
)

# Non-streaming
response = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.content[0].text)

# Streaming
with client.messages.stream(
    model="claude-sonnet-4-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}]
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

</details>

---

## 🔧 Debugging

Debug logging is **disabled by default**. To enable, add to your `.env`:

```env
# Debug logging mode:
# - off: disabled (default)
# - errors: save logs only for failed requests (4xx, 5xx) - recommended for troubleshooting
# - all: save logs for every request (overwrites on each request)
DEBUG_MODE=errors
```

### Debug Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `off` | Disabled (default) | Production |
| `errors` | Save logs only for failed requests (4xx, 5xx) | **Recommended for troubleshooting** |
| `all` | Save logs for every request | Development/debugging |

### Debug Files

When enabled, requests are logged to the `debug_logs/` folder:

| File | Description |
|------|-------------|
| `request_body.json` | Incoming request from client (OpenAI format) |
| `kiro_request_body.json` | Request sent to Kiro API |
| `response_stream_raw.txt` | Raw stream from Kiro |
| `response_stream_modified.txt` | Transformed stream (OpenAI format) |
| `app_logs.txt` | Application logs for the request |
| `error_info.json` | Error details (only on errors) |

---

## 🔧 Troubleshooting

### Connection Issues

**Error: "Name or service not known" or DNS resolution failed**

The Q API endpoint may not be publicly resolvable in your region. Use a VPN or proxy:

```env
VPN_PROXY_URL=http://127.0.0.1:7890
```

See [VPN/Proxy Support](#-vpnproxy-support) for details.

---

**Error: "503 Service Unavailable" through proxy**

The Q API endpoint exists in specific regions only. Try a different region:

```env
KIRO_API_REGION="eu-central-1"  # or us-east-1
```

Commonly reachable regions: `us-east-1`, `eu-central-1`

---

**OIDC works but Q API fails**

Your SSO region may differ from the Q API region. The gateway auto-detects this from credentials, but you can override:

```env
KIRO_API_REGION="eu-central-1"
```

---

## 📜 License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

This means:
- ✅ You can use, modify, and distribute this software
- ✅ You can use it for commercial purposes
- ⚠️ **You must disclose source code** when you distribute the software
- ⚠️ **Network use is distribution** — if you run a modified version on a server and let others interact with it, you must make the source code available to them
- ⚠️ Modifications must be released under the same license

See the [LICENSE](LICENSE) file for the full license text.

### Why AGPL-3.0?

AGPL-3.0 ensures that improvements to this software benefit the entire community. If you modify this gateway and deploy it as a service, you must share your improvements with your users.

### Contributor License Agreement (CLA)

By submitting a contribution to this project, you agree to the terms of our [Contributor License Agreement (CLA)](CLA.md). This ensures that:
- You have the right to submit the contribution
- You grant the maintainer rights to use and relicense your contribution
- The project remains legally protected

---

## 💖 Support the Project

<div align="center">

<img src="https://raw.githubusercontent.com/Tarikul-Islam-Anik/Animated-Fluent-Emojis/master/Emojis/Smilies/Smiling%20Face%20with%20Hearts.png" alt="Love" width="80" />

**If this project saved you time or money, consider supporting it!**

Every contribution helps keep this project alive and growing

<br>

### 🤑 Donate

[**☕ One-time Support**](https://app.lava.top/products/b4e34d12-3b6b-49b7-be50-50b6a20ed262/f3ea941f-de73-4ad1-bbb6-f82042ef8132)

<br>

### 🪙 Or send crypto

| Currency | Network | Address |
|:--------:|:-------:|:--------|
| **USDT** | TRC20 | `TSVtgRc9pkC1UgcbVeijBHjFmpkYHDRu26` |
| **BTC** | Bitcoin | `12GZqxqpcBsqJ4Vf1YreLqwoMGvzBPgJq6` |
| **ETH** | Ethereum | `0xc86eab3bba3bbaf4eb5b5fff8586f1460f1fd395` |
| **SOL** | Solana | `9amykF7KibZmdaw66a1oqYJyi75fRqgdsqnG66AK3jvh` |
| **TON** | TON | `UQBVh8T1H3GI7gd7b-_PPNnxHYYxptrcCVf3qQk5v41h3QTM` |

</div>

---

## ⚠️ Disclaimer

This project is not affiliated with, endorsed by, or sponsored by Amazon Web Services (AWS), Anthropic, or Kiro IDE. Use at your own risk and in compliance with the terms of service of the underlying APIs.

---

<div align="center">

**[⬆ Back to Top](#-kiro-gateway)**

</div>
