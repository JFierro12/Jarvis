import Foundation
import Speech
import AVFoundation

/// On-device speech-to-text using Apple's `Speech` framework. Requires
/// `NSSpeechRecognitionUsageDescription` and `NSMicrophoneUsageDescription`
/// in Info.plist, and `SFSpeechRecognizer`/microphone permission granted
/// before `startTranscribing` is called.
public final class AppleSpeechToTextProvider: NSObject, SpeechToTextProvider, @unchecked Sendable {
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let audioEngine = AVAudioEngine()
    private var recognitionTask: SFSpeechRecognitionTask?
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?

    public override init() {
        super.init()
        // A route change mid-turn (Bluetooth renegotiating, the glasses
        // disconnecting, etc.) leaves the installed tap's format stale
        // relative to the hardware — the next render crashes the process
        // with an uncatchable CoreAudio NSException ("Input HW format and
        // tap format not matching"). Rather than risk restructuring the
        // silence-detection closure below to hot-swap the tap, fall back to
        // the same hard-stop `cancel()` already used elsewhere (e.g.
        // stopEverything()) — the user has to repeat the question, but
        // that's far better than a crash, and it's already-tested code.
        NotificationCenter.default.addObserver(self, selector: #selector(handleConfigurationChange), name: .AVAudioEngineConfigurationChange, object: audioEngine)
    }

    @objc private func handleConfigurationChange(_ note: Notification) {
        cancel()
    }

    public static func requestAuthorization() async -> Bool {
        await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status == .authorized)
            }
        }
    }

    public func startTranscribing(maxDuration: TimeInterval) -> AsyncThrowingStream<TranscriptionResult, Error> {
        AsyncThrowingStream { continuation in
            guard let recognizer, recognizer.isAvailable else {
                continuation.finish(throwing: SpeechToTextError.recognitionFailed("recognizer unavailable"))
                return
            }

            // AsyncThrowingStream's build closure runs synchronously and
            // immediately on whatever thread called startTranscribing() —
            // for AssistantCoordinator (main actor), that's the main
            // thread. audioEngine.start() can block on Bluetooth audio
            // route negotiation (the glasses are a connected Bluetooth
            // audio accessory), so the actual engine setup runs in a
            // detached task instead of inline, to avoid freezing the UI.
            Task.detached { [weak self] in
                guard let self else { return }
                let request = SFSpeechAudioBufferRecognitionRequest()
                request.shouldReportPartialResults = true
                self.recognitionRequest = request

                // Apple doesn't auto-endpoint on a pause in speech — without
                // this, every turn just runs for the full maxDuration
                // regardless of when the user actually stops talking. This
                // tracks simple silence-based cutoff instead: once real
                // speech has been heard, ~1.5s of subsequent quiet ends the
                // turn early.
                var timeoutTask: Task<Void, Error>?
                var graceTask: Task<Void, Never>?
                var hasDetectedSpeech = false
                var lastVoiceActivityAt = Date()
                var lastPartialText = ""
                var hasFinished = false
                let sessionStartedAt = Date()
                let silenceThreshold: Float = 0.015
                let silenceDuration: TimeInterval = 0.5
                let minimumListenDuration: TimeInterval = 0.6
                // All engine control and continuation-finishing happens on
                // this single queue — the tap callback runs on a real-time
                // audio thread and must never call back into the engine
                // directly, and serializing here also prevents two end
                // paths (silence vs. timeout vs. natural isFinal) from
                // double-finishing the continuation.
                let controlQueue = DispatchQueue(label: "com.johnathanfierro.jarvis.stt.control")

                // Ends the turn *gracefully*: tells the recognizer no more
                // audio is coming so it can finalize whatever it already
                // heard, rather than calling recognitionTask.cancel() (which
                // discards the in-progress transcript instead of finalizing
                // it — the bug that made every silence/timeout cutoff come
                // back with an empty question).
                func endTurnGracefully() {
                    controlQueue.async {
                        guard !hasFinished else { return }
                        timeoutTask?.cancel()
                        self.audioEngine.stop()
                        self.audioEngine.inputNode.removeTap(onBus: 0)
                        self.recognitionRequest?.endAudio()
                        // Safety net: if the recognizer never delivers a
                        // final result after endAudio() (rare, but seen
                        // with real hardware/transient service errors),
                        // force-finish with the last partial transcript
                        // instead of hanging or losing it.
                        graceTask = Task {
                            try? await Task.sleep(nanoseconds: 2_000_000_000)
                            controlQueue.async {
                                guard !hasFinished else { return }
                                hasFinished = true
                                if !lastPartialText.isEmpty {
                                    continuation.yield(TranscriptionResult(text: lastPartialText, isFinal: true))
                                }
                                self.recognitionTask?.cancel()
                                self.recognitionTask = nil
                                continuation.finish()
                            }
                        }
                    }
                }

                let inputNode = self.audioEngine.inputNode
                let format = inputNode.outputFormat(forBus: 0)
                inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
                    request.append(buffer)

                    let amplitude = Self.rmsAmplitude(of: buffer)
                    let now = Date()
                    if amplitude > silenceThreshold {
                        hasDetectedSpeech = true
                        lastVoiceActivityAt = now
                        return
                    }
                    guard hasDetectedSpeech,
                          now.timeIntervalSince(sessionStartedAt) > minimumListenDuration,
                          now.timeIntervalSince(lastVoiceActivityAt) > silenceDuration
                    else { return }
                    endTurnGracefully()
                }

                do {
                    self.audioEngine.prepare()
                    try self.audioEngine.start()
                } catch {
                    continuation.finish(throwing: SpeechToTextError.recognitionFailed("audio engine start failed"))
                    return
                }

                timeoutTask = Task {
                    try await Task.sleep(nanoseconds: UInt64(maxDuration * 1_000_000_000))
                    endTurnGracefully()
                }

                self.recognitionTask = recognizer.recognitionTask(with: request) { result, error in
                    controlQueue.async {
                        guard !hasFinished else { return }
                        if let result {
                            lastPartialText = result.bestTranscription.formattedString
                            continuation.yield(TranscriptionResult(text: lastPartialText, isFinal: result.isFinal))
                            if result.isFinal {
                                hasFinished = true
                                graceTask?.cancel()
                                timeoutTask?.cancel()
                                continuation.finish()
                            }
                        }
                        if let error {
                            hasFinished = true
                            graceTask?.cancel()
                            timeoutTask?.cancel()
                            continuation.finish(throwing: SpeechToTextError.recognitionFailed(error.localizedDescription))
                        }
                    }
                }
            }
        }
    }

    /// Hard stop — used when the user (or `stopEverything()`) explicitly
    /// cancels and no answer is wanted. Unlike the graceful end-of-turn path
    /// above, this intentionally discards whatever was in progress.
    public func cancel() {
        audioEngine.stop()
        audioEngine.inputNode.removeTap(onBus: 0)
        recognitionRequest?.endAudio()
        recognitionTask?.cancel()
        recognitionTask = nil
        recognitionRequest = nil
    }

    private static func rmsAmplitude(of buffer: AVAudioPCMBuffer) -> Float {
        guard let channelData = buffer.floatChannelData else { return 0 }
        let frameLength = Int(buffer.frameLength)
        guard frameLength > 0 else { return 0 }
        let samples = channelData[0]
        var sum: Float = 0
        for i in 0..<frameLength {
            sum += samples[i] * samples[i]
        }
        return sqrt(sum / Float(frameLength))
    }
}
