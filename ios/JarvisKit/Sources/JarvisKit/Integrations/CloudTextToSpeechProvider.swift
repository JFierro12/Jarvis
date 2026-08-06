import Foundation
import AVFoundation

/// Remote TTS via the backend's `/v1/speech/synthesize` endpoint — the real
/// audio synthesis (e.g. a cloned voice via ElevenLabs) happens server-side;
/// this only plays back whatever audio bytes come back. See
/// docs/SETUP_BACKEND.md for configuring a real provider.
public final class CloudTextToSpeechProvider: NSObject, TextToSpeechProvider, @unchecked Sendable {
    private struct RequestBody: Encodable {
        let text: String
        let voiceId: String
    }
    private struct ResponseBody: Decodable {
        let audioBase64: String
        let format: String
    }

    private let client: BackendAPIClient
    private var player: AVAudioPlayer?
    private var resumer: SpeechResume?

    public init(client: BackendAPIClient) {
        self.client = client
    }

    public var isSpeaking: Bool {
        player?.isPlaying ?? false
    }

    public func speak(_ text: String, settings: VoiceSettings) async {
        NSLog("[JarvisTTS] speak() called with text=\(text.prefix(40))")
        // Barge-in: a new utterance always interrupts whatever is playing.
        if let player, player.isPlaying {
            player.stop()
        }
        resumer?.resume()
        resumer = nil

        let response: ResponseBody
        do {
            response = try await client.post(
                path: "/v1/speech/synthesize",
                body: RequestBody(text: text, voiceId: settings.voiceIdentifier),
                timeout: 15
            )
            NSLog("[JarvisTTS] backend responded, audio_base64 length=\(response.audioBase64.count) format=\(response.format)")
        } catch {
            NSLog("[JarvisTTS] backend request threw: \(error)")
            return
        }

        guard let data = Data(base64Encoded: response.audioBase64) else {
            NSLog("[JarvisTTS] base64 decode failed")
            return
        }
        NSLog("[JarvisTTS] decoded \(data.count) bytes of audio, attempting playback")

        await withCheckedContinuation { continuation in
            let resumer = SpeechResume(continuation)
            self.resumer = resumer

            do {
                let player = try AVAudioPlayer(data: data)
                player.delegate = self
                self.player = player
                let started = player.play()
                NSLog("[JarvisTTS] player.play() returned \(started), duration=\(player.duration)")
                guard started else {
                    resumer.resume()
                    return
                }
            } catch {
                NSLog("[JarvisTTS] AVAudioPlayer init threw: \(error)")
                resumer.resume()
                return
            }

            // Safety net: AVAudioPlayerDelegate has been observed not to
            // fire reliably over some Bluetooth routes — without this, a
            // dropped callback would hang speak() forever, which blocks the
            // entire assistant turn behind it (this is what produced "no
            // acknowledgement and stuck on listening": the wake-word cue is
            // spoken via this same method, before recording even starts).
            Task {
                try? await Task.sleep(nanoseconds: 20_000_000_000)
                if resumer.resume() {
                    NSLog("[JarvisTTS] 20s safety timeout fired — delegate never called back")
                }
            }
        }
        NSLog("[JarvisTTS] speak() returning")
    }

    public func stopSpeaking() {
        player?.stop()
        resumer?.resume()
        resumer = nil
    }
}

extension CloudTextToSpeechProvider: AVAudioPlayerDelegate {
    public func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        NSLog("[JarvisTTS] audioPlayerDidFinishPlaying successfully=\(flag)")
        resumer?.resume()
        resumer = nil
    }

    public func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        resumer?.resume()
        resumer = nil
    }
}

/// Resumes a `CheckedContinuation` exactly once even when two sources race
/// to resume it (the delegate callback vs. the timeout safety net).
private final class SpeechResume: @unchecked Sendable {
    private var continuation: CheckedContinuation<Void, Never>?
    private let lock = NSLock()

    init(_ continuation: CheckedContinuation<Void, Never>) {
        self.continuation = continuation
    }

    /// Returns whether this call actually resumed something (false if
    /// already resumed by another source).
    @discardableResult
    func resume() -> Bool {
        lock.lock()
        let current = continuation
        continuation = nil
        lock.unlock()
        current?.resume()
        return current != nil
    }
}
