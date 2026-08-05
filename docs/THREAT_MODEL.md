# Threat Model

## Assets

- The user's iPhone and its Keychain-stored tokens.
- Captured images (transient) and saved memories (persistent, on-device and optionally
  backend).
- The backend's memory database and any configured API keys.
- The pc-agent's ability to run a small set of predefined commands on a personal computer.
- Smart-home control (currently mock-only; Home Assistant adapter is real but requires
  explicit configuration).

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| **Stolen phone** | Auth tokens live in Keychain (`KeychainStore`), not `UserDefaults`. Memories are local-first; an attacker with the phone unlocked has the same access the legitimate user would, which is an OS-level (Face ID/passcode) boundary this app inherits rather than re-implements. |
| **Leaked API credentials** | Backend reads secrets only from environment variables (`app/core/config.py`, `pydantic-settings`); nothing is hardcoded or committed. `.env.example` ships with placeholder values only. `SecretRedaction`/`app/security/redaction.py` strip bearer tokens and key-like strings before anything reaches logs or the diagnostics clipboard. |
| **Compromised backend** | The backend is stateless per-request beyond the memories table; it does not proxy arbitrary user commands. `PolicyEngine`/`evaluate_tool_call` re-validates every tool call at execution time server-side, so a compromised or buggy client can't skip authorization by only calling `/v1/tools/execute` directly. |
| **Malicious prompt injection visible in the environment (e.g. a sign)** | Camera/tool/memory/external text is tagged `CAMERA_OBSERVATION` / `TOOL_RESULT` / `MEMORY_RESULT` / `EXTERNAL_CONTENT` (`ContextSource.canAuthorizeActions == false` for all of these) and is only ever *described*, never treated as an instruction. Enforced by `MockLanguageReasoningProvider`'s injection-marker check in `backend/app/services/reasoning.py` and tested directly in `backend/tests/test_prompt_injection.py`. A real LLM-backed provider must carry the same system-prompt contract (`JarvisSystemPolicy.text` in iOS, mirrored server-side). |
| **Tool-output injection** (a tool's return value tries to trigger another action) | Tool results are `TOOL_RESULT`-tagged context, same untrusted-data treatment as camera text — see the same test file. |
| **Unauthorized PC commands** | pc-agent only accepts a closed set of command *IDs* (`COMMAND_DEFINITIONS` in `pc-agent/app/commands.py`) mapped to fixed `argv` lists — there is no code path that accepts or interpolates a raw command string. Binds to `127.0.0.1` by default, requires a bearer pairing token, and rate-limits requests (`pc-agent/app/ratelimit.py`). |
| **Replay of confirmation audio** | `ConfirmationManager` (iOS) requires a *fresh* proposal per pending action; resolving one clears it (`resolve` consumes `pending`), and each has a real expiry (`PendingAction.isExpired`). There is no "replay this audio to confirm" mechanism — confirmation is driven by the current turn's recognized intent (`.confirm`/`.reject`), not by matching recorded audio. |
| **Accidental bystander capture** | No continuous recording; photo capture is explicit-request-only and not silently retained (see `docs/PRIVACY.md`). The app does not perform facial recognition or attempt to identify people — this is a hardcoded behavioral rule in `JarvisSystemPolicy.text`, not a UI setting a user could accidentally enable. |
| **Location leakage** | `enableLocationContext` defaults to `false`. `ContextAssembler` only includes location-derived fields for intents that need them (spec's least-privilege requirement — see `ContextAssemblerTests`). |
| **Memory database compromise** | Local memories live in the app's sandboxed SwiftData store (subject to standard iOS data-protection). Backend memories require the same bearer-token auth as every other endpoint; there is no separate, weaker path to read them. |
| **Insecure local-network discovery** | pc-agent binds to `127.0.0.1` by default; exposing it on the LAN is an explicit opt-in documented as a real exposure in `pc-agent/.env.example`, not a default. |
| **Dependency compromise** | The Meta DAT SDK is pinned to an exact version (`from: "0.9.0"` / `exactVersion` in the Xcode project) rather than an open-ended range, so a compromised newer release doesn't get pulled in silently. Backend/pc-agent dependencies are declared with explicit minimum versions in `pyproject.toml`. |
| **Model hallucination** | Vision and reasoning responses carry explicit confidence/uncertainty fields (`VisionAnalysisResult.confidence`/`uncertaintyNote`) and the system prompt requires stating uncertainty rather than guessing (`JarvisSystemPolicy.text`). |
| **Excessive permissions** | `PolicyEngine`/`evaluate_tool_call` check `requiredPermissions` per tool against a `grantedPermissions` set that the app/backend controls, not something a model response can expand — a model can *propose* a tool name, never grant itself a permission. |

## Explicitly out of scope for this MVP

- Formal penetration testing of the backend or pc-agent.
- Protection against a fully compromised iPhone (jailbroken, malicious profile installed).
- Multi-tenant backend hardening (this backend assumes a small number of trusted personal
  devices per deployment, not a public multi-user SaaS).
