import Foundation
import UIKit
import MWDATCore
import MWDATCamera
#if DEBUG
import MWDATMockDevice
#endif

/// Real `WearableDeviceClient` adapter over the Meta Wearables Device Access
/// Toolkit. Every SDK call here is taken directly from the verified API
/// surface in docs/META_SDK_NOTES.md (sourced from the DAT repo's AGENTS.md
/// and CameraAccess sample) — nothing here is invented.
///
/// Not yet exercised against physical hardware in this session (no glasses
/// were available); verified only through the documented call shapes and,
/// where `useMockDevice` is true, through MockDeviceKit.
///
/// TODO(hardware-verification): confirm real-device `capturePhoto()` latency,
/// exact `RegistrationState`/`DeviceSessionState` case names against the
/// installed SDK version, and firmware/app update flows
/// (`openFirmwareUpdate()` / `openDATGlassesAppUpdate()`) once a physical
/// device or a registered Wearables Developer Center project is available.
public final class MetaWearableDeviceClient: WearableDeviceClient, @unchecked Sendable {
    public private(set) var capabilities: WearableCapabilities
    public let connectionState: AsyncStream<WearableConnectionState>
    private let stateContinuation: AsyncStream<WearableConnectionState>.Continuation

    private let wearables = Wearables.shared
    private var deviceSession: DeviceSession?
    private var camera: Camera?
    private var stream: MWDATCamera.Stream?
    private var listenerTokens: [any AnyListenerToken] = []

    private var pendingPhotoContinuation: CheckedContinuation<CapturedImage, Error>?
    private var frameContinuation: AsyncThrowingStream<VideoFrame, Error>.Continuation?

    private let useMockDevice: Bool

    /// Call once at app launch, before constructing this adapter.
    public static func configureSDK() throws {
        try Wearables.configure()
    }

    /// Route a `.onOpenURL` callback from Meta AI into the SDK.
    public static func handleCallback(url: URL) async {
        _ = try? await Wearables.shared.handleUrl(url)
    }

    public init(capabilities: WearableCapabilities = .standardRayBanMeta, useMockDevice: Bool = false) {
        self.capabilities = capabilities
        self.useMockDevice = useMockDevice
        var continuation: AsyncStream<WearableConnectionState>.Continuation!
        self.connectionState = AsyncStream { continuation = $0 }
        self.stateContinuation = continuation
        stateContinuation.yield(.disconnected)
    }

    public func registerApplication() async throws {
        #if DEBUG
        if useMockDevice {
            MockDeviceKit.shared.enable()
            return
        }
        #endif
        try await wearables.startRegistration()
    }

    public func checkPermissionStatus(_ permission: DevicePermission) async throws -> PermissionStatus {
        let status = try await wearables.checkPermissionStatus(.camera)
        return Self.mapPermissionStatus(status)
    }

    public func requestPermission(_ permission: DevicePermission) async throws -> PermissionStatus {
        let status = try await wearables.requestPermission(.camera)
        return Self.mapPermissionStatus(status)
    }

    /// Generic over the SDK's own permission-status return type so this file
    /// never has to spell out (and risk mis-guessing) that type's exact name —
    /// AGENTS.md documents the call shape but not the concrete type identifier.
    /// TODO(hardware-verification): confirm the exact case names
    /// (`granted`/`denied`/pending) against the installed SDK version.
    private static func mapPermissionStatus<T>(_ status: T) -> PermissionStatus {
        switch "\(status)" {
        case "granted": return .granted
        case "denied": return .denied
        default: return .notDetermined
        }
    }

    public func connect() async throws {
        stateContinuation.yield(.connecting)

        #if DEBUG
        if useMockDevice {
            let mockDevice = try MockDeviceKit.shared.pairGlasses(model: .rayBanMeta)
            mockDevice.powerOn()
            mockDevice.unfold()
            mockDevice.don()
        }
        #endif

        let selector = AutoDeviceSelector(wearables: wearables)
        let session = try wearables.createSession(deviceSelector: selector)
        try session.start()

        for await sessionState in session.stateStream() {
            if "\(sessionState)" == "started" {
                break
            }
            if "\(sessionState)" == "stopped" {
                stateContinuation.yield(.error("Device session stopped before starting."))
                return
            }
        }

        self.deviceSession = session
        stateContinuation.yield(.connected(.rayBanMeta))
    }

    public func disconnect() async {
        camera?.stop()
        deviceSession?.stop()
        camera = nil
        stream = nil
        deviceSession = nil
        listenerTokens.removeAll()
        stateContinuation.yield(.disconnected)
    }

    public func capturePhoto() async throws -> CapturedImage {
        guard let deviceSession else { throw WearableClientError.sessionNotStarted }

        let camera: Camera
        if let existing = self.camera {
            camera = existing
        } else {
            let config = StreamConfiguration(videoCodec: .raw, resolution: .low, frameRate: 15)
            guard let created = try deviceSession.addCamera(config: config) else {
                throw WearableClientError.cameraBusy
            }
            camera = created
            self.camera = camera
            self.stream = camera.stream
        }

        guard let stream else { throw WearableClientError.cameraBusy }

        return try await withCheckedThrowingContinuation { continuation in
            self.pendingPhotoContinuation = continuation
            let token = stream.photoDataPublisher.listen { [weak self] photoData in
                guard let self else { return }
                self.pendingPhotoContinuation?.resume(returning: CapturedImage(data: photoData.data, format: .jpeg))
                self.pendingPhotoContinuation = nil
            }
            listenerTokens.append(token)
            stream.start()
            stream.capturePhoto(format: .jpeg)
        }
    }

    public func startVideoStream(configuration: VideoStreamConfiguration) async throws {
        guard capabilities.supportsVideoStreaming else { throw WearableClientError.streamingUnsupported }
        guard let deviceSession else { throw WearableClientError.sessionNotStarted }

        let sdkResolution: MWDATCamera.StreamingResolution
        switch configuration.resolution {
        case .high: sdkResolution = .high
        case .medium: sdkResolution = .medium
        case .low: sdkResolution = .low
        }

        let camera: Camera
        if let existing = self.camera {
            camera = existing
        } else {
            let config = StreamConfiguration(videoCodec: .raw, resolution: sdkResolution, frameRate: UInt(configuration.frameRate))
            guard let created = try deviceSession.addCamera(config: config) else {
                throw WearableClientError.cameraBusy
            }
            camera = created
            self.camera = camera
        }
        let stream = camera.stream
        self.stream = stream

        let token = stream.videoFramePublisher.listen { [weak self] frame in
            guard let self, let image = frame.makeUIImage(), let data = image.jpegData(compressionQuality: 0.7) else { return }
            self.frameContinuation?.yield(VideoFrame(data: data, timestamp: Date().timeIntervalSince1970, width: Int(image.size.width), height: Int(image.size.height)))
        }
        listenerTokens.append(token)
        stream.start()
    }

    public func stopVideoStream() async {
        camera?.stop()
        frameContinuation?.finish()
        frameContinuation = nil
    }

    public func frames() -> AsyncThrowingStream<VideoFrame, Error> {
        AsyncThrowingStream { continuation in
            self.frameContinuation = continuation
        }
    }
}
