import Foundation
import AVFoundation

/// On-device TTS via `AVSpeechSynthesizer`. This is the default, always-available
/// voice provider; a remote high-quality voice provider can be layered in
/// behind the same `TextToSpeechProvider` protocol (see
/// docs/ARCHITECTURE.md — not implemented in this pass, since it requires a
/// specific chosen vendor and API key the user hasn't configured yet).
public final class AppleTextToSpeechProvider: NSObject, TextToSpeechProvider, @unchecked Sendable {
    private let synthesizer = AVSpeechSynthesizer()
    private var continuation: CheckedContinuation<Void, Never>?

    public override init() {
        super.init()
        synthesizer.delegate = self
    }

    public var isSpeaking: Bool {
        synthesizer.isSpeaking
    }

    public func speak(_ text: String, settings: VoiceSettings) async {
        // Barge-in: a new utterance always interrupts whatever is playing.
        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }
        let utterance = AVSpeechUtterance(string: text)
        utterance.rate = Float(settings.speakingRate) * AVSpeechUtteranceDefaultSpeechRate
        utterance.pitchMultiplier = Float(settings.pitch)
        utterance.voice = AVSpeechSynthesisVoice(identifier: settings.voiceIdentifier) ?? AVSpeechSynthesisVoice(language: "en-US")

        await withCheckedContinuation { continuation in
            self.continuation = continuation
            synthesizer.speak(utterance)
        }
    }

    public func stopSpeaking() {
        synthesizer.stopSpeaking(at: .immediate)
    }
}

extension AppleTextToSpeechProvider: AVSpeechSynthesizerDelegate {
    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        continuation?.resume()
        continuation = nil
    }

    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        continuation?.resume()
        continuation = nil
    }
}
