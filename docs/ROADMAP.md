# Roadmap

Checklist mirrors the phased plan this project was built against. Checked items were
actually implemented and verified (build/tests run) in this session, not just documented.

## Phase 0 — Discovery

- [x] Inspect installed Meta Wearables Codex plugin / official repo (`facebook/meta-wearables-dat-ios`, cloned and inspected directly — `AGENTS.md`, `CHANGELOG.md`, `samples/CameraAccess`).
- [x] Determine current SDK package version (0.9.0) and deployment target floor (iOS 17.2).
- [x] Write `docs/META_SDK_NOTES.md` with a verified capability matrix.
- [x] Identify unsupported/unverified assumptions (wake word, native voice invocation, background execution) before writing any adapter code.

## Phase 1 — Project foundation

- [x] Repository structure (`docs/`, `ios/`, `backend/`, `pc-agent/`, `scripts/`).
- [x] iOS project: `ios/JarvisKit` (SPM package, real business logic) + `ios/Jarvis` (thin Xcode app shell, hand-authored `project.pbxproj` using Xcode 16+ synchronized-folder groups, verified to parse and build via `xcodebuild`).
- [x] Backend project (`backend/`, FastAPI + SQLAlchemy + pytest).
- [x] pc-agent project (`pc-agent/`, FastAPI + psutil + pytest).
- [x] Ruff configured for both Python projects; linting scripted (`scripts/lint.sh`).
- [x] Baseline projects build/test independently (44 + 2 iOS tests, 26 backend tests, 9 pc-agent tests, all passing).

## Phase 2 — Mock wearable integration

- [x] `WearableDeviceClient` protocol + `MockWearableDeviceClient`.
- [x] `AssistantState` state machine with an explicit, tested transition table.
- [x] Main SwiftUI UI (activation, transcript, response, quick actions, stop button, settings/diagnostics/memories sheets).
- [x] Deterministic demo flow: activate → "what am I looking at" → mock photo → mock vision answer → speak → "remember this" → save → search → delete. Exercised end-to-end in `AssistantCoordinatorTests`.
- [x] Unit tests: state machine, intent routing, memory CRUD, confirmation expiry, policy decisions, frame selector, context minimization.

## Phase 3 — Physical Meta integration

