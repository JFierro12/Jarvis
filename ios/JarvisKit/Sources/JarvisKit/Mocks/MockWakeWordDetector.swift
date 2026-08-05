import Foundation

public final class MockWakeWordDetector: WakeWordDetector, @unchecked Sendable {
    public let wakePhrase: String
    private let continuation: AsyncStream<WakeWordDetectorState>.Continuation
    public let state: AsyncStream<WakeWordDetectorState>

    public init(wakePhrase: String = "Jarvis") {
        self.wakePhrase = wakePhrase
        var continuation: AsyncStream<WakeWordDetectorState>.Continuation!
        self.state = AsyncStream { continuation = $0 }
        self.continuation = continuation
        self.continuation.yield(.idle)
    }

    public func start() async throws {
        continuation.yield(.listening)
    }

    public func stop() async {
        continuation.yield(.idle)
    }

    public func simulateDetection() {
        continuation.yield(.detected)
    }
}
