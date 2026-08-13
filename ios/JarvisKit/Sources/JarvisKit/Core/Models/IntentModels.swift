import Foundation

/// The closed set of deterministic intents JARVIS can route locally without
/// touching a language model. Anything outside this set (or below the
/// confidence floor) falls through to `LanguageReasoningProvider`.
public enum IntentKind: String, Codable, Sendable, CaseIterable {
    case analyzeScene = "analyze_scene"
    case identifyCoverage = "identify_coverage"
    case readText = "read_text"
    case rememberScene = "remember_scene"
    case searchMemory = "search_memory"
    case getCalendar = "get_calendar"
    case createReminder = "create_reminder"
    case saveNote = "save_note"
    case getPCStatus = "get_pc_status"
    case controlHomeDevice = "control_home_device"
    case releaseCamera = "release_camera"
    case reconnectCamera = "reconnect_camera"
    case wakeUpCheck = "wake_up_check"
    case shutDown = "shut_down"
    case startBrowseMode = "start_browse_mode"
    case stopBrowseMode = "stop_browse_mode"
    case playGeniusPlaylist = "play_genius_playlist"
    case startFootballAnalysisMode = "start_football_analysis_mode"
    case stopFootballAnalysisMode = "stop_football_analysis_mode"
    /// Synthetic — never produced by IntentRouter's keyword matching.
    /// AssistantCoordinator constructs this directly for any utterance
    /// heard while football analysis mode is active, so the user never has
    /// to say "identify the coverage" before every shorthand read.
    case footballShorthandAnalysis = "football_shorthand_analysis"
    case cancel
    case endConversation = "end_conversation"
    case confirm
    case reject
    case help
    case openEnded = "open_ended"
}

public struct IntentResult: Codable, Sendable, Equatable {
    public let intent: IntentKind
    public let confidence: Double
    public let requiresVisualContext: Bool
    public let requiresLocation: Bool
    public let requiresConfirmation: Bool
    public let parameters: [String: String]

    public init(
        intent: IntentKind,
        confidence: Double,
        requiresVisualContext: Bool = false,
        requiresLocation: Bool = false,
        requiresConfirmation: Bool = false,
        parameters: [String: String] = [:]
    ) {
        self.intent = intent
        self.confidence = confidence
        self.requiresVisualContext = requiresVisualContext
        self.requiresLocation = requiresLocation
        self.requiresConfirmation = requiresConfirmation
        self.parameters = parameters
    }
}

/// Minimum confidence a deterministic local match needs before we trust it
/// over routing to the language model.
public let intentConfidenceFloor = 0.6
