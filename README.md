# CLP (CLI Proxy) — Local AI Proxy Toolkit

## Overview

CLP is a local CLI proxy that manages and forwards API requests to AI providers such as Claude and Codex. It offers one command-line entry to start/stop/manage multiple proxy services, a multi‑config manager, and a Web UI for monitoring.

## Highlights
- Dynamic config switching in CLI/UI without restarting your client; chat context is preserved
- Request filtering to redact sensitive values before they leave your machine
- Multi‑provider support; switch relay endpoints without rewriting client config files
- Token usage accounting and per‑request logging
- Model routing rules to rewrite model names or pick configs by model
- Load balancing/“account pool” with failure‑aware fallback

## UI Preview

<img width="1145" height="1270" alt="chrome_JrEYQkydwH" src="https://github.com/user-attachments/assets/e182b9ea-cc35-4b4e-9d97-659e218af524" />

## Features

### Core
- Two proxy services: Claude (port 3210) and Codex (port 3211)
- Web UI (port 3300) with live request logs and usage stats
- Streaming responses (SSE/NDJSON) passthrough
- Built‑in request filters and usage extraction
- Optional per-configuration RPM throttling to match upstream rate caps

### Monitoring
- Live status and request/response logs
- Service health via `/health` per proxy (used by the dashboard PID indicator)
- Usage metrics by channel and service
- Config health and switch history

## Tech Stack

- Python 3.8+ (3.11 recommended)
- FastAPI (proxy), Flask (UI)
- httpx (async HTTP), uvicorn (ASGI), psutil (process control)

## Project Layout

```
src/
├── main.py                      # CLI entry (clp)
├── core/
│   └── base_proxy.py           # Shared proxy core
├── claude/
│   ├── configs.py              # Claude config accessors
│   ├── ctl.py                  # Claude controller
│   └── proxy.py                # Claude proxy
├── codex/
│   ├── configs.py              # Codex config accessors
│   ├── ctl.py                  # Codex controller
│   └── proxy.py                # Codex proxy
├── legacy/
│   ├── configs.py              # Legacy proxy config
│   ├── ctl.py                  # Legacy proxy controller
│   └── proxy.py                # Legacy proxy (OpenAI-compatible)
├── config/
│   ├── config_manager.py       # JSON config manager (~/.clp/*.json)
│   └── cached_config_manager.py
├── filter/
│   ├── request_filter.py       # Plain filter
│   └── cached_request_filter.py
├── ui/
│   ├── ctl.py                  # UI controller
│   ├── ui_server.py            # Flask UI server
│   └── static/                 # Frontend assets
└── utils/
    ├── platform_helper.py
    └── usage_parser.py
```

## Quick Start (uv)

### Install
```bash
# Create a virtualenv with uv and install the project in editable mode
uv venv .venv
uv pip install -e . -p .venv

# Start all services (Claude 3210, Codex 3211, UI 3300)
uv run -p .venv clp start
```

If you updated the package, restart the services:
```bash
uv run -p .venv clp restart
```

### CLI Commands
```bash
clp start     # start Claude, Codex, and UI
clp stop      # stop all services
clp restart   # restart all services
clp status    # show status
clp ui        # launch the Web UI (port 3300)

# List and switch configurations
clp list claude
clp list codex
clp active claude prod
clp active codex dev
```

## Using with Claude Code (VS Code)
1) Create `~/.claude/settings.json` to point the extension at the local proxy:
```json
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "-",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:3210/v1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "32000",
    "MAX_THINKING_TOKENS": "30000",
    "DISABLE_AUTOUPDATER": "1"
  },
  "permissions": { "allow": [], "deny": [] }
}
```
2) Restart Claude Code after `clp start`.

> **Endpoint note:** Claude clients must call `/v1/messages` (or `/v1/chat/completions`, which the proxy forwards to `/v1/messages`). Configure the base URL with the `/v1` suffix, for example `http://127.0.0.1:3210/v1`.

