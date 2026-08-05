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
        Pattern(intent: .searchMemory, keywords: ["where did i last see", "where is my", "where are my", "search memory", "find my"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .getCalendar, keywords: ["what is next on my calendar", "what's next", "next event", "my calendar"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .createReminder, keywords: ["remind me to", "create a reminder", "set a reminder"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .saveNote, keywords: ["save this as a note", "take a note", "save a note"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .getPCStatus, keywords: ["check my computer", "computer status", "pc status", "is my computer"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
        Pattern(intent: .controlHomeDevice, keywords: ["turn off", "turn on", "dim", "set the light"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: true),
        Pattern(intent: .cancel, keywords: ["cancel", "never mind", "nevermind", "stop"], requiresVisualContext: false, requiresLocation: false, requiresConfirmation: false),
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
