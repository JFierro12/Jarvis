import Foundation
import JarvisKit

/// UserDefaults keys shared between `AppEnvironment` (read at launch) and
/// `SettingsView` (written from the Connection section). Kept in one place
/// so the two never drift apart.
enum LiveModePreferences {
    static let liveModeEnabledKey = "liveModeEnabled"
    static let backendBaseURLKey = "backendBaseURLString"
    /// pc-agent (see pc-agent/) — the local Mac companion service used for
    /// PC status/commands and, now, the real-time gesture-control WebSocket.
    static let pcAgentBaseURLKey = "pcAgentBaseURLString"
    /// Everything in Settings *outside* the Connection section (Privacy,
    /// Capture, Voice, Integrations, Developer — including Remote TTS)
    /// binds directly to `AppEnvironment.configuration.flags`, which is
    /// otherwise in-memory only. Stored here as encoded JSON since
    /// `FeatureFlags` doesn't map onto individual `@AppStorage` keys.
    static let featureFlagsKey = "featureFlagsData"
}

/// Composition root. This is the only place that decides which concrete
/// implementation backs each protocol — everything downstream (views,
/// `AssistantCoordinator`) only ever sees the protocol.
@MainActor
final class AppEnvironment: ObservableObject {
    @Published var configuration: AppConfiguration {
        didSet {
            Self.saveFlags(configuration.flags)
            Task { await self.updateWakeWordListening() }
        }
    }
    @Published private(set) var wakeWordState: WakeWordDetectorState = .idle
    let coordinator: AssistantCoordinator
    let memoryRepository: MemoryRepository
    let audioRouteManager: AudioRouteManager
    let wearableClient: WearableDeviceClient
    let wakeWordDetector: WakeWordDetector

    private var isSceneActive = true
    // Seeded `false`, not `nil` — at cold launch nothing is connected yet,
    // so built-in mic is already the natural starting route. Seeding `nil`
    // made the very first updateWakeWordListening() call always take the
    // "route is changing" branch and call setPreferredInput even though
    // nothing had actually changed, which destabilized the session while
    // it was still settling from activateSession() and crashed the app
    // before any user interaction at all. Only touch the route on a real
    // transition (glasses actually connecting/disconnecting) from here on.
    private var lastAppliedPreferBluetoothInput = false

    private static let keychain = KeychainStore()
    /// Not private: `SettingsView` writes to this same Keychain entry when
    /// the user saves a backend auth token.
    static let backendTokenKey = "backend_auth_token"
    /// Not private: `SettingsView` writes to this same Keychain entry when
    /// the user saves a pc-agent pairing token.
    static let pcAgentTokenKey = "pc_agent_pairing_token"

    static func makeDemo() -> AppEnvironment {
        AppEnvironment(configuration: AppConfiguration(runtimeMode: .demo, flags: loadStoredFlags() ?? .demo))
    }

    /// Live mode assumes physical Ray-Ban Meta glasses and a real iPhone —
    /// the DAT SDK's device session cannot be exercised in the Simulator
    /// (no real Bluetooth stack). See docs/SETUP_IOS.md.
    static func makeLive(backendBaseURL: URL?) -> AppEnvironment {
        AppEnvironment(configuration: AppConfiguration(runtimeMode: .live, backendBaseURL: backendBaseURL, flags: loadStoredFlags() ?? .conservativeDefault))
    }

    /// Reads `SettingsView`'s "Connection" preferences to decide demo vs.
    /// live at launch. `AppEnvironment` is built once via `@StateObject`, so
    /// changes made in Settings only take effect on the next full relaunch —
    /// `SettingsView` says as much next to the toggle.
    static func makeFromStoredPreferences() -> AppEnvironment {
        let defaults = UserDefaults.standard
        guard defaults.bool(forKey: LiveModePreferences.liveModeEnabledKey) else { return .makeDemo() }
        let urlString = defaults.string(forKey: LiveModePreferences.backendBaseURLKey) ?? ""
        return .makeLive(backendBaseURL: URL(string: urlString))
    }

    private static func loadStoredFlags() -> FeatureFlags? {
        guard let data = UserDefaults.standard.data(forKey: LiveModePreferences.featureFlagsKey) else { return nil }
        return try? JSONDecoder().decode(FeatureFlags.self, from: data)
    }