### What the Claude proxy adds for you
- **Header normalisation:** requests are forwarded with the same header shape used by the official Claude Code CLI (e.g. `claude-cli/2.0.15` user agent, canonical `x-stainless-*` values, `Accept-Language: *`). This avoids “credential only authorised for Claude Code” errors on providers such as GACCode.
- **Automatic `metadata.user_id`:** if the client omits the Claude-specific metadata block, the proxy injects a stable, Claude-style `user_…_cli_proxy_account__session_…` identifier so upstream APIs still treat the call as coming from Claude Code.
- **Bearer sanitising:** when an `x-api-key` is present the proxy strips any `Authorization: Bearer …` header, matching the behaviour of the real CLI and preventing key-scope validation failures.

## Using with Codex CLI (OpenAI “Responses” API)

The Codex proxy supports OpenAI’s Responses wire protocol and works with custom base URLs.

What we verified (October 11, 2025)
- Endpoint shape: POST `/responses` with `Accept: text/event-stream` and header `OpenAI-Beta: responses=experimental` is forwarded as‑is.
- SSE streaming: Events pass through to the client (e.g., `response.created`, `in_progress`).
- GACCode compatibility: Tested against `https://gaccode.com/codex/v1` and received HTTP 200 with streaming events.

### Configure Codex CLI to use this proxy
Edit `~/.codex/config.toml`:
```properties
model_provider = "local"
model = "gpt-5-codex"          # or "gpt-5"
model_reasoning_effort = "high"
model_reasoning_summary_format = "experimental"
network_access = "enabled"
disable_response_storage = true
show_raw_agent_reasoning = true

[model_providers.local]
name = "local"
base_url = "http://127.0.0.1:3211/v1"
wire_api = "responses"
```

> **Endpoint note:** Codex clients must call `/v1/responses` (or `/v1/chat/completions`, which the proxy rewrites upstream). Always include `/v1` at the end of the base URL you configure (e.g., `http://127.0.0.1:3211/v1`).

### Add an upstream Codex endpoint (GACCode) to the proxy
Create or edit `~/.clp/codex.json` and add a config (you can manage these in the UI as well):
```json
{
  "gaccode": {
    "base_url": "https://gaccode.com/codex/v1",
    "auth_token": "REPLACE_WITH_YOUR_TOKEN",
    "active": true
  }
}
```
Then:
```bash
uv run -p .venv clp restart
clp active codex gaccode
```

### GACCode specifics (what the proxy now handles for you)
- Path rewrite: if the client calls `/responses` and your upstream `base_url` is `https://gaccode.com/codex/v1`, the proxy keeps the request aligned with `/v1/responses` automatically.
- Streaming + beta header: the proxy forces `Accept: text/event-stream` and `OpenAI-Beta: responses=experimental` so Responses streaming always works.
- No compression for SSE: the proxy sets `Accept-Encoding: identity` to avoid `zstd/gzip` compressed event streams that some clients can’t decode.
- Minimal valid body: if a client omits Responses fields, the proxy backfills `store=false`, `stream=true`, and a safe CLI-style `instructions` block.
- Unsupported fields filter: some clients inject optional keys (e.g., `max_output_tokens`, `service_tier`). The proxy removes fields GACCode rejects to prevent `400 Unsupported parameter`.

### Optional: map local model names to upstream names
If your client uses `gpt-5-codex` but the upstream expects `gpt-5-codes`, open the Web UI → Model Router and add a Model→Model mapping for Codex:
- source: `gpt-5-codex`
- target: `gpt-5-codes`

### Quick connectivity tests
Using curl (through the local proxy):
```bash
curl -N \
  -H 'Accept: text/event-stream' \
  -H 'OpenAI-Beta: responses=experimental' \
  -H 'Content-Type: application/json' \
  -d '{
        "model":"gpt-5",
        "input":[{"type":"message","role":"user","content":[{"type":"text","text":"hi"}]}]
      }' \
  http://127.0.0.1:3211/v1/responses
```

