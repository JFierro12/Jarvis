# Agent Instructions for This Repository

## Before touching Meta DAT SDK integration code

Never invent Meta Wearables Device Access Toolkit (DAT) classes, methods, or
capabilities. `docs/META_SDK_NOTES.md` is the verified source of truth, built by cloning
`facebook/meta-wearables-dat-ios` directly and reading its `AGENTS.md`, `CHANGELOG.md`,
and `samples/CameraAccess`. If you need to check something not covered there:

```bash
git clone --depth 1 https://github.com/facebook/meta-wearables-dat-ios /tmp/dat-ref
cat /tmp/dat-ref/AGENTS.md
```

or inspect the resolved package's `.swiftinterface` files directly (the actual compiled
API surface, ground truth over any documentation):

```bash
find ios/JarvisKit/.build/checkouts/meta-wearables-dat-ios -name "*.swiftinterface"
```

If an API is unclear, isolate it behind a protocol (`ios/JarvisKit/Sources/JarvisKit/Core/Protocols/`)
with a mock implementation, and mark the real adapter with a `TODO(hardware-verification)`
comment explaining exactly what needs checking against physical hardware or a newer SDK
version.

## Architecture rules (don't undo these)

- Every hardware/network/AI dependency is a protocol; concrete implementations (mock or
  real) are injected, never referenced directly by `AssistantCoordinator` or SwiftUI
  views. See `docs/ARCHITECTURE.md`.
- `PolicyEngine` (iOS) / `evaluate_tool_call` (backend) is the *only* authority on whether
  a tool call executes. A language model may propose a call; it never authorizes one.
  The backend re-checks policy at `/v1/tools/execute` time independent of the client.
- Context handed to a reasoning provider is always tagged with a `ContextSource`
  (`SYSTEM_POLICY`/`USER_REQUEST`/`TOOL_RESULT`/`CAMERA_OBSERVATION`/`MEMORY_RESULT`/
  `EXTERNAL_CONTENT`). Only the first two may ever authorize an action — see
  `docs/THREAT_MODEL.md` and `backend/tests/test_prompt_injection.py` before changing
  anything in `ContextAssembler` or `app/services/reasoning.py`.
- `AssistantStateMachine`'s transition table (`ios/JarvisKit/Sources/JarvisKit/Core/AssistantState.swift`)
  is exhaustively unit-tested. If you add a new `AssistantState` case or transition,
  update `AssistantStateMachineTests` in the same change.
- Never claim `supportsNativeVoiceInvocation = true` or firmware-level "Jarvis" wake-word
  support without a citation to current SDK documentation proving it.

## Running tests

```bash
./scripts/test.sh          # everything
./scripts/test.sh ios      # JarvisKit (44 tests) + JarvisTests + JarvisUITests
./scripts/test.sh backend  # 26 tests
./scripts/test.sh pc-agent # 9 tests
```

Keep the project compiling/testing after each change — don't leave a half-finished
implementation across a commit boundary.

## Python version note

`backend/pyproject.toml` and `pc-agent/pyproject.toml` declare `requires-python >=3.12`
(the intended target). The sandboxed environment this project was built in only had
Python 3.9 available, so both `.venv`s here were created with 3.9, and both `ruff`
configs are deliberately pinned to `target-version = "py39"` so linting doesn't suggest
3.10+-only syntax that would break that interpreter. If you have 3.12 available, feel
free to recreate the venvs with it — the code doesn't use any 3.9-specific workaround
that would need reverting.

## Don't

- Don't add a raw-shell-command endpoint to `pc-agent` — it only ever accepts allowlisted
  command *IDs* mapped to fixed `argv` lists (`pc-agent/app/commands.py`).
- Don't make the app claim continuous listening/recording, or claim access to a sensor
  or account it doesn't have — this is a hardcoded behavioral rule
  (`JarvisSystemPolicy.text`), not a toggle.
- Don't upgrade the Meta DAT SDK dependency version without re-reading
  `docs/META_SDK_NOTES.md`'s capability matrix against the new version's changelog first —
  API shapes have changed meaningfully between versions (see the 0.8.0 → 0.9.0 diffs in
  the SDK's own `CHANGELOG.md`).
