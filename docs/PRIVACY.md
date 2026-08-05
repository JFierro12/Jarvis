# Privacy

JARVIS treats privacy as a product feature, not an afterthought. This document is the
implementation-level companion to the onboarding notice shown in the app
(`ios/Jarvis/Jarvis/Features/OnboardingView.swift`).

## What the glasses can capture

- A single photo, only when you explicitly ask something that needs visual context
  (`analyze_scene`, `read_text`, `remember_scene` intents in
  `ios/JarvisKit/Sources/JarvisKit/Services/IntentRouter.swift`).
- No continuous video by default — `enableVideoStreaming` defaults to `false`
  (`ios/JarvisKit/Sources/JarvisKit/Core/FeatureFlags.swift`).
- No microphone/speaker capability comes from the glasses' SDK at all — see
  `docs/META_SDK_NOTES.md`. Audio in/out is standard iOS Bluetooth routing.

## What's processed on-device

- Speech-to-text: Apple's `Speech` framework (`AppleSpeechToTextProvider`), on-device by
  default.
- Text-to-speech: `AVSpeechSynthesizer` (`AppleTextToSpeechProvider`), fully on-device.
- Deterministic intent routing (`IntentRouter`) never leaves the phone.
- The local memory database (SwiftData) is fully on-device unless you explicitly enable
  backend sync.

## What's sent to a backend (only if configured)

- The one image needed for a vision question, base64-encoded, for that single request —
  never a rolling buffer.
- The assembled, least-privilege context for a reasoning request
  (`ContextAssembler.assemble` only includes what the current intent needs — see the
  `ContextAssemblerTests` for the calendar/camera exclusion behavior).
- Nothing is sent until you activate JARVIS for that specific request.

## What is stored

- Photos: deleted immediately after analysis unless you say "remember this" (or a
  `rememberScene` intent is recognized). Only then does `AssistantCoordinator.saveScene`
  write a `MemoryRecord`.
- Memories: on-device by default; the backend's `memories` table exists only for
  optional sync, and is only ever reachable with a valid bearer token.
- Location: `coarseLocationName`/`latitude`/`longitude` on a `MemoryRecord` are populated
  only when `enableLocationContext` is on (default: off).

## When the microphone is active

Only during an active listening session, which is always visibly reflected in
`AssistantState.listening` / `.transcribing` in the UI. There is no background listening
mode in this codebase — the experimental foreground wake word
(`WakeWordDetector`) is explicitly foreground-only and shown with its own listening
indicator when enabled (default: off).

## User controls

Implemented today (`ios/Jarvis/Jarvis/Features/`):

- `MemoriesView`: view, search, delete one memory, delete all memories.
- `SettingsView`: toggle long-term memory, location context, calendar, reminders, photo
  capture, video streaming, foreground wake word, PC agent, smart home, remote TTS,
  developer diagnostics — each defaults off except the low-risk, core-to-the-product ones
  (photo capture, long-term memory, developer diagnostics).
- `SettingsView`'s "Privacy Mode" button disables camera, location, and long-term memory
  in one action.
- `DiagnosticsView`'s "Copy Diagnostics" button redacts anything resembling a token/secret
  (`SecretRedaction.redacted`) before it reaches the pasteboard.
- `AssistantCoordinator.stopEverything()` is the "stop everything" control — cancels
  speech recognition, stops TTS, stops any video stream, clears pending confirmations,
  and resets to idle.

Planned, not yet implemented (see `docs/ROADMAP.md`): data export as a dedicated
onboarding-adjacent action (the backend/local repository both expose `export()` /
`GET /v1/memories/search`, but there is no single-tap "export everything" UI button yet),
and a persistent system-level recording indicator beyond the in-app state text (iOS itself
shows the standard microphone/camera indicators whenever those APIs are actually in use).

## Responsibility

JARVIS is a third-party app. It does not replace Meta's own assistant or "Hey Meta," and
using its camera/photo features around other people is subject to ordinary recording and
privacy law in your jurisdiction — the app does not and cannot make that determination
for you.