Using the built‑in probe (UI → Test Connection), or programmatically calling `CodexProxy.test_endpoint()`.

### Default reasoning settings (UI)

Open the Web UI → **Model Settings (Codex)** to choose per-model defaults for reasoning behaviour:

- **Reasoning Effort** (low/medium/high for both GPT‑5 and GPT‑5‑Codex; GPT‑5 additionally offers *minimal*).
- **Response Detail** (maps to `text.verbosity` low/medium/high).
- **Reasoning Summary** (`off`, `auto`, or `detailed`). `auto` is currently the default for GPT‑5 series and yields the best summary available for the model.

The proxy persists these choices in `~/.clp/data/system.json` and injects them automatically when clients omit the corresponding fields. Unsupported combinations are sanitized (for example, GPT‑5‑Codex never sends `reasoning.effort=minimal`).

## Legacy Proxy Service (port 3212)

The Legacy proxy (`src/legacy/proxy.py`) is a minimal OpenAI-compatible proxy that forwards chat completion requests to any OpenAI-compatible endpoint. It's designed for AI coding agents like Droid CLI and Roo Code.

### Key Features
- **Tool Calling Support**: Full support for OpenAI-style function calling with automatic SSE transformation
- **SSE Streaming**: Transforms non-streaming JSON responses to SSE format for streaming clients
- **RPM Rate Limiting**: Per-provider rate limiting to respect upstream API limits
- **Clean & Minimal**: No unnecessary bloat - just core proxy functionality

### Starting the Legacy Proxy
```bash
# Via CLI
uv run -p .venv clp start

# Or directly
.venv/bin/python -m src.legacy.proxy
```

The proxy listens on port `3212`; use `http://127.0.0.1:3212/v1` as the base URL for client requests.

### Configuration
Create or edit `~/.clp/legacy.json`:
```json
{
  "default": {
    "base_url": "https://your-openai-compatible-api.com/v1",
    "auth_token": "YOUR_API_KEY",
    "active": true
  }
}
```

### Tool Calling (Function Calling)
The legacy proxy fully supports tool calling for models that have the `function_calling` capability:

**Request:**
```bash
curl -X POST http://127.0.0.1:3212/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "your-model-id",
    "messages": [{"role": "user", "content": "Read this file"}],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "Read",
          "description": "Read a file",
          "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}}}
        }
      }
    ],
    "stream": true
  }'
```

**Response (SSE):**
```
data: {"id": "chatcmpl-...", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [{"id": "toolu_...", "type": "function", "function": {"name": "Read", "arguments": "{...}"}}]}, "finish_reason": null}]}

data: {"id": "chatcmpl-...", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}], "usage": {...}}

data: [DONE]
```

