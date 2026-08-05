import Foundation
import CoreGraphics

public enum GlassesModel: String, Codable, Sendable, CaseIterable {
    case rayBanMeta
    case rayBanMetaDisplay
    case oakleyMetaHSTN
    case oakleyMetaVanguard
    case rayBanMetaOptics
    case unknown
}

public enum WearableConnectionState: Equatable, Sendable {
    case unavailable
    case disconnected
    case connecting
    case connected(GlassesModel)
    case paused
    case error(String)
}

/// What the currently connected device can actually do. Populated from the real
/// SDK's `DeviceType`/`compatibility()` when available, or from mock config in dev.
public struct WearableCapabilities: Equatable, Sendable {
    public var hasCamera: Bool
    public var hasDisplay: Bool
    public var supportsVideoStreaming: Bool
    /// Always false today — see docs/META_SDK_NOTES.md. Do not set this to true
    /// without a citation to current SDK documentation proving it.
    public var supportsNativeVoiceInvocation: Bool

    public init(
        hasCamera: Bool = true,
        hasDisplay: Bool = false,
        supportsVideoStreaming: Bool = true,
        supportsNativeVoiceInvocation: Bool = false
    ) {
        self.hasCamera = hasCamera
        self.hasDisplay = hasDisplay
        self.supportsVideoStreaming = supportsVideoStreaming
        self.supportsNativeVoiceInvocation = supportsNativeVoiceInvocation
    }

    public static let standardRayBanMeta = WearableCapabilities(hasCamera: true, hasDisplay: false, supportsVideoStreaming: true, supportsNativeVoiceInvocation: false)
    public static let rayBanMetaDisplay = WearableCapabilities(hasCamera: true, hasDisplay: true, supportsVideoStreaming: true, supportsNativeVoiceInvocation: false)
}

public enum DevicePermission: Sendable {
    case camera
}

public enum PermissionStatus: Equatable, Sendable {
    case notDetermined
    case granted
    case denied
}

public struct CapturedImage: Sendable {
    public let data: Data
    public let format: ImageFormat
    public let capturedAt: Date
    public let width: Int?
    public let height: Int?

    public init(data: Data, format: ImageFormat, capturedAt: Date = Date(), width: Int? = nil, height: Int? = nil) {
        self.data = data
        self.format = format
        self.capturedAt = capturedAt
        self.width = width
        self.height = height
    }
}

public enum ImageFormat: String, Sendable {
    case jpeg
    case png
}

public struct VideoFrame: Sendable {
    public let data: Data
    public let timestamp: TimeInterval
    public let width: Int
    public let height: Int
    public let sharpnessScore: Double?

    public init(data: Data, timestamp: TimeInterval, width: Int, height: Int, sharpnessScore: Double? = nil) {
        self.data = data
        self.timestamp = timestamp
        self.width = width
        self.height = height
        self.sharpnessScore = sharpnessScore
    }
}

/// Named distinctly from the SDK's own `MWDATCamera.StreamResolution` to avoid
/// any ambiguity when both are visible in the same file.
public enum CaptureResolution: String, Sendable {
    case high   // 720x1280
    case medium // 504x896
    case low    // 360x640
}

public struct VideoStreamConfiguration: Sendable {
    public let resolution: CaptureResolution
    /// Valid values per the DAT SDK: 2, 7, 15, 24, 30.
    public let frameRate: Int
    public let maxDurationSeconds: TimeInterval

    public init(resolution: CaptureResolution = .low, frameRate: Int = 15, maxDurationSeconds: TimeInterval = 30) {
        self.resolution = resolution
        self.frameRate = frameRate
        self.maxDurationSeconds = maxDurationSeconds
    }
}

public enum WearableClientError: Error, Equatable, LocalizedError {
    case notRegistered
    case registrationFailed(String)
    case permissionDenied(DevicePermissionDescription)
    case deviceUnavailable
    case sessionNotStarted
    case cameraBusy
    case captureFailed(String)
    case streamingUnsupported

    public var errorDescription: String? {
        switch self {
        case .notRegistered: return "This app is not yet registered with Meta AI."
        case .registrationFailed(let reason): return "Registration failed: \(reason)"
        case .permissionDenied: return "Camera access was denied."
        case .deviceUnavailable: return "The glasses are disconnected."
        case .sessionNotStarted: return "The device session has not started yet."
        case .cameraBusy: return "The glasses camera is busy."
        case .captureFailed(let reason): return "Capture failed: \(reason)"
        case .streamingUnsupported: return "Video streaming is not supported on this device."
        }
    }
}

public struct DevicePermissionDescription: Equatable, Sendable {
    public let permission: String
    public init(_ permission: String) { self.permission = permission }
}
