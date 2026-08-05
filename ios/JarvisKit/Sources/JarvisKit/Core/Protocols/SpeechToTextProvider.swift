import Foundation

public struct TranscriptionResult: Sendable, Equatable {
    public let text: String
    public let isFinal: Bool

    public init(text: String, isFinal: Bool) {
        self.text = text
        self.isFinal = isFinal
    }
}

public enum SpeechToTextError: Error, Equatable {
    case permissionDenied
    case timeout
    case cancelled
    case recognitionFailed(String)
}

/// Streaming speech-to-text. Implementations must support cancellation and a
/// maximum listening duration — JARVIS never listens indefinitely.
public protocol SpeechToTextProvider: AnyObject, Sendable {
    func startTranscribing(maxDuration: TimeInterval) -> AsyncThrowingStream<TranscriptionResult, Error>
    func cancel()
}