**How it works:**
1. Client sends `stream=true` with tools to the proxy
2. Proxy forwards the request with `stream=false` to upstream (A4F and similar providers don't support streaming + tools)
3. Upstream returns a complete JSON response with tool calls
4. Proxy automatically transforms the JSON response into SSE chunks for the client
5. Tool calls appear in SSE delta fields as if they came from a streaming response

### Known Issues
See `docs/OPUS_DROID_ISSUE.md` for documented issues with specific model/client combinations.

### RPM Rate Limiting
Each legacy configuration can declare an optional `rpm_limit`. When set to a positive number the proxy smooths request bursts so the upstream never returns a rate-limit error. Values `0` or `null` disable throttling entirely.

- **Where**: `~/.clp/legacy.json` (or via the UI) → set `"rpm_limit": 10` for 10 requests/minute.
- **How**: Requests queue with a short safety margin (≈10 %) instead of hammering the upstream.
- **Disable**: Remove the field or set it to `0` for unlimited RPM.

### Legacy health endpoint
Every proxy exposes `GET /health` with its PID and active configuration. The dashboard already consumes this endpoint, but you can also poll it from external monitors if you launch services manually.

## Factory/Droid BYOK (works with the proxy)

Example `~/.factory/config.json` entry pointing at the local proxy:
```json
{
  "custom_models": [
    {
      "model_display_name": "GPT-5",
      "model": "gpt-5",
      "base_url": "http://127.0.0.1:3211/v1",
      "api_key": "sk-local-proxy-anything",
      "provider": "openai",
      "max_tokens": 400000,
      "extra_headers": {
        "OpenAI-Beta": "responses=experimental",
        "Accept": "text/event-stream"
      }
    }
  ]
}
```

Notes:
- Set the upstream in `~/.clp/codex.json` to `https://gaccode.com/codex/v1` (include the `/v1` suffix); the proxy keeps downstream `/v1/responses` and `/v1/chat/completions` requests consistent with the upstream path.
- If the client can’t add headers, the proxy now injects the Responses/SSE headers for you.

## Legacy service configuration

- Config file: `~/.clp/legacy.json`
- Each entry needs `base_url`, `auth_token`, and optionally `api_key`, `weight`, `rpm_limit`, etc.
- Set `"rpm_limit": 0` (or omit it) for unlimited throughput; use a positive value to let the proxy pace requests locally.
- Health check: `curl http://127.0.0.1:3212/health` returns JSON with `status`, `pid`, and the active config, which keeps the dashboard in sync even when the service is launched manually.

## Troubleshooting

- 401 Unauthorized
  - Most common cause: key in the wrong place. Put your real key under `auth_token` in `~/.clp/codex.json`. The proxy injects `Authorization: Bearer …` upstream. Your client can use any dummy key.

- 400 Invalid request (e.g., `max_tokens_exceeded`)
  - The legacy proxy now streams upstream validation errors immediately in SSE, so clients display the reason instead of timing out. Adjust the offending parameter (e.g., lower `max_tokens`) and retry.

- 200 but no visible answer
  - Previously caused by compressed SSE (`content-encoding: zstd`) or missing Responses headers; the proxy now forces identity encoding and adds headers. If a UI still doesn’t render, use the helper below to confirm streaming works.

- 400 Unsupported parameter: max_output_tokens (or similar)
  - The proxy now drops unsupported optional fields for `/responses`. If you still see this, open the request row in the UI and verify the request body after filtering.

### Helper endpoints (for quick, curl‑only checks)

- Build a valid Responses body and headers:
  - `POST /api/codex/build-body` with `{ "prompt": "hi", "model": "gpt-5" }` → returns `{ json, headers }` you can pipe to `curl`.
- Send and show the first SSE lines end‑to‑end:
  - `POST /api/codex/quick-send` with `{ "prompt": "hi", "model": "gpt-5", "max_lines": 40 }` → `{ status_code, lines }`.

These helpers hit the local proxy and confirm that upstream credentials, path, and streaming are healthy.

## Configuration Files
CLP stores configs under `~/.clp/`:
- `~/.clp/claude.json` — Claude service configs
- `~/.clp/codex.json` — Codex service configs
- `~/.clp/run/` — runtime files (PID, logs)
- `~/.clp/data/` — logs and usage stats (jsonl)

## Request Filters
Create `~/.clp/filter.json` rules to redact/replace sensitive values before forwarding. See `src/filter/request_filter.py` for the replace/remove operations supported.

## Model Routing and Load Balancing
- Model routing: map model→model or model→config in the Web UI; requests are rewritten before forwarding.
- Load balancing: weight‑based or active‑first with failure thresholds; failed configs are temporarily excluded.

## License

MIT License

## Authors

gjp, edxeth

---

Note: On first run, the proxy may start in a placeholder state. Add at least one upstream config in the UI or in `~/.clp/*.json`, then restart services.
