import Foundation
import AVFoundation

/// The single owner of `AVAudioSession` configuration. Nothing else in the
/// app should call `AVAudioSession.sharedInstance().setCategory` — route
/// changes and interruptions are handled here and reflected out through
/// `currentRoute` so the rest of the app only ever reasons about
/// `AudioRoute`, never `AVAudioSession` directly.
public final class AVAudioSessionRouteManager: NSObject, AudioRouteManager, @unchecked Sendable {
    private let session = AVAudioSession.sharedInstance()
    private let continuation: AsyncStream<AudioRoute>.Continuation
    public let currentRoute: AsyncStream<AudioRoute>

    public override init() {
        var continuation: AsyncStream<AudioRoute>.Continuation!
        self.currentRoute = AsyncStream { continuation = $0 }
        self.continuation = continuation
        super.init()
        NotificationCenter.default.addObserver(self, selector: #selector(routeChanged), name: AVAudioSession.routeChangeNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(interruption), name: AVAudioSession.interruptionNotification, object: nil)
    }

    public func activateSession() throws {
        try session.setCategory(.playAndRecord, mode: .voiceChat, options: [.allowBluetooth, .allowBluetoothA2DP, .defaultToSpeaker])
        try session.setActive(true, options: [])
        continuation.yield(resolveCurrentRoute())
    }

    public func deactivateSession() throws {
        try session.setActive(false, options: [.notifyOthersOnDeactivation])
    }

    @objc private func routeChanged(_ note: Notification) {
        continuation.yield(resolveCurrentRoute())
    }

    @objc private func interruption(_ note: Notification) {
        // On interruption end, iOS expects the app to reactivate the session
        // itself before resuming audio work.
        guard let info = note.userInfo,
              let typeValue = info[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: typeValue) else { return }
        if type == .ended {
            try? activateSession()
        }
    }

    private func resolveCurrentRoute() -> AudioRoute {
        let route = session.currentRoute
        let output = route.outputs.first
        let input = route.inputs.first

        let outputPort: AudioPort
        switch output?.portType {
        case .bluetoothHFP: outputPort = .bluetoothHFP
        case .bluetoothA2DP, .bluetoothLE: outputPort = .bluetoothA2DP
        case .headphones, .headsetMic: outputPort = .wiredHeadset
        case .builtInSpeaker: outputPort = .iPhoneSpeaker
        default: outputPort = .unknown
        }

        let inputPort: AudioPort
        switch input?.portType {
        case .bluetoothHFP: inputPort = .bluetoothHFP
        case .headsetMic: inputPort = .wiredHeadset
        case .builtInMic: inputPort = .iPhoneMicrophone
        default: inputPort = .unknown
        }

        return AudioRoute(input: inputPort, output: outputPort, inputDeviceName: input?.portName, outputDeviceName: output?.portName)
    }
}
