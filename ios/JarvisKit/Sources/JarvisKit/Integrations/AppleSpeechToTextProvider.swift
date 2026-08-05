import Foundation
import Speech
import AVFoundation

/// On-device speech-to-text using Apple's `Speech` framework. Requires
/// `NSSpeechRecognitionUsageDescription` and `NSMicrophoneUsageDescription`
/// in Info.plist, and `SFSpeechRecognizer`/microphone permission granted
/// before `startTranscribing` is called.
public final class AppleSpeechToTextProvider: SpeechToTextProvider, @unchecked Sendable {
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let audioEngine = AVAudioEngine()
    private var recognitionTask: SFSpeechRecognitionTask?
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?

    public init() {}

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

            let request = SFSpeechAudioBufferRecognitionRequest()
            request.shouldReportPartialResults = true
            self.recognitionRequest = request

            let inputNode = audioEngine.inputNode
            let format = inputNode.outputFormat(forBus: 0)
            inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
                request.append(buffer)
            }

            do {
                audioEngine.prepare()
                try audioEngine.start()
            } catch {
                continuation.finish(throwing: SpeechToTextError.recognitionFailed("audio engine start failed"))
                return
            }

            let timeoutTask = Task {
                try await Task.sleep(nanoseconds: UInt64(maxDuration * 1_000_000_000))
                self.cancel()
                continuation.finish()
            }

            recognitionTask = recognizer.recognitionTask(with: request) { result, error in
                if let result {
                    continuation.yield(TranscriptionResult(text: result.bestTranscription.formattedString, isFinal: result.isFinal))
                    if result.isFinal {
                        timeoutTask.cancel()
                        continuation.finish()
                    }
                }
                if let error {
                    timeoutTask.cancel()
                    continuation.finish(throwing: SpeechToTextError.recognitionFailed(error.localizedDescription))
                }
            }
        }
    }

    public func cancel() {
        audioEngine.stop()
        audioEngine.inputNode.removeTap(onBus: 0)
        recognitionRequest?.endAudio()
        recognitionTask?.cancel()
        recognitionTask = nil
        recognitionRequest = nil
    }
}