- [x] Official SDK added via Swift Package Manager (real remote dependency, resolves and builds).
- [x] `MetaWearableDeviceClient` implements registration, permission checks, session lifecycle, photo capture (continuation-bridged from the SDK's callback-based `photoDataPublisher`), and video streaming, using only verified API calls (see `docs/META_SDK_NOTES.md`).
- [ ] **Not verified against physical hardware** — no Ray-Ban Meta glasses were available this session. Compiles against the real SDK; behavior only exercised through `MockDeviceKit`.
- [ ] Disconnection/reconnection behavior against a real device — unverified.

## Phase 4 — Audio and speech

- [x] `AVAudioSessionRouteManager` (real `AVAudioSession` category/route/interruption handling).
- [x] `AppleSpeechToTextProvider` (real `Speech` framework, streaming partial results, cancellation, timeout).
- [x] `AppleTextToSpeechProvider` (real `AVSpeechSynthesizer`, barge-in via `stopSpeaking(at: .immediate)`).
- [x] Wired into `AppEnvironment.makeLive(backendBaseURL:)` — `.live` runtime mode now constructs `MetaWearableDeviceClient`, `AppleSpeechToTextProvider`, `AppleTextToSpeechProvider`, and `AVAudioSessionRouteManager` for real, plus `LocalMemoryRepository` (SwiftData) and, when a backend URL is configured, `CloudVisionReasoningProvider`/`CloudLanguageReasoningProvider`. Falls back to mock vision/reasoning if no backend URL is set yet (graceful degradation, spec §27) — never crashes on a missing config. Rebuilt and retested: 44 JarvisKit tests + 2 JarvisTests still pass.
- [ ] Real-device Bluetooth route verification (glasses as active mic/speaker) — unverified without hardware.
- [ ] No UI action yet calls `wearableClient.registerApplication()`/`.connect()` in live mode — connecting to a real device still needs a "Connect" step wired into onboarding or `MainView` (the Meta AI registration handshake needs a real interactive flow, not just object construction).

## Phase 5 — Vision

- [x] `VisionReasoningProvider` protocol, mock implementation with uncertainty-aware responses.
- [x] `CloudVisionReasoningProvider` (real `URLSession`-based client for `POST /v1/vision/analyze`).
- [x] Backend endpoint implemented and tested, including invalid-image-data handling.
- [x] Prompt-injection defense tested explicitly (camera text containing "ignore previous instructions" is described, never acted on).

## Phase 6 — Memory

- [x] Local memory repository: `InMemoryMemoryRepository` (tests/previews) and `LocalMemoryRepository` (real SwiftData-backed).
- [x] Backend memory CRUD + keyword search (`backend/app/memory/`), tested.
- [ ] Backend synchronization from the iOS app is not wired up (protocols support it; no sync scheduler exists yet).
- [x] `MemoriesView` UI: list, search, delete one, delete all (with confirmation dialog).
- [ ] One-tap data export UI action (underlying `export()`/search APIs exist, no button yet).

## Phase 7 — Tools

- [x] `ToolRegistry` / `ToolDefinition` (iOS) and `TOOL_DEFINITIONS` (backend) — matching risk levels and confirmation rules.
- [x] `PolicyEngine` (iOS) / `evaluate_tool_call` (backend) — the sole authority on tool authorization, re-checked server-side at execute time.
- [x] `ConfirmationManager` with real expiry.
- [x] Audit-style structured decision fields (`PolicyDecision`, `AuditEvent` model) — event *logging* (persisting `AuditEvent`s) is modeled but not yet wired to a persistent audit log.
- [ ] Calendar/Reminders EventKit adapters — protocols/tool definitions exist, no concrete implementation.
- [x] pc-agent: real host status (`psutil`) + 5 allowlisted commands, pairing token, rate limiting.
- [x] Smart-home: `SmartHomeClient` protocol, `MockSmartHomeClient`, and a real (untested-without-a-real-instance) `HomeAssistantSmartHomeClient` restricted to low-risk domains.

## Phase 8 — Experimental wake word

- [ ] Not implemented. `WakeWordDetector` protocol and `MockWakeWordDetector` exist;
  no real on-device detector. Explicitly deferred — building this well (with honest
  battery/latency measurement) needs real hardware and dedicated time, and the spec itself
  places it last, behind every core flow being stable first.

## Known follow-ups (not phase-specific)

- No UI entry point calls `AppEnvironment.makeLive(backendBaseURL:)` yet — `JarvisApp.swift`
  still always calls `.makeDemo()`. Wiring a Settings toggle (or a build-configuration
  switch) to pick live mode, plus the "Connect" action noted under Phase 3/4 above, is the
  remaining glue between what's built and what a user on real hardware actually taps.
- Backend reasoning/vision now default to Claude Opus 5 (`JARVIS_REASONING_PROVIDER=anthropic`)
  when configured — see `docs/SETUP_BACKEND.md`. Model is overridable via
  `JARVIS_ANTHROPIC_MODEL` for cost-sensitive personal use (e.g. `claude-sonnet-5`).
- `JarvisUITests` (onboarding → main screen, stop button reachability) are written but
  could not be executed in this session — `xcodebuild test` for the UI test target failed
  with `RequestDenied by service delegate (SBMainWorkspace)` when launching the
  `XCUIApplication` test runner, both with and without the harness sandbox disabled. This
  looks like a headless-environment/Simulator-automation-permission limitation rather than
  a code defect (the same simulator ran the actual app and the host-app `JarvisTests`
  target successfully). Re-run `xcodebuild test -scheme Jarvis -only-testing:JarvisUITests`
  from a normal, logged-in Xcode session to verify.
