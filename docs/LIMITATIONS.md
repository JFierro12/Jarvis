# Limitations

This document exists specifically to prevent this project's own README, UI copy, or a
future contributor's assumptions from overstating what's real. When in doubt, check
`docs/META_SDK_NOTES.md` — it is the source of truth for what the Meta DAT SDK actually
does, verified against the real SDK checked out from `facebook/meta-wearables-dat-ios`.

## Firmware-native Meta functionality vs. this app

| Capability | Firmware-native (Meta) | This app |
|---|---|---|
| "Hey Meta" wake phrase | Yes — built into the glasses | **No.** This app cannot hook, intercept, or replace it. |
| "Jarvis" as a wake word | No such thing exists at the firmware level | Only as an **on-phone, foreground-only, experimental** feature (`WakeWordDetector`, default off) — never described as glasses-native anywhere in this codebase. |
| Photo capture | Yes, via Meta AI's own flows | Yes, via the documented DAT SDK `Stream.capturePhoto()` API, from within this app's own registered session. |
| Native voice invocation of a 3rd-party app | Not documented as supported | `WearableCapabilities.supportsNativeVoiceInvocation` hardcoded `false`. Flip only with a citation to current SDK docs proving otherwise. |

## Foreground vs. background

- **Foreground, fully supported today:** push-to-talk, press-to-activate session, photo
  capture, video streaming (behind a feature flag), the experimental wake word detector.
- **Background:** not supported by this codebase. The one documented background
  capability in the DAT SDK (0.9.0 changelog) is that an *already-started* camera
  *recording* can continue if the app backgrounds — this is not the same as background
  listening/streaming, and this codebase does not build on it. No silent-audio keep-alive
  hacks exist anywhere in this repo — if you find one, it's a bug, not a feature.

## Standard Ray-Ban Meta vs. Meta Ray-Ban Display

- Standard Ray-Ban Meta glasses have **no third-party visual HUD.** All responses for
  this configuration are audio-only, which is why `GlassesConfiguration.standardRayBanMeta`
  is the default (`hasDisplay: false`).
- Meta Ray-Ban Display glasses support the `MWDATDisplay` module (FlexBox/Text/Button/Image
  layout). **Not implemented in this codebase** — `MWDATDisplay` is not among JarvisKit's
  package dependencies, and `GlassesConfiguration.rayBanMetaDisplay` exists as a config
  value but has no corresponding UI rendering path. This is an explicit, documented gap,
  not an oversight.

## Implemented vs. planned

See `docs/ROADMAP.md` for the phase-by-phase breakdown. In short, as of this session:

**Implemented and tested:**
- Assistant state machine, intent router, context assembler, policy engine, confirmation
  manager, frame selector — all in `ios/JarvisKit`, 44 passing unit tests.
- Mock-backed end-to-end demo flow (activate → visual question → mock vision → speak →
  remember → search → delete), verified via `AssistantCoordinatorTests`.
- Real Meta DAT SDK integration compiles against the actual SDK (`MetaWearableDeviceClient`)
  and the app builds/runs in the iOS Simulator using `MockDeviceKit` — **not yet verified
  against physical glasses** (none were available in this session).
- Backend (`backend/`): all documented endpoints, policy engine, prompt-injection defense,
  26 passing tests.
- pc-agent (`pc-agent/`): read-only status + 5 allowlisted commands, pairing-token auth,
  rate limiting, 9 passing tests.

**Explicitly not implemented in this pass** (tracked in `docs/ROADMAP.md`):
- Calendar/Reminders EventKit integration (protocols exist; no concrete adapter yet).
- Home Assistant adapter exists (`HomeAssistantSmartHomeClient`) but is unexercised without
  a real Home Assistant instance and has no automated test.
- App Intents / Shortcuts / Action Button entry point.
- Foreground wake-word detector's actual on-device model (protocol + mock exist; no real
  `WakeWordDetector` implementation).
- Meta Ray-Ban Display rendering (`MWDATDisplay`).
- Data export as a one-tap UI action (the underlying `export()`/search APIs exist).
- Remote/cloud TTS voice provider (only the on-device Apple voice is implemented).
- `JarvisUITests` exist and are written against real accessibility labels, but could not
  be executed in this sandboxed session — see `docs/ROADMAP.md` for why.
