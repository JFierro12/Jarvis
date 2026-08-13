import Foundation

/// Routes deterministic commands locally, falling through to the language
/// model only for ambiguous or open-ended requests. See spec §12 — most
/// utterances should never touch a cloud model.
///
/// "Jarvis" is treated as an optional conversational prefix: "Jarvis, what
/// am I looking at?" and "What am I looking at?" route identically. The
/// prefix is not required once a session is already active.
public final class IntentRouter: Sendable {
    private struct Pattern {
        let intent: IntentKind
        let keywords: [String]
        let requiresVisualContext: Bool
        let requiresLocation: Bool
        let requiresConfirmation: Bool
    }

    private let patterns: [Pattern] = [
        Pattern(intent: .analyzeScene, keywords: ["what am i looking at", "what is this", "what's this", "identify this"], requiresVisualContext: true, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .readText, keywords: ["read this", "read the", "what does this error", "summarize this page"], requiresVisualContext: true, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .rememberScene, keywords: ["remember this", "remember where", "save this as a project note", "remember my"], requiresVisualContext: true, requiresLocation: true, requiresConfirmation: false),
        // "browsing".contains("browse") is true, so .stopBrowseMode must be
        // matched before .startBrowseMode's bare "browse" keyword, or "stop
        // browsing" would incorrectly route to .startBrowseMode. Both must
        // also precede .cancel (keywords include bare "stop") for the same
        // reason.
        Pattern(intent: .stopBrowseMode, keywords: ["stop browsing", "stop the browse", "end browsing", "end the browse", "exit browse mode", "exit browsing"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .startBrowseMode, keywords: ["browse"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .searchMemory, keywords: ["where did i last see", "where is my", "where are my", "search memory", "find my"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .getCalendar, keywords: ["what is next on my calendar", "what's next", "next event", "my calendar"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .createReminder, keywords: ["remind me to", "create a reminder", "set a reminder"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .saveNote, keywords: ["save this as a note", "take a note", "save a note"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .getPCStatus, keywords: ["check my computer", "computer status", "pc status", "is my computer"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .releaseCamera, keywords: ["turn off the camera", "turn off your camera", "release the camera", "turn off camera", "camera off", "turn off the camera light", "turn off your camera light"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .reconnectCamera, keywords: ["connect the camera", "reconnect the camera", "connect your camera", "turn the camera back on", "turn on the camera", "camera on", "reconnect the glasses", "connect the glasses"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .wakeUpCheck, keywords: ["are you awake", "are you up", "you up", "you awake"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .playGeniusPlaylist, keywords: ["who the genius in the room is", "genius in the room"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .shutDown, keywords: ["shut down", "shutdown"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .controlHomeDevice, keywords: ["turn off", "turn on", "dim", "set the light"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: true),
        Pattern(intent: .endConversation, keywords: ["thank you jarvis", "thank you, jarvis", "thank you sir", "thank you, sir", "thanks jarvis", "thanks sir", "that will be all", "that'll be all", "that is all", "that's all", "we're done here", "i'm done here"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .cancel, keywords: ["cancel", "never mind", "nevermind", "stop", "not right now", "not now", "no thanks"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .confirm, keywords: ["confirm", "yes", "do it", "go ahead"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .reject, keywords: ["no", "reject", "don't"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .help, keywords: ["help", "what can you do"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false)
    ]

    public init() {}

    /// Strips a leading "Jarvis," / "Hey Jarvis," prefix if present. Safe to
    /// call unconditionally — text without the prefix passes through unchanged.
    public func normalizingWakePrefix(_ text: String) -> String {
        var trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        for prefix in ["hey jarvis,", "hey jarvis", "jarvis,", "jarvis"] {
            if trimmed.lowercased().hasPrefix(prefix) {
                trimmed = String(trimmed.dropFirst(prefix.count))
                trimmed = trimmed.trimmingCharacters(in: CharacterSet(charactersIn: " ,").union(.whitespacesAndNewlines))
                break
            }
        }
        return trimmed.isEmpty ? text : trimmed
    }

    /// Returns a deterministic match with confidence, or `.openEnded` with a
    /// low confidence when nothing local matches — signaling the caller to
    /// fall through to `LanguageReasoningProvider`.
    public func route(_ rawText: String) -> IntentResult {
        let normalized = normalizingWakePrefix(rawText).lowercased()

        for pattern in patterns {
            for keyword in pattern.keywords where normalized.contains(keyword) {
                let confidence = confidence(for: normalized, keyword: keyword)
                return IntentResult(
                    intent: pattern.intent,
                    confidence: confidence,
                    requiresVisualContext: pattern.requiresVisualContext,
                    requiresLocation: pattern.requiresLocation,
                    requiresConfirmation: pattern.requiresConfirmation,
                    parameters: ["question": normalized]
                )
            }
        }

        return IntentResult(intent: .openEnded, confidence: 0.3, parameters: ["question": normalized])
    }

    private func confidence(for text: String, keyword: String) -> Double {
        // A near-exact match is more confident than a keyword buried in a longer sentence.
        let lengthRatio = Double(keyword.count) / Double(max(text.count, 1))
        return min(0.99, 0.75 + lengthRatio * 0.25)
    }
}
