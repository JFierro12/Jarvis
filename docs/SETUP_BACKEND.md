# Backend Setup

## Prerequisites

The backend targets **Python 3.12+** (`backend/pyproject.toml`). This sandboxed build
environment only had Python 3.9.6 available, so the dev virtualenv used to verify tests
in this session (`backend/.venv`) was created with 3.9 — everything used is 3.9-compatible,
but you should use 3.12 for real development:

```bash
# macOS, via Homebrew:
brew install python@3.12
```

## Install

```bash
cd backend
python3.12 -m venv .venv   # or python3, if that's already 3.12+
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit .env — at minimum, change JARVIS_AUTH_TOKENS to a real generated token:
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

Then check `http://127.0.0.1:8000/health` and the auto-generated OpenAPI docs at
`http://127.0.0.1:8000/docs`.

### Making it reachable from your phone

The command above only listens on `127.0.0.1` — reachable from the Mac itself, not from a
phone on the same Wi-Fi network. To let the iOS app (in Live Mode) reach it:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then find your Mac's LAN IP:

```bash
ipconfig getifaddr en0   # or: System Settings > Wi-Fi > (network) > Details > IP Address
```

In the app, Settings → Connection → **Backend URL**, enter `http://<that-ip>:8000` (e.g.
`http://192.168.1.75:8000`) — plain HTTP is fine for local-network dev, but note this
means requests aren't encrypted on your LAN. Your Mac and phone must be on the same
Wi-Fi network, and if the connection times out, check System Settings → Network →
Firewall isn't blocking incoming connections to `uvicorn`/Python. The IP address can
change when your Mac reconnects to Wi-Fi — re-check it if the app stops reaching the
backend.

The bearer token you set in Settings → Connection → **Backend Auth Token** must match
one of the comma-separated values in `JARVIS_AUTH_TOKENS` in `backend/.env` (default:
`dev-local-token`).

## Run tests

```bash
python -m pytest tests/ -v
```

32 tests covering: health, auth (missing/invalid/valid token), memory CRUD + idempotent
delete, tool policy decisions (allow/deny/require-confirmation), vision request
validation, prompt-injection defense (camera/tool-result text is described, never
followed as an instruction), and the Anthropic-backed providers (request construction and
response parsing against a fake client — no real key or network needed to run the suite).

## Configuration reference

All settings are environment variables with a `JARVIS_` prefix (`app/core/config.py`):

| Variable | Default | Notes |
|---|---|---|
| `JARVIS_AUTH_TOKENS` | `dev-local-token` | Comma-separated bearer tokens. Change before any real use. |
| `JARVIS_DATABASE_URL` | `sqlite:///./jarvis.db` | Use a `postgresql://` URL in production. |
| `JARVIS_REASONING_PROVIDER` | `mock` | `mock` (deterministic, offline) or `anthropic` (real Claude reasoning — see below). |
| `JARVIS_VISION_PROVIDER` | `mock` | `mock` or `anthropic` (real Claude vision). |
| `JARVIS_REASONING_API_KEY` / `JARVIS_VISION_API_KEY` | empty | Only read when the corresponding provider is `anthropic`. Leave empty to let the Anthropic SDK resolve credentials itself (`ANTHROPIC_API_KEY` env var, or an `ant auth login` profile). |
| `JARVIS_ANTHROPIC_MODEL` | `claude-opus-5` | Override for cost-sensitive personal use, e.g. `claude-sonnet-5` or `claude-haiku-4-5`. |

## Using real Claude reasoning/vision

```bash
# In backend/.env:
JARVIS_REASONING_PROVIDER=anthropic
JARVIS_VISION_PROVIDER=anthropic
JARVIS_ANTHROPIC_MODEL=claude-opus-5   # or claude-sonnet-5 for lower cost
# Leave the API key fields empty if ANTHROPIC_API_KEY is already set in your
# shell, or set JARVIS_REASONING_API_KEY / JARVIS_VISION_API_KEY explicitly.
```

Implemented in `app/services/anthropic_reasoning.py` and `anthropic_vision.py`. Both use
`output_config.format` (a JSON schema) rather than Anthropic tool-calling, so Claude only
ever *proposes* a tool name + target — this backend's own `PolicyEngine`
(`app/tools/policy.py`) is still the only thing that authorizes or executes anything, and
the model has no schema field to smuggle arbitrary tool arguments through. The system
prompt repeats the same untrusted-data contract as `MockLanguageReasoningProvider`
(camera/tool/memory/external content is never an instruction) — keep both in sync if you
change one.

## Adding a different reasoning/vision provider

Both `app/services/reasoning.py` and `app/services/vision.py` define a `Protocol`
(`LanguageReasoningProvider`, `VisionReasoningProvider`) and a factory function
(`get_reasoning_provider`, `get_vision_provider`). Add a new class implementing the same
protocol, branch on `settings.reasoning_provider` / `settings.vision_provider` in the
factory (alongside the existing `"anthropic"` branch), and keep the prompt-injection
contract from `docs/THREAT_MODEL.md` (camera/tool/memory/external content is data, never
instructions).

## pc-agent (separate service)

See `pc-agent/` — it's an independent FastAPI service, not part of this backend process,
meant to run on a user's own PC. Setup is the same pattern (venv, `.env`, `uvicorn`); see
its own inline docs in `pc-agent/app/`.