    private static func saveFlags(_ flags: FeatureFlags) {
        guard let data = try? JSONEncoder().encode(flags) else { return }
        UserDefaults.standard.set(data, forKey: LiveModePreferences.featureFlagsKey)
    }

    init(configuration: AppConfiguration) {
        self.configuration = configuration

        switch configuration.runtimeMode {
        case .demo:
            self.coordinator = DemoEnvironment.makeCoordinator()
            self.memoryRepository = InMemoryMemoryRepository()
            self.audioRouteManager = MockAudioRouteManager()
            self.wearableClient = MockWearableDeviceClient()
            self.wakeWordDetector = MockWakeWordDetector()
        case .live:
            try? MetaWearableDeviceClient.configureSDK()
            let wearableClient = MetaWearableDeviceClient(
                capabilities: configuration.glassesConfiguration.hasDisplay ? .rayBanMetaDisplay : .standardRayBanMeta
            )
            self.wearableClient = wearableClient
            let audioRouteManager = AVAudioSessionRouteManager()
            // Never called before now — without this, AVAudioSession stays
            // at its default category, which doesn't prefer the glasses as
            // an input/output route. This is what both STT and TTS need to
            // route through Bluetooth instead of the phone's own mic/speaker.
            try? audioRouteManager.activateSession()
            self.audioRouteManager = audioRouteManager
            self.wakeWordDetector = AppleWakeWordDetector()

            let memoryRepository: MemoryRepository
            if let container = try? LocalMemoryRepository.makeContainer() {
                memoryRepository = LocalMemoryRepository(modelContainer: container)
            } else {
                memoryRepository = InMemoryMemoryRepository()
            }
            self.memoryRepository = memoryRepository

            let tokenProvider: @Sendable () -> String? = { try? Self.keychain.get(Self.backendTokenKey) }
            let visionProvider: VisionReasoningProvider
            let languageProvider: LanguageReasoningProvider
            let textToSpeech: TextToSpeechProvider
            if let backendURL = configuration.backendBaseURL {
                let backendClient = BackendAPIClient(baseURL: backendURL, tokenProvider: tokenProvider)
                visionProvider = CloudVisionReasoningProvider(client: backendClient)
                languageProvider = CloudLanguageReasoningProvider(client: backendClient)
                // Remote TTS (e.g. a cloned voice via ElevenLabs) needs both
                // the flag on and a backend to actually call — falls back
                // to the on-device voice otherwise, same reasoning as
                // vision/language above.
                textToSpeech = configuration.flags.enableRemoteTTS
                    ? CloudTextToSpeechProvider(client: backendClient)
                    : AppleTextToSpeechProvider()
            } else {
                // No backend configured yet — fall back to mocks rather than
                // constructing a client with no URL. See docs/SETUP_BACKEND.md
                // for standing up the backend, then pass its URL to
                // AppEnvironment.makeLive(backendBaseURL:).
                visionProvider = MockVisionReasoningProvider()
                languageProvider = MockLanguageReasoningProvider()
                textToSpeech = AppleTextToSpeechProvider()
            }

            // pc-agent (see pc-agent/) is the local Mac companion service —
            // used both for the existing PC status/lock/launch commands
            // (previously unused in live mode) and, now, the real-time
            // gesture-control WebSocket. Falls back to mocks the same way
            // vision/language do above when it isn't configured yet.
            let pcAgentTokenProvider: @Sendable () -> String? = { try? Self.keychain.get(Self.pcAgentTokenKey) }
            let pcAgentBaseURLString = UserDefaults.standard.string(forKey: LiveModePreferences.pcAgentBaseURLKey) ?? ""
            let pcAgentClient: PCAgentClient?
            let handGestureController: HandGestureController
            if configuration.flags.enablePCAgent,
               let pcAgentBaseURL = URL(string: pcAgentBaseURLString),
               let pcAgentToken = pcAgentTokenProvider(), !pcAgentToken.isEmpty {
                pcAgentClient = URLSessionPCAgentClient(baseURL: pcAgentBaseURL, pairingToken: pcAgentToken)
                let gestureStreamClient = URLSessionGestureStreamClient(baseURL: pcAgentBaseURL, pairingToken: pcAgentToken)
                handGestureController = LiveHandGestureController(wearableClient: wearableClient, gestureStreamClient: gestureStreamClient)
            } else {
                pcAgentClient = nil
                handGestureController = MockHandGestureController()
            }

            self.coordinator = AssistantCoordinator(
                wearableClient: wearableClient,
                speechToText: AppleSpeechToTextProvider(),
                textToSpeech: textToSpeech,
                visionProvider: visionProvider,
                languageProvider: languageProvider,
                memoryRepository: memoryRepository,
                toolExecutor: MockToolExecutor(memoryRepository: memoryRepository, pcAgentClient: pcAgentClient),
                handGestureController: handGestureController,
                musicPlayerController: AppleMusicPlayerController()
            )
        }

        observeWakeWordDetector()
        Task { await updateWakeWordListening() }
    }

