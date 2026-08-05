# iOS Setup

## Prerequisites

- Xcode 16+ (built/tested with Xcode 26.6).
- iOS 17.2+ deployment target (matches the Meta DAT SDK's own minimum as of SDK 0.9.0).
- A Meta account + the **Meta AI** companion app installed on a real test device, if you
  intend to test against physical Ray-Ban Meta glasses. Not required for demo mode or
  `MockDeviceKit`-based development.

## Opening the project

```bash
open ios/Jarvis/Jarvis.xcodeproj
```

`ios/Jarvis` is a thin app shell; almost all logic lives in the local Swift package
`ios/JarvisKit`, referenced via **Add Local Package** (already wired into the checked-in
`project.pbxproj` as an `XCLocalSwiftPackageReference` pointing at `../JarvisKit`). Xcode
will resolve `JarvisKit`'s own dependency on the real
`https://github.com/facebook/meta-wearables-dat-ios` package automatically — this requires
network access the first time.

## Running in demo mode (no hardware, no credentials)

Demo mode is the default (`AppConfiguration(runtimeMode: .demo, flags: .demo)` in
`AppEnvironment.makeDemo()`). Just build and run on any iOS 17.2+ simulator:

```bash
xcodebuild build -project ios/Jarvis/Jarvis.xcodeproj -scheme Jarvis \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
```

Everything — the glasses connection, camera, vision analysis, memory storage — is backed
by deterministic in-memory mocks (`ios/JarvisKit/Sources/JarvisKit/Mocks/DemoEnvironment.swift`).
No backend, no Meta developer account, no API keys.

## Running JarvisKit's unit tests

This is the primary, hardware-independent test suite (44 tests covering the state
machine, intent router, memory repository, policy engine, confirmation manager, and frame
selector):

```bash
xcodebuild test -project ios/JarvisKit/Package.swift -scheme JarvisKit \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
# or, from ios/JarvisKit:
xcodebuild test -scheme JarvisKit -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
```

## Running the app's own tests

```bash
xcodebuild test -project ios/Jarvis/Jarvis.xcodeproj -scheme Jarvis \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -only-testing:JarvisTests
```

`JarvisUITests` exist and should work the same way from an interactive Xcode session /
normal (non-headless) machine; see `docs/ROADMAP.md` for a note on why they could not be
executed in the sandboxed session that built this project.

## Enabling Developer Mode for real Meta glasses (Phase 3+)

1. Open the **Meta AI** app on your phone and pair your glasses if you haven't already.
2. Meta AI → Settings → (your glasses) → **Developer Mode** → on. The glasses may restart.
3. In `ios/Jarvis/Jarvis/Resources/Info.plist`, the `MWDAT.MetaAppID` key is already set to
   `0`, which is correct for Developer Mode. For production, register a project at the
   [Wearables Developer Center](https://wearables.developer.meta.com/) and replace it (and
   add `ClientToken`/`DEVELOPMENT_TEAM`) per `docs/META_SDK_NOTES.md`.
4. `AppEnvironment.makeLive(backendBaseURL:)` builds the real stack —
   `MetaWearableDeviceClient`, `AppleSpeechToTextProvider`, `AppleTextToSpeechProvider`,
   `AVAudioSessionRouteManager`, `LocalMemoryRepository`, and (if you pass a backend URL)
   `CloudVisionReasoningProvider`/`CloudLanguageReasoningProvider`. It also calls
   `MetaWearableDeviceClient.configureSDK()` once, internally. **Not yet wired to a UI
   switch** — `JarvisApp.swift` still always calls `.makeDemo()`; change that call site (or
   add a Settings toggle) to actually reach live mode.
5. Real hardware requires a physical iPhone, not the Simulator (no Bluetooth stack there).
   Set your own signing team in Xcode's Signing & Capabilities tab (see Troubleshooting).
6. Nothing yet calls `wearableClient.registerApplication()` / `.connect()` — the
   Meta AI registration handshake needs a real "Connect" action wired into onboarding or
   `MainView`; see `docs/ROADMAP.md`.

## Using MockDeviceKit directly (no app changes)

`MetaWearableDeviceClient(useMockDevice: true)` pairs a simulated Ray-Ban Meta via the
real SDK's own `MockDeviceKit` (not this project's separate Swift-only mocks) — useful for
exercising the actual DAT SDK call shapes without hardware. See
`ios/JarvisKit/Sources/JarvisKit/Integrations/MetaWearableDeviceClient.swift`.

## Troubleshooting

- **Package resolution fails / times out:** requires network access to
  `github.com/facebook/meta-wearables-dat-ios`. Check connectivity, then
  File → Packages → Reset Package Caches in Xcode.
- **"Multiple commands produce Info.plist":** already handled in this project via a
  `PBXFileSystemSynchronizedBuildFileExceptionSet` excluding
  `Resources/Info.plist` from the app target's automatic resource membership — if you add
  a *new* Info.plist-like file under a synchronized group, you'll need a similar exception.
- **Code signing errors on simulator:** the checked-in project sets
  `CODE_SIGNING_ALLOWED = NO` / `CODE_SIGNING_REQUIRED = NO` for simulator builds. For a
  real device you'll need to set a development team and re-enable signing in the target's
  Signing & Capabilities tab.
- **Developer Mode toggles off after a firmware update:** re-enable it in Meta AI; this is
  a known DAT SDK quirk documented upstream (`docs/META_SDK_NOTES.md`).
