# Meta Wearables Device Access Toolkit (DAT) — iOS Verified Capability Notes

**Verified from:** `facebook/meta-wearables-dat-ios` (cloned locally at commit visible via
`git log -1` in that checkout on 2026-08-04), specifically `AGENTS.md`, `README.md`,
`CHANGELOG.md` (`[0.9.0] - 2026-08-03` is the latest entry), and the `samples/CameraAccess`
and `samples/DisplayAccess` sample apps. This is the authoritative source for this project —
no API below was invented. Where the SDK is silent, this document says so explicitly.

Package version at time of writing: **0.9.0**. Minimum deployment target: **iOS 17.2**
(bumped from 15.2 in 0.9.0 — apps targeting older iOS versions can no longer link the SDK).

## Modules

| Module | Purpose |
|---|---|
| `MWDATCore` | Device discovery, registration, permissions, device selectors, `Wearables` entry point |
| `MWDATCamera` | `Camera` capability, `Stream`, `VideoFrame`, photo capture |
| `MWDATDisplay` | Display capability, layout DSL (`FlexBox`, `Text`, `Button`, `Image`, `Icon`, `VideoPlayer`) — Meta Ray-Ban Display only |
| `MWDATMockDevice` | `MockDeviceKit`, `MockGlasses`, `MockCameraKit` — simulator testing without hardware. **Not linked by this project** — see note below. |

