import Foundation
import Foundation
import Speech
import AVFoundation

/// Foreground-only, on-device wake word detection using Apple's `Speech`
/// framework. Continuously restarts short recognition passes (Apple imposes
/// an undocumented duration limit on a single `SFSpeechRecognitionTask`) and
/// checks each partial transcript for `wakePhrase`. This has no relationship
/// to the glasses' firmware "Hey Meta" phrase — see docs/META_SDK_NOTES.md.
///
/// Requires `NSSpeechRecognitionUsageDescription` and
/// `NSMicrophoneUsageDescription` in Info.plist, and both permissions
/// granted before `start()` is called.
public final class AppleWakeWordDetector: NSObject, WakeWordDetector, @unchecked Sendable {
    public let wakePhrase: String
    public let state: AsyncStream<WakeWordDetectorState>
    private let stateContinuation: AsyncStream<WakeWordDetectorState>.Continuation

    private let recognizer: SFSpeechRecognizer?
    private let audioEngine = AVAudioEngine()
    private var recognitionTask: SFSpeechRecognitionTask?
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var isRunning = false
    private var restartWorkItem: DispatchWorkItem?
    /// Consecutive restart-on-error count with no successful result in
    /// between. kAFAssistantErrorDomain 1101 has been observed to fail
    /// *immediately and identically* on every retry rather than being a
    /// one-off transient hiccup — without this, a fixed 0.3s restart spins
    /// hundreds of times a second indefinitely, hammering the speech
    /// service and starving the rest of the app of CPU/audio resources.
    private var consecutiveErrorCount = 0
    private static let maxConsecutiveErrors = 5

    /// Every engine/tap mutation (install/remove tap, start/stop engine,
    /// scheduling a restart) runs strictly serialized on this dedicated
    /// background queue — never the main thread (avoids blocking the UI on
    /// Bluetooth audio route negotiation) and never concurrently with
    /// itself (avoids the fatal "nullptr == Tap()" assertion that fires if
    /// installTap() is called twice before a prior tap is removed, which a
    /// naive `Task.detached` restart loop can race into).
    private let engineQueue = DispatchQueue(label: "com.johnathanfierro.jarvis.wakeword.engine")

