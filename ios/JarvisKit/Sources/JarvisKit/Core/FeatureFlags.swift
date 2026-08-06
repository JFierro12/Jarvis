import Foundation

/// All flags default conservative (spec §30). Nothing here enables camera,
/// location, or long-term memory by default in a fresh install — onboarding
/// is what turns these on, deliberately, one at a time.
public struct FeatureFlags: Sendable, Equatable, Codable {
    public var enableVisualAnalysis: Bool
    public var enableVideoStreaming: Bool
    public var enablePhotoCapture: Bool
    public var enableForegroundWakeWord: Bool
    /// Keeps wake-word listening running after the app is backgrounded
    /// (not force-quit) — requires `enableForegroundWakeWord` also being on.
    /// Uses the `audio` background mode to stay alive, which costs real
    /// battery and is not guaranteed forever: iOS can still suspend the app
    /// if it decides the activity doesn't look like genuine ongoing audio
    /// use. Off by default — this is a deliberate opt-in, not a silent
    /// always-on microphone.
    public var enableBackgroundWakeWord: Bool
    public var enableNativeVoiceInvocation: Bool
    public var enableLongTermMemory: Bool
    public var enableLocationContext: Bool
    public var enableCalendar: Bool
    public var enableReminders: Bool
    public var enablePCAgent: Bool
    public var enableSmartHome: Bool
    public var enableRemoteTTS: Bool
    public var enableDeveloperDiagnostics: Bool

    public init(
        enableVisualAnalysis: Bool = true,
        enableVideoStreaming: Bool = false,
        enablePhotoCapture: Bool = true,
        enableForegroundWakeWord: Bool = false,
        enableBackgroundWakeWord: Bool = false,
        enableNativeVoiceInvocation: Bool = false,
        enableLongTermMemory: Bool = true,
        enableLocationContext: Bool = false,
        enableCalendar: Bool = false,
        enableReminders: Bool = false,
        enablePCAgent: Bool = false,
        enableSmartHome: Bool = false,
        enableRemoteTTS: Bool = false,
        enableDeveloperDiagnostics: Bool = true
    ) {
        self.enableVisualAnalysis = enableVisualAnalysis
        self.enableVideoStreaming = enableVideoStreaming
        self.enablePhotoCapture = enablePhotoCapture
        self.enableForegroundWakeWord = enableForegroundWakeWord
        self.enableBackgroundWakeWord = enableBackgroundWakeWord
        self.enableNativeVoiceInvocation = enableNativeVoiceInvocation
        self.enableLongTermMemory = enableLongTermMemory
        self.enableLocationContext = enableLocationContext
        self.enableCalendar = enableCalendar
        self.enableReminders = enableReminders
        self.enablePCAgent = enablePCAgent
        self.enableSmartHome = enableSmartHome
        self.enableRemoteTTS = enableRemoteTTS
        self.enableDeveloperDiagnostics = enableDeveloperDiagnostics
    }

    public static let conservativeDefault = FeatureFlags()

    /// Demo mode: everything runs locally with deterministic mock data, no
    /// credentials required.
    public static let demo = FeatureFlags(
        enableVisualAnalysis: true,
        enableVideoStreaming: false,
        enablePhotoCapture: true,
        enableForegroundWakeWord: false,
        enableBackgroundWakeWord: false,
        enableNativeVoiceInvocation: false,
        enableLongTermMemory: true,
        enableLocationContext: false,
        enableCalendar: false,
        enableReminders: false,
        enablePCAgent: false,
        enableSmartHome: false,
        enableRemoteTTS: false,
        enableDeveloperDiagnostics: true
    )
}

public enum RuntimeMode: String, Sendable, Equatable {
    case demo
    case live
}

public struct AppConfiguration: Sendable, Equatable {
    public var runtimeMode: RuntimeMode
    public var backendBaseURL: URL?
    public var flags: FeatureFlags
    public var glassesConfiguration: GlassesConfiguration

    public init(runtimeMode: RuntimeMode = .demo, backendBaseURL: URL? = nil, flags: FeatureFlags = .demo, glassesConfiguration: GlassesConfiguration = .standardRayBanMeta) {
        self.runtimeMode = runtimeMode
        self.backendBaseURL = backendBaseURL
        self.flags = flags
        self.glassesConfiguration = glassesConfiguration
    }
}

/// Which physical glasses to assume when no device has connected yet.
/// Standard Ray-Ban Meta has no third-party visual HUD; only the Display
/// model does. See docs/META_SDK_NOTES.md.
public struct GlassesConfiguration: Sendable, Equatable {
    public var model: GlassesModel
    public var hasDisplay: Bool

    public init(model: GlassesModel, hasDisplay: Bool) {
        self.model = model
        self.hasDisplay = hasDisplay
    }

    public static let standardRayBanMeta = GlassesConfiguration(model: .rayBanMeta, hasDisplay: false)
    public static let rayBanMetaDisplay = GlassesConfiguration(model: .rayBanMetaDisplay, hasDisplay: true)
}
