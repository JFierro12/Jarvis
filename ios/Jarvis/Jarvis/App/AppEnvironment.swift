import Foundation
import JarvisKit

/// UserDefaults keys shared between `AppEnvironment` (read at launch) and
/// `SettingsView` (written from the Connection section). Kept in one place
/// so the two never drift apart.
enum LiveModePreferences {
    static let liveModeEnabledKey = "liveModeEnabled"
    static let backendBaseURLKey = "backendBaseURLString"
}

/// Composition root. This is the only place that decides which concrete
/// implementation backs each protocol — everything downstream (views,
/// `AssistantCoordinator`) only ever sees the protocol.
@MainActor
final class AppEnvironment: ObservableObject {
    @Published var configuration: AppConfiguration
    let coordinator: AssistantCoordinator
    let memoryRepository: MemoryRepository
    let audioRouteManager: AudioRouteManager
    let wearableClient: WearableDeviceClient

    private static let keychain = KeychainStore()
    /// Not private: `SettingsView` writes to this same Keychain entry when
    /// the user saves a backend auth token.
    static let backendTokenKey = "backend_auth_token"

    static func makeDemo() -> AppEnvironment {
        AppEnvironment(configuration: AppConfiguration(runtimeMode: .demo, flags: .demo))
    }

    /// Live mode assumes physical Ray-Ban Meta glasses and a real iPhone —
    /// the DAT SDK's device session cannot be exercised in the Simulator
    /// (no real Bluetooth stack). See docs/SETUP_IOS.md.
    static func makeLive(backendBaseURL: URL?) -> AppEnvironment {
        AppEnvironment(configuration: AppConfiguration(runtimeMode: .live, backendBaseURL: backendBaseURL, flags: .conservativeDefault))
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

    init(configuration: AppConfiguration) {
        self.configuration = configuration

        switch configuration.runtimeMode {
        case .demo:
            self.coordinator = DemoEnvironment.makeCoordinator()
            self.memoryRepository = InMemoryMemoryRepository()
            self.audioRouteManager = MockAudioRouteManager()
            self.wearableClient = MockWearableDeviceClient()
        case .live:
            try? MetaWearableDeviceClient.configureSDK()
            let wearableClient = MetaWearableDeviceClient(
                capabilities: configuration.glassesConfiguration.hasDisplay ? .rayBanMetaDisplay : .standardRayBanMeta,
                useMockDevice: false
            )
            self.wearableClient = wearableClient
            self.audioRouteManager = AVAudioSessionRouteManager()

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
            if let backendURL = configuration.backendBaseURL {
                let backendClient = BackendAPIClient(baseURL: backendURL, tokenProvider: tokenProvider)
                visionProvider = CloudVisionReasoningProvider(client: backendClient)
                languageProvider = CloudLanguageReasoningProvider(client: backendClient)
            } else {
                // No backend configured yet — fall back to mocks rather than
                // constructing a client with no URL. See docs/SETUP_BACKEND.md
                // for standing up the backend, then pass its URL to
                // AppEnvironment.makeLive(backendBaseURL:).
                visionProvider = MockVisionReasoningProvider()
                languageProvider = MockLanguageReasoningProvider()
            }

            self.coordinator = AssistantCoordinator(
                wearableClient: wearableClient,
                speechToText: AppleSpeechToTextProvider(),
                textToSpeech: AppleTextToSpeechProvider(),
                visionProvider: visionProvider,
                languageProvider: languageProvider,
                memoryRepository: memoryRepository,
                toolExecutor: MockToolExecutor(memoryRepository: memoryRepository)
            )
        }
    }

    func diagnosticsSnapshot() -> DiagnosticsSnapshot {
        DiagnosticsSnapshot(
            sdkVersion: "0.9.0",
            appVersion: "0.1.0-dev",
            deviceModel: configuration.glassesConfiguration.model.rawValue,
            connectionState: "disconnected",
            capabilities: configuration.glassesConfiguration.hasDisplay ? .rayBanMetaDisplay : .standardRayBanMeta,
            backendReachable: configuration.backendBaseURL != nil,
            supportsNativeVoiceInvocation: false,
            foregroundWakeWordEnabled: configuration.flags.enableForegroundWakeWord,
            imageStorageEnabled: configuration.flags.enableLongTermMemory
        )
    }
}