    /// Foreground-only: called from `JarvisApp`'s `.onChange(of: scenePhase)`
    /// so wake word listening stops the moment the app backgrounds, per
    /// `WakeWordDetector`'s documented contract.
    func handleScenePhaseChange(isActive: Bool) {
        isSceneActive = isActive
        Task { await updateWakeWordListening() }
    }

    private func updateWakeWordListening() async {
        // enableBackgroundWakeWord is a modifier on top of the base wake-word
        // flag, not a replacement — it only keeps listening alive past a
        // backgrounding transition; it does nothing if the base flag is off.
        let shouldListen = configuration.flags.enableForegroundWakeWord
            && (isSceneActive || configuration.flags.enableBackgroundWakeWord)
        guard shouldListen else {
            await wakeWordDetector.stop()
            return
        }
        if configuration.runtimeMode == .live {
            guard await AppleWakeWordDetector.requestAuthorization() else {
                wakeWordState = .unavailable(reason: "Microphone or speech recognition permission was denied.")
                return
            }

            let preferGlassesMic: Bool
            if case .connected = coordinator.wearableConnectionState {
                preferGlassesMic = true
            } else {
                preferGlassesMic = false
            }
            if lastAppliedPreferBluetoothInput != preferGlassesMic {
                // Only touch the route when it's actually changing (e.g. the
                // glasses just connected/disconnected) — not on every turn.
                // Stop first so no tap is installed on the OLD route, then
                // give iOS a moment to finish the hardware handover before
                // installing a fresh tap. Skipping the stop/settle step
                // here previously crashed with a CoreAudio format mismatch
                // ("Input HW format and tap format not matching") whenever
                // the preferred input changed while a tap from the old
                // route was still installed.
                await wakeWordDetector.stop()
                audioRouteManager.setPreferBluetoothInput(preferGlassesMic)
                lastAppliedPreferBluetoothInput = preferGlassesMic
                try? await Task.sleep(nanoseconds: 200_000_000)
            }
        }
        try? await wakeWordDetector.start()
    }

    private func observeWakeWordDetector() {
        Task { [weak self] in
            guard let self else { return }
            for await state in self.wakeWordDetector.state {
                self.wakeWordState = state
                if state == .detected {
                    // The coordinator's speech-to-text path owns its own
                    // separate AVAudioEngine — stop the wake word detector's
                    // engine first so the two never run concurrently and
                    // fight over the microphone, then resume listening
                    // (respecting the current flag/foreground state)
                    // once the assistant turn is done.
                    await self.wakeWordDetector.stop()
                    await self.coordinator.activate(mode: .foregroundWakeWord)
                    await self.updateWakeWordListening()
                }
            }
        }
    }

    func diagnosticsSnapshot() -> DiagnosticsSnapshot {
        DiagnosticsSnapshot(
            sdkVersion: "0.9.0",
            appVersion: "0.1.0-dev",
            deviceModel: configuration.glassesConfiguration.model.rawValue,
            connectionState: Self.describe(coordinator.wearableConnectionState),
            capabilities: configuration.glassesConfiguration.hasDisplay ? .rayBanMetaDisplay : .standardRayBanMeta,
            backendReachable: configuration.backendBaseURL != nil,
            supportsNativeVoiceInvocation: false,
            foregroundWakeWordEnabled: configuration.flags.enableForegroundWakeWord,
            imageStorageEnabled: configuration.flags.enableLongTermMemory
        )
    }

    private static func describe(_ state: WearableConnectionState) -> String {
        switch state {
        case .unavailable: return "unavailable"
        case .disconnected: return "disconnected"
        case .connecting: return "connecting"
        case .connected(let model): return "connected (\(model.rawValue))"
        case .paused: return "paused"
        case .error(let message): return "error: \(message)"
        }
    }
}