A DAT app should only depend on these four modules (per `AGENTS.md`, "Allowed dependencies").
This project links only `MWDATCore` and `MWDATCamera`. `MWDATMockDevice` was tried and then
removed: embedding it alongside `MWDATCamera` in the same app target caused an Objective-C
runtime class collision on a physical device (`SUPMediaStream*` and related symbols defined
in both frameworks' binaries), logged as `objc[...]: Class ... is implemented in both ...
This may cause spurious casting failures and mysterious crashes.` Demo mode never actually
used `MockDeviceKit` in practice (see point 4 below), so nothing was lost by removing it —
if a future need for `MockDeviceKit` arises, it should go in a separate build target that is
never linked into the same binary as `MWDATCamera` on-device.

## Capability matrix

| Capability | Supported by current SDK? | Standard Ray-Ban Meta? | Meta Ray-Ban Display only? | Requires foreground app? | Requires Bluetooth audio profile? | Requires explicit permission? | Verified from | Implementation status | Notes |
|---|---|---|---|---|---|---|---|---|---|
| App registration (`Wearables.shared.startRegistration()`) | Yes | Yes | Yes | Yes | No | No (registration itself; opens Meta AI) | AGENTS.md §Permissions | Implemented (mock); real adapter wired, unverified on hardware | Dev Mode uses `MetaAppID = 0`; production needs a real `APPLICATION_ID`/`ClientToken` from the Wearables Developer Center |
| Device discovery (`devicesStream()`) | Yes | Yes | Yes | Yes | No | No | AGENTS.md §Session management | Implemented (mock) + real adapter | — |
| Device session lifecycle (`DeviceSession`, `.idle/.starting/.started/.paused/.stopping/.stopped`) | Yes | Yes | Yes | Yes | No | No | AGENTS.md §Session management | Implemented (mock) + real adapter | App must not restart while `.paused`; only react to `.started`/`.stopped` |
| Camera permission (`checkPermissionStatus(.camera)` / `requestPermission(.camera)`) | Yes | Yes | Yes | Yes | No | Yes | AGENTS.md §Permissions | Implemented (mock) + real adapter | Grant is "any linked device" — not per-device |
| Photo capture (`Stream.capturePhoto(format:)` → `photoDataPublisher`) | Yes | Yes | Yes | Yes | No | Yes (camera) | AGENTS.md §Camera Streaming | Implemented (mock) + real adapter | Formats: JPEG, PNG |
| Video streaming (`Camera.stream`, `videoFramePublisher`) | Yes | Yes | Yes | Yes | No | Yes (camera) | AGENTS.md §Camera Streaming | Implemented (mock) + real adapter (photo-first per MVP guidance) | Resolutions: `.high` 720×1280, `.medium` 504×896, `.low` 360×640. Frame rates: 2/7/15/24/30 fps. SDK auto-degrades resolution first, then frame rate (floor 15fps) under BT bandwidth pressure |
| Display rendering (`Display`, `FlexBox`/`Text`/`Button`/`Image`/`VideoPlayer`) | Yes | **No** | Yes | Yes | No | No | AGENTS.md §Display Access | Not implemented (out of scope for standard glasses MVP) | Gate behind `GlassesConfiguration.hasDisplay` |
| Microphone access via DAT SDK | **Not exposed by DAT.** No `MWDATAudio`/microphone module exists in `MWDATCore`/`MWDATCamera`/`MWDATDisplay`. | N/A | N/A | N/A | Yes (standard iOS Bluetooth HFP, outside DAT) | N/A | Absence confirmed by module list in AGENTS.md and full-text search of AGENTS.md for "microphone"/"audio" (only appears re: video-with-sound capture, not a JARVIS mic API) | Implemented via `AVAudioSession`/`AVAudioEngine` Bluetooth HFP route, **not** via DAT | Speech input goes through the iPhone's standard Bluetooth audio route to the glasses, not a Meta SDK call |
| Speaker output via DAT SDK | **Not exposed by DAT.** | N/A | N/A | N/A | Yes (standard iOS Bluetooth A2DP/HFP, outside DAT) | N/A | Same as above | Implemented via `AVAudioSession` output route selection | TTS playback routes through whichever output `AVAudioSession.currentRoute` reports as active |
| Custom wake-word ("Jarvis") support | **Not supported by DAT.** No wake-word or voice-invocation API in any module. | No | No | — | — | — | Absence confirmed by full read of AGENTS.md; no `WakeWord`/`VoiceInvocation` symbol anywhere | `supportsNativeVoiceInvocation` hardcoded `false` | Do not represent third-party wake word as firmware-native |
| Native voice invocation of a 3P app by the glasses | **Not documented / not supported.** | No | No | — | — | — | Same as above | Capability flag defaults `false` | Re-verify against `CHANGELOG.md` on every SDK bump |
| Background execution while app is backgrounded | Partially — camera *recording* can continue backgrounded per 0.9.0 changelog ("Camera Access sample: record video with optional sound-in-video, continuing while the app is backgrounded"), but **general background streaming/wake-word is not documented as supported** | Yes (recording only) | Yes | No (for general streaming) | — | — | CHANGELOG.md [0.9.0] Added | Treated as unsupported for JARVIS's continuous-listening use case; only recording-in-progress continuation is verified | Do not build silent-audio keep-alive hacks |
| MockDeviceKit (`MockDeviceKit.shared`, `pairGlasses(model:)`, `.don()/.doff()`, `setCameraFeed`/`setCapturedImage`) | Yes | Yes | Yes | Yes (simulator/dev) | No | Configurable via `mockPermissions` | AGENTS.md §Testing instructions | **Not used** — this project's demo mode runs on a separate hand-written `MockWearableDeviceClient` (pure Swift, no SDK dependency at all), not on MWDATMockDevice/MockDeviceKit; see the module note above | Models: `.rayBanMeta`, `.oakleyMetaHSTN`, `.oakleyMetaVanguard`, `.rayBanMetaOptics`, `.metaGlasses` |
| Release-channel testing (production, non-Dev-Mode) | Yes, via Wearables Developer Center project + release channel | Yes | Yes | Yes | No | Yes | README.md, AGENTS.md §Permissions | Not exercised (requires a registered org/app) | Needs `APPLICATION_ID` + `ClientToken` + Team ID |

## What this means for JARVIS's architecture

1. **Microphone and speaker are never DAT SDK calls.** They are ordinary `AVAudioSession`
   Bluetooth routing (HFP for mic in, A2DP/HFP for audio out) once the glasses are the active
   Bluetooth audio accessory. `AudioRouteManager` in this codebase reflects that split
   explicitly — it does not depend on `WearableDeviceClient` at all.
2. **There is no wake-word or native-voice-invocation API.** `WearableCapabilities.supportsNativeVoiceInvocation`
   defaults to `false` and there is nothing to flip it to `true` today. The foreground
   custom wake-word feature (`WakeWordDetector`) is a purely on-phone, app-local feature —
   it has nothing to do with the glasses' firmware and must never be described that way in
   UI copy.
3. **Photo capture is the reliable, low-risk MVP path**; continuous video streaming works
   but is bandwidth/battery-expensive over Bluetooth Classic and is deferred behind
   `enableVideoStreaming` (default off).
4. **Demo mode runs on a pure-Swift mock, not MockDeviceKit.** `MockWearableDeviceClient`
   has zero dependency on any MWDAT module, which is what let it keep working when
   `MWDATMockDevice` was removed from the app target entirely (see module note above).

## Verified on physical hardware (2026-08-04)

A real Ray-Ban Meta device, in Developer Mode, was used to exercise the real adapter for the
first time this session. Findings:

- `Wearables.shared.startRegistration()` throws typed `MWDATCore.RegistrationError` — not a
  generic `Error` — and `.alreadyRegistered` is a real, expected case hit on every run after
  the first successful registration (the app was already registered with Meta AI from an
  earlier attempt). The adapter now catches this specifically and treats it as success rather
  than aborting the connect flow.
- `RegistrationState`, `PermissionStatus` (cases: `.granted`, `.denied` — no `.notDetermined`
  in the real SDK type), and `DeviceSessionState` (cases: `.idle`, `.starting`, `.started`,
  `.paused`, `.stopping`, `.stopped`) case names are confirmed exactly against the real
  `MWDATCore.swiftinterface` shipped in the resolved 0.9.0 package — the adapter's stringly-typed
  placeholder comparisons (`"\(status)" == "granted"` etc.) have been replaced with typed
  switches over the real enums.
- Embedding `MWDATMockDevice` alongside `MWDATCamera` in the same app binary crashes on-device
  at launch with duplicate Objective-C class definitions (see module note above). This was not
  visible in Simulator builds.

## Unverified / re-check-before-relying-on

- Whether `openFirmwareUpdate()` / `openDATGlassesAppUpdate()` flows are reachable without a
  registered production app.
- Exact behavior of `checkPermissionStatus` when no device has ever been paired.
- Whether Wi-Fi transport (mentioned in `[0.8.0]` changelog) changes any of the above
  bandwidth/quality tradeoffs for photo capture specifically.
- Real-hardware timing for `capturePhoto()` round-trip latency (spec targets <1s) — not yet
  measured; connect/register is the only part of the real adapter exercised on hardware so far.