    public init(wakePhrase: String = "jarvis") {
        self.wakePhrase = wakePhrase
        self.recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
        var continuation: AsyncStream<WakeWordDetectorState>.Continuation!
        self.state = AsyncStream { continuation = $0 }
        self.stateContinuation = continuation
        super.init()
        stateContinuation.yield(.idle)
        // Bluetooth route changes (e.g. the glasses connecting/disconnecting,
        // or the OS renegotiating HFP) can happen at any time, not just when
        // this app explicitly changes the preferred input — including the
        // "API MISUSE: CBCentralManager..." reconnection churn seen
        // throughout this app's logs. A tap installed against a stale format
        // crashes with an uncatchable CoreAudio NSException the next time
        // the engine renders audio ("Input HW format and tap format not
        // matching") — Swift's try/catch cannot intercept an NSException, so
        // the only real fix is to never let the tap go stale: rebuild it
        // whenever the engine reports its configuration actually changed.
        NotificationCenter.default.addObserver(self, selector: #selector(handleConfigurationChange), name: .AVAudioEngineConfigurationChange, object: audioEngine)
    }

    @objc private func handleConfigurationChange(_ note: Notification) {
        engineQueue.async { [weak self] in
            guard let self, self.isRunning else { return }
            self.beginListeningCycle()
        }
    }

    /// Requests both permissions this feature needs — speech recognition
    /// and microphone record access. Neither is requested anywhere else in
    /// the app today (the push-to-talk path has the same gap), so this is
    /// the first real trigger for either system prompt.
    public static func requestAuthorization() async -> Bool {
        let speechGranted = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status == .authorized)
            }
        }
        guard speechGranted else { return false }
        return await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { granted in
                continuation.resume(returning: granted)
            }
        }
    }

    public func start() async throws {
        guard let recognizer, recognizer.isAvailable else {
            stateContinuation.yield(.unavailable(reason: "Speech recognizer unavailable"))
            throw SpeechToTextError.recognitionFailed("recognizer unavailable")
        }
        guard !isRunning else { return }
        isRunning = true
        consecutiveErrorCount = 0
        engineQueue.async { [weak self] in
            self?.beginListeningCycle()
        }
    }

    public func stop() async {
        isRunning = false
        engineQueue.sync { [weak self] in
            self?.restartWorkItem?.cancel()
            self?.restartWorkItem = nil
            self?.teardownAudio()
        }
        stateContinuation.yield(.idle)
    }

    /// Must only be called on `engineQueue`.
    private func beginListeningCycle() {
        guard isRunning else { return }
        teardownAudio()

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        // Continuous, indefinite listening should stay fully on-device
        // rather than streaming audio to Apple's servers.
        request.requiresOnDeviceRecognition = true
        recognitionRequest = request

        let inputNode = audioEngine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            request.append(buffer)
        }

        do {
            audioEngine.prepare()
            try audioEngine.start()
        } catch {
            stateContinuation.yield(.unavailable(reason: "Audio engine failed to start: \(error.localizedDescription)"))
            isRunning = false
            return
        }

        stateContinuation.yield(.listening)

        recognitionTask = recognizer?.recognitionTask(with: request) { [weak self] result, error in
            // The SDK delivers this on an arbitrary thread — hop back onto
            // engineQueue before touching any engine state or scheduling a
            // restart, so it can never race a concurrent teardown/restart.
            guard let self else { return }
            self.engineQueue.async {
                self.handleRecognitionCallback(result: result, error: error)
            }
        }
    }

    /// Must only be called on `engineQueue`.
    private func handleRecognitionCallback(result: SFSpeechRecognitionResult?, error: Error?) {
        if let result {
            consecutiveErrorCount = 0
            let text = result.bestTranscription.formattedString.lowercased()
            if text.contains(wakePhrase.lowercased()) {
                stateContinuation.yield(.detected)
                scheduleRestart(after: 0.5)
                return
            }
            if result.isFinal {
                scheduleRestart(after: 0.1)
                return
            }
        }
        if let error {
            consecutiveErrorCount += 1
            NSLog("[JarvisWakeWord] recognition error (\(consecutiveErrorCount)/\(Self.maxConsecutiveErrors)): \(error)")
            guard consecutiveErrorCount < Self.maxConsecutiveErrors else {
                NSLog("[JarvisWakeWord] giving up after \(consecutiveErrorCount) consecutive errors — stopping instead of spinning forever")
                isRunning = false
                teardownAudio()
                stateContinuation.yield(.unavailable(reason: "Speech recognition kept failing (kAFAssistantErrorDomain). Try turning the wake word toggle off and back on in Settings."))
                return
            }
            // Exponential backoff (0.3s, 0.6s, 1.2s, 2.4s, ...) instead of a
            // fixed delay — gives a genuinely transient failure room to
            // clear before the next attempt, rather than retrying at the
            // same rate that just failed four times in a row.
            let backoff = 0.3 * pow(2.0, Double(consecutiveErrorCount - 1))
            scheduleRestart(after: backoff)
        }
    }

    /// Must only be called on `engineQueue`.
    private func scheduleRestart(after seconds: Double) {
        guard isRunning else { return }
        teardownAudio()
        let workItem = DispatchWorkItem { [weak self] in
            guard let self, self.isRunning else { return }
            self.beginListeningCycle()
        }
        restartWorkItem = workItem
        engineQueue.asyncAfter(deadline: .now() + seconds, execute: workItem)
    }

    /// Must only be called on `engineQueue`.
    private func teardownAudio() {
        if audioEngine.isRunning {
            audioEngine.stop()
        }
        audioEngine.inputNode.removeTap(onBus: 0)
        recognitionRequest?.endAudio()
        recognitionTask?.cancel()
        recognitionTask = nil
        recognitionRequest = nil
    }
}
