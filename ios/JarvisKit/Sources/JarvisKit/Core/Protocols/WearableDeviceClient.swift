import Foundation

/// Abstraction over the Meta Wearables Device Access Toolkit (or MockDeviceKit).
/// See docs/META_SDK_NOTES.md for exactly which of these calls map to a real,
/// documented DAT SDK API versus what is synthesized for testing.
///
/// This protocol intentionally does not expose microphone/speaker methods —
/// those are not part of the DAT SDK surface (see META_SDK_NOTES.md) and are
/// owned by `AudioRouteManager` / `AudioSessionCoordinator` instead.
public protocol WearableDeviceClient: AnyObject, Sendable {
    var connectionState: AsyncStream<WearableConnectionState> { get }
    var capabilities: WearableCapabilities { get }

    func registerApplication() async throws
    func checkPermissionStatus(_ permission: DevicePermission) async throws -> PermissionStatus
    func requestPermission(_ permission: DevicePermission) async throws -> PermissionStatus
    func connect() async throws
    func disconnect() async
    func capturePhoto() async throws -> CapturedImage
    func startVideoStream(configuration: VideoStreamConfiguration) async throws
    func stopVideoStream() async
    func frames() -> AsyncThrowingStream<VideoFrame, Error>
}
