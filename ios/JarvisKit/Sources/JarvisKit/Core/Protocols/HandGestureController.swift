import Foundation

public enum GestureControlState: Equatable, Sendable {
    case inactive
    case starting
    case active
    case error(String)
}

/// Owns the full lifecycle of continuous hand-gesture control: starting the
/// glasses' video stream, classifying frames, and forwarding events to the
/// paired Mac. Mutually exclusive with point-in-time vision capture
/// (`WearableDeviceClient.capturePhoto()`) — enforcement of that lives in
/// `AssistantCoordinator`, not here.
public protocol HandGestureController: AnyObject, Sendable {
    var state: AsyncStream<GestureControlState> { get }
    func start() async throws
    func stop() async
}
