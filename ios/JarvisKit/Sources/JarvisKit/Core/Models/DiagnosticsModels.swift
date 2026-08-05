import Foundation

/// Snapshot backing the developer diagnostics screen (spec §29). Assembled by
/// the app layer from whichever live components it has; every field here is
/// meant to be displayed as-is, with secrets never entering this struct in
/// the first place (see `SecretRedaction` for free-text fields).
public struct DiagnosticsSnapshot: Sendable, Equatable {
    public var sdkVersion: String
    public var appVersion: String
    public var deviceModel: String
    public var connectionState: String
    public var capabilities: WearableCapabilities
    public var cameraPermission: PermissionStatus
    public var microphonePermission: PermissionStatus
    public var speechPermission: PermissionStatus
    public var calendarPermission: PermissionStatus
    public var locationPermission: PermissionStatus
    public var audioRoute: AudioRoute
    public var streamState: String
    public var lastFrameTimestamp: Date?
    public var backendReachable: Bool
    public var lastRequestLatencyMs: Int?
    public var selectedProviders: [String: String]
    public var supportsNativeVoiceInvocation: Bool
    public var foregroundWakeWordEnabled: Bool
    public var imageStorageEnabled: Bool
    public var memoryDatabaseStatus: String

    public init(
        sdkVersion: String = "0.9.0",
        appVersion: String = "0.1.0-dev",
        deviceModel: String = "Simulator",
        connectionState: String = "disconnected",
        capabilities: WearableCapabilities = .standardRayBanMeta,
        cameraPermission: PermissionStatus = .notDetermined,
        microphonePermission: PermissionStatus = .notDetermined,
        speechPermission: PermissionStatus = .notDetermined,
        calendarPermission: PermissionStatus = .notDetermined,
        locationPermission: PermissionStatus = .notDetermined,
        audioRoute: AudioRoute = .iphoneOnly,
        streamState: String = "stopped",
        lastFrameTimestamp: Date? = nil,
        backendReachable: Bool = false,
        lastRequestLatencyMs: Int? = nil,
        selectedProviders: [String: String] = [:],
        supportsNativeVoiceInvocation: Bool = false,
        foregroundWakeWordEnabled: Bool = false,
        imageStorageEnabled: Bool = false,
        memoryDatabaseStatus: String = "ok"
    ) {
        self.sdkVersion = sdkVersion
        self.appVersion = appVersion
        self.deviceModel = deviceModel
        self.connectionState = connectionState
        self.capabilities = capabilities
        self.cameraPermission = cameraPermission
        self.microphonePermission = microphonePermission
        self.speechPermission = speechPermission
        self.calendarPermission = calendarPermission
        self.locationPermission = locationPermission
        self.audioRoute = audioRoute
        self.streamState = streamState
        self.lastFrameTimestamp = lastFrameTimestamp
        self.backendReachable = backendReachable
        self.lastRequestLatencyMs = lastRequestLatencyMs
        self.selectedProviders = selectedProviders
        self.supportsNativeVoiceInvocation = supportsNativeVoiceInvocation
        self.foregroundWakeWordEnabled = foregroundWakeWordEnabled
        self.imageStorageEnabled = imageStorageEnabled
        self.memoryDatabaseStatus = memoryDatabaseStatus
    }

    /// Multi-line, human-readable, secret-free text for the "copy diagnostics" button.
    public func renderedForClipboard() -> String {
        let lines = [
            "SDK version: \(sdkVersion)",
            "App version: \(appVersion)",
            "Device model: \(deviceModel)",
            "Connection: \(connectionState)",
            "Camera permission: \(cameraPermission)",
            "Microphone permission: \(microphonePermission)",
            "Speech permission: \(speechPermission)",
            "Calendar permission: \(calendarPermission)",
            "Location permission: \(locationPermission)",
            "Audio route: \(audioRoute.input.rawValue) in / \(audioRoute.output.rawValue) out",
            "Stream state: \(streamState)",
            "Backend reachable: \(backendReachable)",
            "Native voice invocation supported: \(supportsNativeVoiceInvocation)",
            "Foreground wake word enabled: \(foregroundWakeWordEnabled)",
            "Image storage enabled: \(imageStorageEnabled)",
            "Memory database: \(memoryDatabaseStatus)"
        ]
        return SecretRedaction.redacted(lines.joined(separator: "\n"))
    }
}
