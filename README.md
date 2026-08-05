# JARVIS

A privacy-conscious, voice-first ambient AI assistant prototype for Ray-Ban Meta smart
glasses + iPhone — the closest realistically achievable approximation of Tony Stark's
JARVIS using currently-shipping hardware and the Meta Wearables Device Access Toolkit
(DAT), which is in developer preview.

This is a real, building, testing monorepo — not a design document. See
`docs/ROADMAP.md` for exactly what's implemented vs. planned, and
`docs/LIMITATIONS.md` for what this app can and cannot do relative to Meta's own
firmware.

## What's currently functional

- **iOS app + JarvisKit** (`ios/`): full demo-mode flow — activate, ask "what am I
  looking at?", get a mock vision answer spoken back, say "remember this", search
  memory, delete a memory — all backed by deterministic mocks, no credentials needed.
  44 passing unit tests in `JarvisKit`, 2 in the app target. Builds and runs in the iOS
  Simulator; screenshots/manual verification confirm the onboarding → main flow works.
- **Real Meta DAT SDK integration** (`MetaWearableDeviceClient`): compiles against the
  actual `facebook/meta-wearables-dat-ios` package (registration, permissions, session
  lifecycle, photo capture, video streaming) using only verified, documented API calls —
  see `docs/META_SDK_NOTES.md`. **Not yet verified against physical glasses** (none were
  available while building this).
- **Backend** (`backend/`): FastAPI service implementing every endpoint in the spec
  (session, transcribe, reason, vision, speech, memories, tools, PC integration), a policy
  engine mirroring the iOS one, and prompt-injection defense. Reasoning and vision are
  backed by **Claude Opus 5** (`JARVIS_REASONING_PROVIDER=anthropic` /
  `JARVIS_VISION_PROVIDER=anthropic`, model overridable via `JARVIS_ANTHROPIC_MODEL`) via
  structured JSON-schema output — falls back to deterministic mocks with no key/network
  needed. 32 passing tests.
- **pc-agent** (`pc-agent/`): localhost-bound FastAPI service exposing read-only host
  metrics and 5 allowlisted commands (never a raw shell string), pairing-token auth, rate
  limiting. 9 passing tests.
- **`AppEnvironment.makeLive(backendBaseURL:)`**: builds the real iOS stack —
  `MetaWearableDeviceClient`, `AppleSpeechToTextProvider`, `AppleTextToSpeechProvider`,
  `AVAudioSessionRouteManager`, `LocalMemoryRepository` (SwiftData), and (once a backend
  URL is supplied) `CloudVisionReasoningProvider`/`CloudLanguageReasoningProvider` talking
  to the Claude-backed backend above. Compiles and passes all 46 iOS tests; not yet wired
  to a UI toggle (`JarvisApp.swift` still calls `.makeDemo()` — see `docs/ROADMAP.md`) and
  not yet verified against physical glasses.

## What's mocked / not yet wired up

- No UI switch calls `AppEnvironment.makeLive(backendBaseURL:)` yet — see
  `docs/ROADMAP.md` for the remaining glue (a live-mode toggle, plus a "Connect" action to
  actually call `registerApplication()`/`connect()` on the glasses).
- Calendar/Reminders (EventKit), Home Assistant (implemented but unexercised without a
  real instance), Meta Ray-Ban Display rendering, and the experimental foreground wake
  word are not implemented. Full breakdown in `docs/ROADMAP.md`.

## Required hardware / accounts

- **For demo mode:** none. A Mac with Xcode is enough.
- **For live glasses integration:** an iPhone, Ray-Ban Meta glasses (or Meta Ray-Ban
  Display), the Meta AI companion app, and Developer Mode enabled on the glasses. See
  `docs/SETUP_IOS.md`.
- **For the backend/pc-agent:** just Python 3.12+ (see `docs/SETUP_BACKEND.md`).

## Quick start

```bash
# iOS demo mode — no setup needed beyond Xcode:
open ios/Jarvis/Jarvis.xcodeproj
# Build & run on any iOS 17.2+ simulator. That's the whole demo.

# Backend + pc-agent:
./scripts/bootstrap.sh
./scripts/test.sh
```

See `docs/SETUP_IOS.md` and `docs/SETUP_BACKEND.md` for full setup, troubleshooting, and
how to move from demo mode toward real hardware/cloud providers.

## Running tests

```bash
./scripts/test.sh          # everything
./scripts/test.sh ios
./scripts/test.sh backend
./scripts/test.sh pc-agent
```

`JarvisUITests` are written but could not be executed in the sandboxed session that built
this project (a Simulator/automation-permission limitation, not a code issue — see
`docs/ROADMAP.md`). Run them from a normal, interactive Xcode session to verify.

## Privacy

Read `docs/PRIVACY.md` before using this with real glasses. In short: no continuous
recording, photo capture only on explicit request, images deleted unless you say
"remember this," and this app does not replace or control Meta's own "Hey Meta"
assistant.

## Known wake-word limitation

There is no way for a third-party app to make Ray-Ban Meta glasses respond to the word
"Jarvis" at the firmware level — "Hey Meta" is Meta's own, and the DAT SDK does not
expose a wake-word or native-voice-invocation API (verified directly against the SDK; see
`docs/META_SDK_NOTES.md`). This project's activation model is push-to-talk /
press-to-activate-session / (optionally) an on-phone, foreground-only experimental wake
word — never a claim of glasses-level "Jarvis" support.

## Documentation map

- `docs/ARCHITECTURE.md` — system/sequence/state diagrams.
- `docs/META_SDK_NOTES.md` — verified DAT SDK capability matrix.
- `docs/PRIVACY.md` / `docs/THREAT_MODEL.md` — privacy and security design.
- `docs/SETUP_IOS.md` / `docs/SETUP_BACKEND.md` — setup and troubleshooting.
- `docs/ROADMAP.md` — phase-by-phase implementation status.
- `docs/LIMITATIONS.md` — firmware vs. app, foreground vs. background, standard vs.
  display glasses.
- `AGENTS.md` — guidance for AI coding agents working in this repo.
