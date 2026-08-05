import Foundation

public struct VoiceSettings: Sendable, Equatable {
    public var voiceIdentifier: String
    public var speakingRate: Double
    public var pitch: Double
    public var verbosity: Verbosity
    public var acknowledgementStyle: AcknowledgementStyle
    public var activationSoundEnabled: Bool
    public var interruptibleSpeechEnabled: Bool

    public init(
        voiceIdentifier: String = "en-US-calm-default",
        speakingRate: Double = 1.0,
        pitch: Double = 1.0,
        verbosity: Verbosity = .concise,
        acknowledgementStyle: AcknowledgementStyle = .minimal,
        activationSoundEnabled: Bool = true,
        interruptibleSpeechEnabled: Bool = true
    ) {
        self.voiceIdentifier = voiceIdentifier
        self.speakingRate = speakingRate
        self.pitch = pitch
        self.verbosity = verbosity
        self.acknowledgementStyle = acknowledgementStyle
        self.activationSoundEnabled = activationSoundEnabled
        self.interruptibleSpeechEnabled = interruptibleSpeechEnabled
    }

    public static let `default` = VoiceSettings()
}

public enum Verbosity: String, Sendable, Codable, CaseIterable {
    case minimal
    case concise
    case detailed
}

public enum AcknowledgementStyle: String, Sendable, Codable, CaseIterable {
    case none
    case minimal
    case friendly
}

/// Short, frequently-used phrases that implementations are encouraged to cache
/// rather than re-synthesize every time (see spec §11).
public enum CannedPhrase: String, Sendable, CaseIterable {
    case ready = "Ready."
    case listening = "Listening."
    case saved = "Saved."
    case needsConfirmation = "I need your confirmation."
    case glassesDisconnected = "The glasses are disconnected."
    case couldNotCompleteSafely = "I could not complete that safely."
}

/// Text-to-speech with barge-in: starting a new utterance must stop any
/// utterance currently in flight.
public protocol TextToSpeechProvider: AnyObject, Sendable {
    func speak(_ text: String, settings: VoiceSettings) async
    func stopSpeaking()
    var isSpeaking: Bool { get }
}
