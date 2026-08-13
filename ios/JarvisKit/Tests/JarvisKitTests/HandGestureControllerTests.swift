import XCTest
@testable import JarvisKit

final class HandGestureControllerTests: XCTestCase {
    func testStartConnectsStreamAndStartsGlassesVideo() async throws {
        let wearable = MockWearableDeviceClient()
        let gestureStream = MockGestureStreamClient()
        let controller = LiveHandGestureController(wearableClient: wearable, gestureStreamClient: gestureStream)

        try await controller.start()

        XCTAssertEqual(gestureStream.connectCallCount, 1)
        XCTAssertEqual(wearable.startVideoStreamCallCount, 1)
    }

    func testStopIsIdempotentAndTearsDownBothSides() async throws {
        let wearable = MockWearableDeviceClient()
        let gestureStream = MockGestureStreamClient()
        let controller = LiveHandGestureController(wearableClient: wearable, gestureStreamClient: gestureStream)

        try await controller.start()
        await controller.stop()
        await controller.stop()

        XCTAssertEqual(wearable.stopVideoStreamCallCount, 1, "a second stop() while already inactive must be a no-op")
        XCTAssertEqual(gestureStream.disconnectCallCount, 1)
    }

    func testStartWhileAlreadyActiveIsIgnored() async throws {
        let wearable = MockWearableDeviceClient()
        let gestureStream = MockGestureStreamClient()
        let controller = LiveHandGestureController(wearableClient: wearable, gestureStreamClient: gestureStream)

        try await controller.start()
        try await controller.start()

        XCTAssertEqual(gestureStream.connectCallCount, 1, "a second start() while already active must be a no-op")
        XCTAssertEqual(wearable.startVideoStreamCallCount, 1)
    }

    func testStateTransitionsInactiveStartingActiveInactive() async throws {
        let wearable = MockWearableDeviceClient()
        let gestureStream = MockGestureStreamClient()
        let controller = LiveHandGestureController(wearableClient: wearable, gestureStreamClient: gestureStream)

        try await controller.start()
        await controller.stop()

        // AsyncStream's default buffering is unbounded, so every state
        // yielded before this loop starts consuming is still delivered in
        // order — no race against start()/stop() having already returned.
        var collected: [GestureControlState] = []
        for await state in controller.state {
            collected.append(state)
            if collected.count == 4 { break }
        }
        XCTAssertEqual(collected, [.inactive, .starting, .active, .inactive])
    }

    func testStartFailureSurfacesAsErrorState() async {
        let wearable = MockWearableDeviceClient()
        let gestureStream = MockGestureStreamClient()
        gestureStream.shouldFailConnect = true
        let controller = LiveHandGestureController(wearableClient: wearable, gestureStreamClient: gestureStream)

        do {
            try await controller.start()
            XCTFail("expected connect() failure to propagate")
        } catch {
            XCTAssertEqual(wearable.startVideoStreamCallCount, 0, "should not start the glasses stream if the WS connect already failed")
        }
    }
}
