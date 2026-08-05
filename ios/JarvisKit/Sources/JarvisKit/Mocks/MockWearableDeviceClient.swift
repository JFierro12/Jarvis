import Foundation

/// Pure-Swift mock of `WearableDeviceClient` — no dependency on MWDATMockDevice,
/// so it runs anywhere (unit tests, SwiftUI previews, demo mode) without the
/// real SDK's device/registration lifecycle. For exercising the actual DAT
/// MockDeviceKit, see `MetaWearableDeviceClient` in Integrations, which can be
/// configured to wrap either the real hardware path or MockDeviceKit.
public final class MockWearableDeviceClient: WearableDeviceClient, @unchecked Sendable {
    public let capabilities: WearableCapabilities
    private let stateContinuation: AsyncStream<WearableConnectionState>.Continuation
    public let connectionState: AsyncStream<WearableConnectionState>
    private let demoImageData: Data
    private var permissionStatus: PermissionStatus

    public init(capabilities: WearableCapabilities = .standardRayBanMeta, demoImageData: Data = Data(), initialPermission: PermissionStatus = .granted) {
        self.capabilities = capabilities
        self.demoImageData = demoImageData
        self.permissionStatus = initialPermission
        var continuation: AsyncStream<WearableConnectionState>.Continuation!
        self.connectionState = AsyncStream { continuation = $0 }
        self.stateContinuation = continuation
        stateContinuation.yield(.disconnected)
    }

    public func registerApplication() async throws {}

    public func checkPermissionStatus(_ permission: DevicePermission) async throws -> PermissionStatus {
        permissionStatus
    }

    public func requestPermission(_ permission: DevicePermission) async throws -> PermissionStatus {
        permissionStatus
    }

    public func connect() async throws {
        stateContinuation.yield(.connecting)
        try await Task.sleep(nanoseconds: 50_000_000)
        stateContinuation.yield(.connected(.rayBanMeta))
    }

    public func disconnect() async {
        stateContinuation.yield(.disconnected)
    }

    public func capturePhoto() async throws -> CapturedImage {
        guard permissionStatus == .granted else {
            throw WearableClientError.permissionDenied(DevicePermissionDescription("camera"))
        }
        return CapturedImage(data: demoImageData, format: .jpeg)
    }

    public func startVideoStream(configuration: VideoStreamConfiguration) async throws {
        guard capabilities.supportsVideoStreaming else { throw WearableClientError.streamingUnsupported }
    }

    public func stopVideoStream() async {}

    public func frames() -> AsyncThrowingStream<VideoFrame, Error> {
        AsyncThrowingStream { continuation in
            continuation.finish()
        }
    }

    public func setPermission(_ status: PermissionStatus) {
        permissionStatus = status
    }
}
