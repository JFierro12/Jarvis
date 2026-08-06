import Foundation

public final class MockHandGestureController: HandGestureController, @unchecked Sendable {
    private let stateContinuation: AsyncStream<GestureControlState>.Continuation
    public let state: AsyncStream<GestureControlState>
    public private(set) var startCallCount = 0
    public private(set) var stopCallCount = 0
    public var currentState: GestureControlState = .inactive

    public init() {
        var continuation: AsyncStream<GestureControlState>.Continuation!
        self.state = AsyncStream { continuation = $0 }
        self.stateContinuation = continuation
        stateContinuation.yield(.inactive)
    }

    public func start() async throws {
        startCallCount += 1
        currentState = .active
        stateContinuation.yield(.starting)
        // A real suspension point, matching MockWearableDeviceClient.connect()
        // — without one, this async func can run to completion without ever
        // yielding to the scheduler, so a caller's `state` stream observer
        // Task (e.g. AssistantCoordinator.observeGestureControlState())
        // never gets a chance to run before start() returns.
        try? await Task.sleep(nanoseconds: 1_000_000)
        stateContinuation.yield(.active)
    }

    public func stop() async {
        stopCallCount += 1
        currentState = .inactive
        try? await Task.sleep(nanoseconds: 1_000_000)
        stateContinuation.yield(.inactive)
    }
}
