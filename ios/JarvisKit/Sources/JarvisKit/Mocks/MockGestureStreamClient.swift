import Foundation

public final class MockGestureStreamClient: GestureStreamClient, @unchecked Sendable {
    public private(set) var sentEvents: [GestureEvent] = []
    public private(set) var connectCallCount = 0
    public private(set) var disconnectCallCount = 0
    public var shouldFailConnect = false

    public init() {}

    public func connect() async throws {
        connectCallCount += 1
        if shouldFailConnect {
            throw GestureStreamError.connectionFailed("mock connect failure")
        }
    }

    public func send(_ event: GestureEvent) async {
        sentEvents.append(event)
    }

    public func disconnect() async {
        disconnectCallCount += 1
    }
}
