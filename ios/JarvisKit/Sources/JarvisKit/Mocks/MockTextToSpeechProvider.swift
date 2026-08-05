import Foundation

/// Records what would have been spoken instead of producing audio. Deterministic,
/// synchronous-feeling, and safe for XCTest / SwiftUI previews.
public final class MockTextToSpeechProvider: TextToSpeechProvider, @unchecked Sendable {
    public private(set) var spokenUtterances: [String] = []
    public private(set) var isSpeaking: Bool = false

    public init() {}

    public func speak(_ text: String, settings: VoiceSettings) async {
        isSpeaking = true
        spokenUtterances.append(text)
        // Simulate barge-in-safe short latency instead of blocking tests.
        try? await Task.sleep(nanoseconds: 1_000_000)
        isSpeaking = false
    }

    public func stopSpeaking() {
        isSpeaking = false
    }
}
