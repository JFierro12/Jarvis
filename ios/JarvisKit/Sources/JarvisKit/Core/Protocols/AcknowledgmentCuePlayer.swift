import Foundation

/// Plays a short local "I heard you" sound in response to the wake word,
/// instead of speaking a phrase through `TextToSpeechProvider`. Kept as its
/// own protocol rather than folded into `TextToSpeechProvider` — this is a
/// fixed, local UI cue with no text/voice-settings surface, not a speech
/// capability.
public protocol AcknowledgmentCuePlayer: AnyObject, Sendable {
    func play() async
}
