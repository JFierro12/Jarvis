import Foundation
import AudioToolbox

/// Plays a bundled local chime via `AudioServicesPlaySystemSound` — no
/// network round trip and no `AVAudioEngine`/`AVAudioSession` involvement,
/// so it's near-instant and doesn't interact with the wake-word/STT audio
/// session. This replaced a spoken "Yes, sir?" TTS acknowledgment, which
/// (especially through the cloud TTS provider) meant a full network round
/// trip before the app even started listening for the actual request.
public final class SystemSoundAcknowledgmentCuePlayer: AcknowledgmentCuePlayer, @unchecked Sendable {
    private var soundID: SystemSoundID = 0
    private let isLoaded: Bool

    public init(resourceName: String = "AcknowledgmentChime", resourceExtension: String = "wav", bundle: Bundle = .main) {
        guard let url = bundle.url(forResource: resourceName, withExtension: resourceExtension) else {
            NSLog("[JarvisAudio] acknowledgment chime asset not found in bundle — cue will be a no-op")
            self.isLoaded = false
            return
        }
        var createdID: SystemSoundID = 0
        let status = AudioServicesCreateSystemSoundID(url as CFURL, &createdID)
        if status == kAudioServicesNoError {
            self.soundID = createdID
            self.isLoaded = true
        } else {
            NSLog("[JarvisAudio] AudioServicesCreateSystemSoundID failed with status \(status) — cue will be a no-op")
            self.isLoaded = false
        }
    }

    deinit {
        if isLoaded {
            AudioServicesDisposeSystemSoundID(soundID)
        }
    }

    public func play() async {
        guard isLoaded else { return }
        AudioServicesPlaySystemSound(soundID)
    }
}
