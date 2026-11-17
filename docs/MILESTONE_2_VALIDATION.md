# Milestone closeout evidence

The existing `Mark-VII` merge and uncommitted security work were preserved.
No commit, push, deployment, database-content migration, model download, or real
PC application launch was performed by this implementation pass.

## Implemented

- Expiring, server-owned device sessions with separately scoped approval secrets,
  one-time enrollment, revocation, logout, and an optional private TLS listener.
- Canonical immutable action records, atomic single-use execution, identity and
  policy binding, bounded stores, explicit failure/unknown results, cancellation
  before dispatch, and an OS-enforced single-runtime lease.
- Public dashboard credential removal, origin/host validation, CSP, native
  navigation restrictions, production debug disablement, and safe text rendering.
- Durable shared conversations, provider/prompt metadata, capability discovery,
  registered-PC routing, and explicit unavailable states.
- Desktop and native iOS clients for the shared runtime. No SwiftUI or coordinator
  code directly imports an operating-system automation implementation.
- Configuration-isolated tests, injectable runtime/device/provider boundaries,
  no database engine at import time, and guards against external test effects.

## Automated results in this Windows session

| Check | Result |
|---|---|
| Original backend suite, before this pass | 128 passed after rerunning outside the sandbox; initial sandbox run had temporary-directory access errors |
| Expanded backend suite | 153 passed; one existing UTC deprecation warning |
| Standalone pc-agent suite, full collection | 9 passed, 9 skipped; fixed argv runners were mocked. The macOS-only Quartz tests now skip by platform rather than breaking collection |
| Backend and changed supporting Python lint | Passed |
| Desktop JavaScript syntax and canonical contract fixture | Passed |
| Canonical JSON schema export drift check | Passed |
| Headless Chromium desktop flow | Passed login, proposal, explicit button confirmation, actual test-result display, cancellation; no page JavaScript errors |
| Windows PyInstaller build | Succeeded in `desktop/dist-milestone2/JARVIS` |
| Built executable `--self-test` | Exit code 0; passed packaged startup, public shell, session authentication, approval separation, isolated memory operation |

The browser smoke test initially found confirmation controls hidden by collapsed
chat. The UI now opens chat for approval and places connection controls in the
visible sidebar. The test passed after that fix.

An independent re-verification pass reproduced every row above on this Windows
host, and found that the pc-agent row had been obtained by naming four test
files explicitly. The whole suite could not be collected at all here, because
`app/gesture.py` imported `Quartz` at module scope: `./scripts/test.sh pc-agent`,
the command AGENTS.md documents, aborted with `ModuleNotFoundError`. The import
is now guarded, `app.gesture.is_available()` reports platform support, and the
public entry points raise `GestureUnavailableError` with a clear message instead
of failing on `None`. The macOS-only tests skip by platform. `app/main.py`
already mounted `/gesture/stream` on darwin only, so no runtime behavior on
macOS changed.

The final regression pass also caught Windows rejecting a second runtime's read
of the locked byte before the explicit lock attempt. Startup now reports the
intended single-owner error. Revocation, logout, policy changes, and expiry also
invalidate consumed actions still waiting for dispatch; already-started actions
cannot be promised to stop.

Commands used (paths relative to the indicated working directory):

```powershell
# backend/
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m ruff check app tests --no-cache
# pc-agent/
..\backend\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
# repository root
node --check backend/app/static/assets/app.js
node scripts/test_contract.cjs
.\backend\.venv\Scripts\python.exe scripts/export_contract.py --check
.\backend\.venv\Scripts\python.exe scripts/smoke_desktop_ui.py
# desktop/
.\venv\Scripts\python.exe -m PyInstaller jarvis.spec --noconfirm --distpath dist-milestone2 --workpath build-milestone2
Start-Process -FilePath .\dist-milestone2\JARVIS\JARVIS.exe -ArgumentList '--self-test' -WindowStyle Hidden -Wait -PassThru
```

## Not verified here

- Swift compilation/Xcode tests: no Swift/Xcode installation is available on this
  Windows host. Shared decoding and coordinator tests are added for macOS CI.
- Physical iPhone, TLS certificate installation, private-network connectivity,
  actual microphone recognition, Bluetooth routes, native WebView2 permission
  behavior, and Ray-Ban Meta hardware were not exercised.
- No real provider quality/latency benchmark, training experiment, or penetration
  test was run. Deterministic fake-provider tests establish contract behavior,
  not real-model prompt-injection immunity.
- The Windows adapter is hosted inside the trusted desktop process. Independent
  remote-agent hosting, automatic discovery, session refresh, all-device memory
  synchronization, and the remaining tool catalog are outside this increment.

## Review and checkpoint

Suggested commit title: `Unify desktop and iOS runtime sessions with explicit action approval`.

Suggested description: Replace public dashboard credentials and transferable
confirmation assertions with enrolled device sessions and server-owned actions.
Add durable conversation handoff, capability discovery, Windows adapter routing,
client migration, canonical contract fixtures, and isolated regression coverage.
Native iPhone/WebView2 hardware verification remains pending.

Review the complete working-tree diff alongside the previously staged database
and egg-info removals. New source, fixtures, tests, contracts, and setup documents
belong in the checkpoint; runtime databases, credentials, binaries, caches, and
build output do not. Nothing has been staged or committed by this pass.
