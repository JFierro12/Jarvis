import Foundation

public enum WakeWordDetectorState: Equatable, Sendable {
    case idle
    case listening
    case detected
    case unavailable(reason: String)
}

/// Foreground-only, on-phone wake word detection. This has no relationship to
/// the glasses' firmware "Hey Meta" wake phrase — see docs/META_SDK_NOTES.md.
/// It must only run while an authorized JARVIS session is active, and any UI
/// that surfaces `state` must show a visible listening indicator whenever
/// this reports `.listening`.
public protocol WakeWordDetector: AnyObject, Sendable {
    var state: AsyncStream<WakeWordDetectorState> { get }
    var wakePhrase: String { get }

    /// Starts listening. Callers must stop this when the app backgrounds —
    /// implementations must not attempt to keep running in the background.
    func start() async throws
    func stop() async
}
