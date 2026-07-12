# Hosting `meta-data-mcp` as a remote SSE server

This guide walks through deploying `meta-data-mcp` on your own server so that
remote MCP clients (Claude Desktop, Claude Code, Cursor, Windsurf, Gemini CLI,
LM Studio, etc.) can connect over HTTPS without each user spawning the server
locally.

The supported architecture is:

```
   MCP client                       Your server
   (Claude Desktop, etc.)
        │
        │  HTTPS + Authorization: Bearer <token>
        ▼
   ┌─────────────────┐   loopback   ┌──────────────────────────┐
   │  Reverse proxy  │ ───────────► │ meta-data-mcp (SSE)      │
   │  (Caddy / nginx │              │ 127.0.0.1:8000           │
   │  / Cloudflare)  │              │ stdio-style transport in │
   │                 │              │ both directions          │
   └─────────────────┘              └──────────────────────────┘
        ▲
        │  TLS termination + (optional) extra auth layer
        │
   Public internet
```

The server itself only listens on `127.0.0.1`. TLS and the outermost network
boundary belong to a reverse proxy you control. Authentication is enforced by
a built-in bearer-token middleware (see [Authentication](#authentication)).

---

## 1. Quick install (one command)

If you're on Linux with systemd, the bundled installer does steps 1–5 in one shot:

```bash
# From the repo
sudo ./scripts/install-systemd-service.sh --start --contact ops@yourdomain.example

# Or remote-piped (review the script first)
curl -fsSL https://raw.githubusercontent.com/derekslinz/meta-data-mcp/main/scripts/install-systemd-service.sh \
  | sudo bash -s -- --start --contact ops@yourdomain.example
```

What it does (re-runnable; existing env/unit files get `.bak` backups):

1. Creates a service user (`mcp` by default; `--user NAME` to override).
2. Installs `uv` for that user, then `uv tool install meta-data-mcp`.
3. Generates a 32-byte bearer token (`--token VALUE` to supply your own,
   `--rotate-token` to force a fresh one on re-run).
4. Writes `/etc/meta-data-mcp/env` (mode `0600`, owned by the service user)
   with `META_DATA_MCP_AUTH_TOKEN` and optional `META_DATA_MCP_CONTACT`.
5. Writes `/etc/systemd/system/meta-data-mcp.service` with sensible hardening
   (`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only`,
   etc.) and `ExecStart` bound to `127.0.0.1:8000`.
6. `systemctl daemon-reload` and (with `--start`) `enable --now`.

At the end it prints the token once. Skip to step 4 for the TLS reverse proxy.

To run from a source checkout instead of PyPI:

```bash
sudo ./scripts/install-systemd-service.sh --source /opt/meta-data-mcp --start
```

To remove it later:

```bash
sudo ./scripts/install-systemd-service.sh --uninstall
```

The remainder of this guide is the manual breakdown of what that script
does, plus the TLS / client wiring that's the same either way.

## 2. Install on the host (manual)

```bash
# As an unprivileged user on the host (e.g. an Ubuntu droplet)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install meta-data-mcp
# or, from source:
git clone https://github.com/derekslinz/meta-data-mcp.git
cd meta-data-mcp
uv sync
```

Verify:

```bash
# If installed with `uv tool install`:
meta-data-mcp version

# If running from a source checkout after `uv sync`:
uv run meta-data-mcp version
```

## 3. Set the bearer token

The server only requires authentication when the
`META_DATA_MCP_AUTH_TOKEN` environment variable is set. Generate a long
random secret and store it somewhere stable (a systemd `EnvironmentFile`, a
1Password CLI lookup, or your secret manager of choice):

```bash
openssl rand -hex 32
# → e.g. 9e2f...5a1c (paste this into your env file)
```

When the env var is **unset**, the server logs:

```
SSE bearer auth DISABLED — set META_DATA_MCP_AUTH_TOKEN to require
Authorization: Bearer <token> on /sse and /messages
```

and serves SSE traffic without authentication. This is fine for local
development on `127.0.0.1`, but is **not safe for public hosting**. The
sections below assume you've set the variable.

## 4. Run the server bound to localhost

```bash
export META_DATA_MCP_AUTH_TOKEN="$(cat /etc/meta-data-mcp/token)"
meta-data-mcp run --transport sse --host 127.0.0.1 --port 8000
```

On startup you should see:

```
SSE bearer auth enabled (META_DATA_MCP_AUTH_TOKEN set;
protecting /sse and /messages)
Uvicorn running on http://127.0.0.1:8000
```

### systemd unit (recommended)

`/etc/systemd/system/meta-data-mcp.service`:

```ini
[Unit]
Description=meta-data-mcp SSE server
After=network-online.target

[Service]
Type=simple
User=mcp
EnvironmentFile=/etc/meta-data-mcp/env
ExecStart=/home/mcp/.local/bin/meta-data-mcp run --transport sse \
    --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/meta-data-mcp/env` (mode `0600`, owner `mcp:mcp`):

```
META_DATA_MCP_AUTH_TOKEN=...your generated hex...
META_DATA_MCP_CONTACT=ops@yourdomain.example
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now meta-data-mcp
sudo systemctl status meta-data-mcp
```

## 5. Put TLS in front (Caddy example)

The smallest practical Caddy config (`/etc/caddy/Caddyfile`):

```caddyfile
mcp.linzalytics.com {
    reverse_proxy 127.0.0.1:8000 {
        # SSE keeps connections open for hours; bump the proxy timeouts.
        flush_interval -1
        transport http {
            keepalive 5m
            response_header_timeout 0
        }
    }
}
```

Caddy will issue and renew a Let's Encrypt cert automatically as long as
`mcp.linzalytics.com` resolves to your server. Reload with
`sudo systemctl reload caddy`.

> The `flush_interval -1` directive disables response buffering so SSE
> chunks reach the client immediately. Without it, clients see large
> latency spikes and eventual hangs.

### Health check

`/` is intentionally **not** behind the bearer-token middleware so that
uptime monitoring works:

```bash
curl -sS https://mcp.linzalytics.com/ | jq
# → {"status":"running","server":"meta-data-mcp","transport":"sse",...}
```

`/sse` and `/messages` return `401 Unauthorized` without a valid bearer
token:

```bash
curl -sS https://mcp.linzalytics.com/sse -i | head -3
# → HTTP/2 401
# → www-authenticate: Bearer realm="meta-data-mcp"
```

## 6. Wire a client to the hosted server

Run on **your laptop** (not the server):

```bash
. ./.meta-data-mcp.env
meta-data-mcp setup --print-json
```

The stdout of that command is a JSON snippet for the **local stdio**
launcher (still useful as a fallback). The interesting part is on
**stderr**, where the command surfaces the SSE client snippet you'll
actually paste into the remote-client config:

```jsonc
{
  "meta-data-mcp": {
    "type": "sse",
    "url": "https://YOUR-HOST/sse",
    "headers": {
      "Authorization": "Bearer <the-same-token>"
    }
  }
}
```

> **The `"type": "sse"` field matters.** Claude Code validates entries
> under `mcpServers` strictly: without it the entry is treated as a
> stdio launcher, validation expects `command`, and the server is
> skipped with `command: expected string, received undefined`.
> Other clients (Cursor, Windsurf) accept the bare `{url, headers}`
> shape, but including `"type": "sse"` is always safe.

Replace `YOUR-HOST` with your hostname (e.g. `mcp.linzalytics.com`) and
paste the result into:

| Client | Where to paste |
|---|---|
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` under `mcpServers` |
| Claude Code | `~/.claude.json` under `mcpServers` |
| Cursor | `~/.cursor/mcp.json` under `mcpServers` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` under `mcpServers` |
| Gemini CLI | `~/.gemini/settings.json` under `mcpServers` |
| LM Studio | In-app MCP settings UI |

Restart the client. It will connect to your server, send the token on
every request, and see all 66 meta-data-mcp tools just as if the server
were running locally.

## Authentication

| Property | Value |
|---|---|
| Mechanism | `Authorization: Bearer <token>` HTTP header |
| Token source | `META_DATA_MCP_AUTH_TOKEN` environment variable on the server |
| Protected paths | `/sse`, `/messages` |
| Unprotected paths | `/` (health check) |
| Failure response | `401` with `WWW-Authenticate: Bearer realm="meta-data-mcp"` |
| Token comparison | `hmac.compare_digest` (constant-time) |
| Number of tokens | One per server instance (rotate by restarting with a new value) |
| OAuth / per-user tokens | Supported — enable `META_DATA_MCP_OAUTH_ISSUER`, optionally with the [email gate](#email-gate-magic-link-sign-in--per-user-rate-limiting) for per-user identity + rate limiting |

### Threat model

`meta-data-mcp` exposes ~66 read-only public-data providers (NASA, NOAA,
OSM, SEC EDGAR, Crossref, …). Nothing the server returns is sensitive on
its own; the data is already public via the upstream APIs. What the
bearer token defends against is:

- **Abuse / DoS** of your compute and bandwidth budget.
- **Shared rate-limit exhaustion** against upstream APIs that throttle
  per-IP (SEC EDGAR, OpenAlex, Overpass).
- **Outbound UA / contact reputation** — many upstreams require an
  identifying User-Agent (`META_DATA_MCP_CONTACT`) and may ban abusers.

For a single operator (you) and a handful of MCP clients, a single
shared bearer token is the right tool. If you ever need per-user
revocation or audit trails, front the server with Cloudflare Access or
similar — it adds SSO + audit without code changes.

### Rotating the token

```bash
NEW=$(openssl rand -hex 32)
sudo tee /etc/meta-data-mcp/env >/dev/null <<EOF
META_DATA_MCP_AUTH_TOKEN=$NEW
META_DATA_MCP_CONTACT=ops@yourdomain.example
EOF
sudo systemctl restart meta-data-mcp
# Update each client's headers to send the new value.
```

## Email gate (magic-link sign-in + per-user rate limiting)

For a public deployment where you want to know who is connecting and throttle
each user independently — instead of handing out one shared bearer token — turn
on the **magic-link email gate**. It rides the existing OAuth 2.1 flow: at the
consent step the user enters an email, receives a single-use sign-in link, and
clicking it completes the OAuth handshake. The verified email becomes the
rate-limit identity.

Requires OAuth to be enabled (`META_DATA_MCP_OAUTH_ISSUER`).

| Variable | Purpose | Default |
|---|---|---|
| `META_DATA_MCP_EMAIL_GATE` | Set to `1`/`true`/`on` to enable the gate | off |
| `META_DATA_MCP_RATE_LIMIT_RPM` | Requests per minute per verified email (`0` disables throttling) | `30` |
| `META_DATA_MCP_EMAIL_FROM` | Sender address for the sign-in email (required for Resend/SMTP) | — |
| `META_DATA_MCP_RESEND_API_KEY` | Use the [Resend](https://resend.com) HTTP API to send links | — |
| `META_DATA_MCP_SMTP_HOST` | Use SMTP (STARTTLS) instead of Resend | — |
| `META_DATA_MCP_SMTP_PORT` | SMTP port | `587` |
| `META_DATA_MCP_SMTP_USER` / `META_DATA_MCP_SMTP_PASSWORD` | SMTP auth (omit for unauthenticated relays) | — |

**Email backend precedence:** Resend (if `…_RESEND_API_KEY` set) → SMTP (if
`…_SMTP_HOST` set) → **console** (neither set). The console backend logs the
magic link instead of emailing it — useful for local testing, never for
production (anyone reading the logs can sign in). Because of that risk, enabling
the gate with no real backend **refuses to start**; set
`META_DATA_MCP_ALLOW_CONSOLE_EMAIL=1` to explicitly allow the console backend
for local testing.

```bash
# Example: gated public server using Resend, 60 req/min per user.
export META_DATA_MCP_OAUTH_ISSUER="https://mcp.example.com"
export META_DATA_MCP_EMAIL_GATE=1
export META_DATA_MCP_RATE_LIMIT_RPM=60
export META_DATA_MCP_EMAIL_FROM="meta-data-mcp <no-reply@example.com>"
export META_DATA_MCP_RESEND_API_KEY="re_..."
```

**Abuse protection:** the sign-in *request* endpoint is throttled independently
of the per-user API limit — at most 20 requests per client IP and 3 sign-in
emails per address per 15-minute window. An IP over its cap gets `429`; an
address over its cap silently stops receiving emails while still showing the
normal "check your email" page, so a victim's inbox can't be bombed and an
attacker can't tell a throttled address from a delivered one. (Client IP honors
`X-Forwarded-For` from the trusted reverse proxy.)

**Caveats (v1):** the magic-link tokens, the email→token binding, and the
rate-limit counters are all **in-memory and process-local** — a restart forces
every user to re-verify, and the limiter does not share state across workers
(see [Single-worker constraint](#single-worker-constraint)). A durable backend
(SQLite) is a planned follow-up; the store interfaces are already isolated for
it. The static `META_DATA_MCP_AUTH_TOKEN`, if also set, continues to work and is
**not** rate-limited (it's the operator's own key).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `401 Unauthorized` from your own client | Token mismatch between server env and client header | Re-copy the token; verify with `curl -H "Authorization: Bearer $TOKEN" https://host/sse` |
| SSE connects then drops every 60 s | Reverse proxy idle/read timeout too low | Disable response timeout in Caddy/nginx (see Caddy snippet above) |
| Client never sees tools | Client doesn't support remote SSE | Use `setup --client <name>` with the local stdio snippet instead |
| `unauthorized` on `/sse` despite valid header | CORS preflight stripped the header | Already handled — CORS middleware sits outermost; confirm proxy doesn't drop `Authorization` |
| Server warning `auth DISABLED` on startup | `META_DATA_MCP_AUTH_TOKEN` not visible to the process | Verify with `sudo systemctl show meta-data-mcp -p Environment`; reload if you edited the env file |
| `Could not find session for ID: …` after `systemctl restart` | Session state is in-memory; restart wipes every active session | The client must re-handshake (re-open `/sse`, capture the new `endpoint` event, re-send `initialize`). For Claude Code this means running `/mcp` once; if a tool call after that fails with `Invalid request parameters` or `Received request before initialization was complete`, run `/mcp` again — the first reconnect re-opens SSE but Claude Code does not always replay `initialize` automatically. See [Behaviour on restart](#behaviour-on-restart). |
| `Could not write spec file: Read-only file system` from `opendata_plugins_create` | `ProtectSystem=strict` makes the source tree read-only at runtime | The install script ([scripts/install-systemd-service.sh](../scripts/install-systemd-service.sh)) now adds `tools/specs/`, `meta_data_mcp/providers/`, and `tests/providers/` to `ReadWritePaths`. For an existing deployment, edit `/etc/systemd/system/meta-data-mcp.service` to add those three paths, `systemctl daemon-reload`, then restart. |

## Single-worker constraint

**Always run meta-data-mcp with a single uvicorn worker** (the default). The server uses process-level mutable state for:

- **Plugin activation** — `TOOLS`, `TOOLS_HANDLERS`, `_active_providers` are in-memory lists/dicts. A provider activated in worker A is invisible to worker B.
- **OAuth tokens** — `InMemoryOAuthProvider` stores tokens in the Python process. A token issued by worker A cannot be verified by worker B.

If you need horizontal scale, put multiple single-worker instances behind a load balancer with sticky sessions (route by `session_id` cookie or a custom header). The `/` health endpoint is stateless and can be load-balanced freely.

## Behaviour on restart

MCP-over-SSE sessions are **process-local in-memory state**. `SseServerTransport` allocates a fresh `session_id` for every SSE handshake and remembers it only in the running Python process. When the systemd service restarts (or crashes and is restarted), every active session is forgotten.

What that means in practice:

- The reverse proxy keeps the TLS endpoint up, so `https://mcp.YOUR-HOST/` keeps serving the health-check 200 immediately.
- Connected clients see EOF on their SSE stream when the server exits, then reconnect transparently when uvicorn comes back up.
- **However, MCP clients do not always replay the `initialize` handshake against the new session.** Posts to `/messages?session_id=…` against the now-dead session return `404 Could not find session`; posts to the freshly-opened session before `initialize` return JSON-RPC `-32602 Invalid request parameters` (the server logs `Received request before initialization was complete`).

This is upstream MCP-SDK + client behaviour, not specific to meta-data-mcp. The pragmatic fix is to **avoid unnecessary restarts** — the things that currently require one are:

- Code deploys (`git pull && uv sync && systemctl restart`).
- Token rotation (`/etc/meta-data-mcp/env` is loaded only at process start).
- Changes to the systemd unit (`daemon-reload && restart`).

Plugin creation via `opendata_plugins_create` does **not** require a restart — it hot-loads the new module into the running registry, and the new tools become visible on the next `tools/list` from any connected client.

When a restart is unavoidable, clients should explicitly re-handshake:

- **Claude Code**: run `/mcp` and click reconnect on `meta-data-mcp`. If the first tool call after that fails, run `/mcp` once more — the second reconnect tends to also re-send `initialize`.
- **Cursor / Windsurf / Cline**: usually fully renegotiate sessions on EOF without user action.
- **Custom clients**: on SSE-stream EOF, re-open `/sse`, read the new `endpoint` event, then POST a fresh `initialize` before any tool calls.
