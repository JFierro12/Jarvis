import Foundation

/// Deterministic STT mock: replays a scripted transcript instead of listening.
/// Used in demo mode, previews, and tests.
public final class MockSpeechToTextProvider: SpeechToTextProvider, @unchecked Sendable {
    public var scriptedTranscript: String = "what am I looking at"
    private var cancelled = false

    public init(scriptedTranscript: String = "what am I looking at") {
        self.scriptedTranscript = scriptedTranscript
    }

    public func startTranscribing(maxDuration: TimeInterval) -> AsyncThrowingStream<TranscriptionResult, Error> {
        cancelled = false
        return AsyncThrowingStream { continuation in
            Task {
                if self.cancelled {
                    continuation.finish(throwing: SpeechToTextError.cancelled)
                    return
                }
                continuation.yield(TranscriptionResult(text: self.scriptedTranscript, isFinal: true))
                continuation.finish()
            }
        }
    }

    public func cancel() {
        cancelled = true
    }
}
