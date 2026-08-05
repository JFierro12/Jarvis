import Foundation

public final class MockAudioRouteManager: AudioRouteManager, @unchecked Sendable {
    private let continuation: AsyncStream<AudioRoute>.Continuation
    public let currentRoute: AsyncStream<AudioRoute>

    public init(initialRoute: AudioRoute = .iphoneOnly) {
        var continuation: AsyncStream<AudioRoute>.Continuation!
        self.currentRoute = AsyncStream { continuation = $0 }
        self.continuation = continuation
        self.continuation.yield(initialRoute)
    }

    public func activateSession() throws {}
    public func deactivateSession() throws {}

    public func simulateRouteChange(_ route: AudioRoute) {
        continuation.yield(route)
    }
}
