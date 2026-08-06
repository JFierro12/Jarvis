import Foundation

public enum AudioPort: String, Sendable, Equatable {
    case iPhoneMicrophone
    case iPhoneSpeaker
    case bluetoothHFP
    case bluetoothA2DP
    case wiredHeadset
    case unknown
}

public struct AudioRoute: Sendable, Equatable {
    public let input: AudioPort
    public let output: AudioPort
    public let inputDeviceName: String?
    public let outputDeviceName: String?

    public init(input: AudioPort, output: AudioPort, inputDeviceName: String? = nil, outputDeviceName: String? = nil) {
        self.input = input
        self.output = output
        self.inputDeviceName = inputDeviceName
        self.outputDeviceName = outputDeviceName
    }

    public static let iphoneOnly = AudioRoute(input: .iPhoneMicrophone, output: .iPhoneSpeaker)

    public var glassesAreActiveOutput: Bool {
        output == .bluetoothHFP || output == .bluetoothA2DP
    }
}

/// Owns "which physical device is the mic/speaker right now" — never assumed,
/// always observed. The DAT SDK has no microphone/speaker API (see
/// docs/META_SDK_NOTES.md); this is pure `AVAudioSession` route inspection.
public protocol AudioRouteManager: AnyObject, Sendable {
    var currentRoute: AsyncStream<AudioRoute> { get }
    func activateSession() throws
    func deactivateSession() throws

    /// Bluetooth Classic can't do high-quality stereo output (A2DP) and live
    /// microphone input (HFP, low-bitrate mono) on the same device at once —
    /// as long as a Bluetooth accessory's mic is in use, that accessory's
    /// *output* also gets forced down to HFP quality. `true` prefers a
    /// connected Bluetooth accessory's mic (e.g. the glasses, while worn);
    /// `false` prefers the iPhone's own built-in mic, keeping any Bluetooth
    /// output (music, TTS) at full A2DP quality. Never throws on a missing
    /// port — falls back to whatever the system already has selected.
    func setPreferBluetoothInput(_ preferBluetooth: Bool)
}
