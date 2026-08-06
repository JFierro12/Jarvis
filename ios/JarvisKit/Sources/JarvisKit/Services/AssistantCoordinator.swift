import Foundation

/// Application-service layer that sequences one JARVIS turn: activate, listen,
/// transcribe, decide intent, gather context, reason (locally or via a
/// provider), execute an authorized tool if needed, speak, return to idle.
///
/// This is deliberately *not* a god object: it owns no SDK/network/audio
/// logic itself. Every capability is an injected protocol, so this type is
/// fully testable with mocks and the concrete implementation can be swapped
/// (real Meta SDK vs MockDeviceKit, on-device vs cloud reasoning) without
/// touching this file.
@MainActor
public final class AssistantCoordinator: ObservableObject {
    @Published public private(set) var state: AssistantState = .idle
    @Published public private(set) var partialTranscript: String = ""
    @Published public private(set) var lastResponse: String = ""
    @Published public private(set) var wearableConnectionState: WearableConnectionState = .disconnected
    @Published public private(set) var gestureControlState: GestureControlState = .inactive

    private let stateMachine: AssistantStateMachine
    private let intentRouter: IntentRouter
    private let contextAssembler: ContextAssembler
    private let policyEngine: PolicyEngine
    private let confirmationManager: ConfirmationManager
    private let toolExecutor: ToolExecutor
    private let wearableClient: WearableDeviceClient
    private let speechToText: SpeechToTextProvider
    private let textToSpeech: TextToSpeechProvider
    private let visionProvider: VisionReasoningProvider
    private let languageProvider: LanguageReasoningProvider
    private let memoryRepository: MemoryRepository
    private let grantedPermissions: Set<String>
    private let voiceSettings: VoiceSettings
    private let handGestureController: HandGestureController
    private let musicPlayerController: MusicPlayerController

    /// True while waiting for the user's yes/no answer to "would you like
    /// directions on how to do so sir?" after starting browse mode.
    /// Deliberately coordinator-local instead of routed through
    /// `ConfirmationManager`/`PendingAction` — that type is purpose-built
    /// for risky tool-call confirmations tied to the `AssistantStateMachine`
    /// transition table, and forcing a plain dialogue branch through it
    /// would mean fabricating a fake `toolName`/`riskLevel`.
    private var awaitingBrowseDirectionsAnswer = false

    public init(
        wearableClient: WearableDeviceClient,
        speechToText: SpeechToTextProvider,
        textToSpeech: TextToSpeechProvider,
        visionProvider: VisionReasoningProvider,
        languageProvider: LanguageReasoningProvider,
        memoryRepository: MemoryRepository,
        toolExecutor: ToolExecutor,
        intentRouter: IntentRouter = IntentRouter(),
        contextAssembler: ContextAssembler = ContextAssembler(),
        policyEngine: PolicyEngine = PolicyEngine(),
        confirmationManager: ConfirmationManager = ConfirmationManager(),
        grantedPermissions: Set<String> = [],
        voiceSettings: VoiceSettings = .default,
        handGestureController: HandGestureController = MockHandGestureController(),
        musicPlayerController: MusicPlayerController = MockMusicPlayerController()
    ) {
        self.wearableClient = wearableClient
        self.speechToText = speechToText
        self.textToSpeech = textToSpeech
        self.visionProvider = visionProvider
        self.languageProvider = languageProvider
        self.memoryRepository = memoryRepository
        self.toolExecutor = toolExecutor
        self.intentRouter = intentRouter
        self.contextAssembler = contextAssembler
        self.policyEngine = policyEngine
        self.confirmationManager = confirmationManager
        self.grantedPermissions = grantedPermissions
        self.voiceSettings = voiceSettings
        self.handGestureController = handGestureController
        self.musicPlayerController = musicPlayerController
        self.stateMachine = AssistantStateMachine()
        observeWearableConnection()
        observeGestureControlState()
    }

    private func observeWearableConnection() {
        Task { [weak self] in
            guard let self else { return }
            for await state in self.wearableClient.connectionState {
                self.wearableConnectionState = state
            }
        }
    }

    private func observeGestureControlState() {
        Task { [weak self] in
            guard let self else { return }
            for await state in self.handGestureController.state {
                self.gestureControlState = state
            }
        }
    }

    /// Registers the app with Meta AI (if needed) and starts a device
    /// session. Safe to call again after a failure or disconnect. Progress
    /// and outcome are reflected in `wearableConnectionState`, which mirrors
    /// the wearable client's own connection stream.
    public func connectWearable() async {
        do {
            try await wearableClient.registerApplication()
            try await wearableClient.connect()
        } catch {
            wearableConnectionState = .error((error as? LocalizedError)?.errorDescription ?? "Could not connect to your glasses.")
        }
    }

    public func disconnectWearable() async {
        await wearableClient.disconnect()
    }

    /// Thin public wrapper so UI (e.g. a "stop browsing" button) can end
    /// gesture control without the coordinator exposing `handGestureController`
    /// itself.
    public func stopBrowseMode() async {
        await handleStopBrowseMode()
    }

    private func handleStartBrowseMode() async {
        if case .active = gestureControlState {
            await respond("Already browsing, sir.")
        } else {
            await respond("Browsing sir, would you like directions on how to do so sir?")
            awaitingBrowseDirectionsAnswer = true
        }
        move(to: .completed)
        stateMachine.reset()
        state = .idle
    }

    private func resolveBrowseDirectionsAnswer(userSaidYes: Bool) async {
        if userSaidYes {
            await respond(Self.gestureInstructions)
        }
        do {
            try await handGestureController.start()
            await respond("Enjoy your browse, sir.")
        } catch {
            await respond("I couldn't start gesture control, sir.")
        }
        move(to: .completed)
        stateMachine.reset()
        state = .idle
    }

    private func handleStopBrowseMode() async {
        if case .active = gestureControlState {
            await handGestureController.stop()
            await respond("Stopped browsing, sir.")
        } else {
            await respond("I wasn't browsing, sir.")
        }
        move(to: .completed)
        stateMachine.reset()
        state = .idle
    }

    private static let geniusPlaylistName = "Genius, Billionaire, Playboy, Philanthropist"

    private func handlePlayGeniusPlaylist() async {
        await respond("As if there were ever a doubt Mr. Fierro")
        do {
            try await musicPlayerController.shufflePlaylist(named: Self.geniusPlaylistName)
        } catch MusicPlayerError.permissionDenied {
            await respond("I need permission to access your Apple Music library, sir.")
        } catch MusicPlayerError.playlistNotFound {
            await respond("I couldn't find that playlist in your library, sir.")
        } catch {
            await respond("I couldn't start the music, sir.")
        }
        move(to: .completed)
        stateMachine.reset()
        state = .idle
    }

    private static let gestureInstructions =
        "Hold up one finger to move the cursor, like a trackpad. Bring your index and middle finger together and move up or down to scroll. Make a peace sign to click. Show both hands and move them apart or together to zoom."

    private func move(to newState: AssistantState) {
        do {
            try stateMachine.transition(to: newState)
            state = newState
        } catch {
            // An invalid transition is a programming error, not a user-facing
            // one; fail safe to idle rather than leaving stale UI state.
            stateMachine.reset()
            state = .idle
        }
    }

    /// Entry point for push-to-talk / press-to-activate / App Intent flows.
    /// Wake-word activations stay in a continuous back-and-forth — each
    /// completed turn immediately starts listening for the next one — until
    /// the user says something like "thank you, sir" (`.endConversation`) or
    /// cancels. Other activation modes (typed demo input, App Intents) are
    /// always exactly one turn, matching their previous behavior.
    public func activate(mode: ActivationMode, spokenText: String? = nil) async {
        var shouldContinueListening = await performTurn(mode: mode, spokenText: spokenText, isConversationContinuation: false)
        while shouldContinueListening {
            shouldContinueListening = await performTurn(mode: .foregroundWakeWord, spokenText: nil, isConversationContinuation: true)
        }
    }

    /// Runs exactly one listen-decide-respond turn. Returns whether the
    /// caller should immediately listen again (continuous conversation) —
    /// true only for a normally-completed wake-word turn; false for
    /// non-wake-word modes, `.cancel`, and `.endConversation`.
    private func performTurn(mode: ActivationMode, spokenText: String?, isConversationContinuation: Bool) async -> Bool {
        move(to: .activating)
        move(to: .listening(mode))

        let transcript: String
        if let spokenText {
            // Text-injected path (App Intent, Shortcuts, typed demo input) skips STT.
            transcript = spokenText
        } else {
            if mode == .foregroundWakeWord && !isConversationContinuation {
                // Wake word has no physical tap to confirm activation, and
                // the user is typically wearing the glasses rather than
                // looking at the phone — a spoken cue is the only reliable
                // "I'm listening now" signal, and doubles as the moment to
                // say a cancel phrase if it triggered by accident. Skipped
                // on continuation turns — repeating it every follow-up in a
                // conversation would feel unnatural.
                NSLog("[JarvisCoordinator] wake word activated, about to speak acknowledgement cue")
                await textToSpeech.speak("Yes, sir?", settings: voiceSettings)
                NSLog("[JarvisCoordinator] acknowledgement cue done, starting collectTranscript()")
            }
            transcript = await collectTranscript()
        }
        partialTranscript = transcript

        guard !transcript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            // Speech recognition failing (or hearing nothing) used to still
            // count as a "completed" turn, which in continuous-conversation
            // mode meant immediately listening again — if the recognizer
            // keeps failing instantly (as kAFAssistantErrorDomain 1101 can),
            // that's an infinite tight loop hammering
            // com.apple.speech.localspeechrecognition with no way out
            // except the user happening to say an end-conversation phrase.
            // Ending the conversation here instead breaks that loop.
            NSLog("[JarvisCoordinator] empty transcript — ending conversation instead of looping")
            if mode == .foregroundWakeWord {
                await respond("I didn't catch that, sir.")
            }
            move(to: .completed)
            stateMachine.reset()
            state = .idle
            return false
        }

        move(to: .transcribing)
        move(to: .deciding)

        let intent = intentRouter.route(transcript)

        switch intent.intent {
        case .confirm, .reject:
            if awaitingBrowseDirectionsAnswer {
                awaitingBrowseDirectionsAnswer = false
                await resolveBrowseDirectionsAnswer(userSaidYes: intent.intent == .confirm)
                return mode == .foregroundWakeWord
            }
            await resolveConfirmation(userSaidYes: intent.intent == .confirm)
            return mode == .foregroundWakeWord
        case .cancel:
            await respond("Okay, sir.")
            move(to: .completed)
            stateMachine.reset()
            state = .idle
            return false
        case .endConversation:
            await respond("You're welcome, sir.")
            move(to: .completed)
            stateMachine.reset()
            state = .idle
            return false
        case .releaseCamera:
            // The DAT SDK has no way to release just the camera capability
            // (only add one) — the capture indicator LED reflects the
            // whole device session being provisioned, not just active
            // streaming. Fully disconnecting is the only way to turn it
            // off; voice/wake-word keep working afterward since those
            // route through plain Bluetooth audio, not this session — only
            // vision needs a fresh connect before it works again.
            await handGestureController.stop()
            await disconnectWearable()
            await respond("Camera released, sir. Say \"connect the camera\" before I can look at anything again.")
            move(to: .completed)
            stateMachine.reset()
            state = .idle
            return mode == .foregroundWakeWord
        case .reconnectCamera:
            if case .connected = wearableConnectionState {
                // Calling connect() again while already connected isn't a
                // no-op — the SDK throws sessionAlreadyExists, which
                // connect() surfaces as a generic connection *error*,
                // overwriting a perfectly good connection state with a
                // misleading one. Skip the redundant attempt entirely.
                await respond("Already connected, sir.")
            } else {
                await connectWearable()
                if case .connected = wearableConnectionState {
                    await respond("Camera connected, sir.")
                } else {
                    await respond("I couldn't connect to the glasses, sir.")
                }
            }
            move(to: .completed)
            stateMachine.reset()
            state = .idle
            return mode == .foregroundWakeWord
        case .wakeUpCheck:
            // Spoken reply comes first so it feels immediate — connecting
            // can take a few seconds (the glasses' own Bluetooth-readiness
            // retry loop), and there's no reason to make "For you, sir.
            // Always." wait on that.
            await respond("For you, sir. Always.")
            if case .connected = wearableConnectionState {
                // Same reasoning as .reconnectCamera above — already
                // connected, so don't trigger a redundant connect() that
                // would just fail with sessionAlreadyExists and clobber a
                // fine connection state with an error.
            } else {
                await connectWearable()
            }
            // Deliberately does NOT also start the camera/video stream here
            // to light the LED — that was tried and reverted. startVideoStream()
            // calls stream.start() without waiting for .streaming, so a
            // later "Look" request finds the camera mid-transition instead
            // of a clean .stopped/.streaming state, which is exactly the
            // class of camera-state race that has broken vision analysis
            // repeatedly in this codebase (see attemptCapture()'s own
            // comments). Vision analysis matters more than the LED lighting
            // up on a voice greeting — the LED still turns on the first
            // time the user actually asks Jarvis to look at something.
            move(to: .completed)
            stateMachine.reset()
            state = .idle
            return mode == .foregroundWakeWord
        case .shutDown:
            await handGestureController.stop()
            await disconnectWearable()
            await respond("Shutting down, sir.")
            move(to: .completed)
            stateMachine.reset()
            state = .idle
            return false
        case .playGeniusPlaylist:
            // Unlike other commands, this deliberately ends the conversation
            // instead of continuing to listen — once the music starts, the
            // mic goes back to resting until "Jarvis" wakes it again, rather
            // than staying engaged for a follow-up the way other commands do.
            await handlePlayGeniusPlaylist()
            return false
        case .startBrowseMode:
            await handleStartBrowseMode()
            return mode == .foregroundWakeWord
        case .stopBrowseMode:
            await handleStopBrowseMode()
            return mode == .foregroundWakeWord
        default:
            await handle(intent: intent, transcript: transcript)
            return mode == .foregroundWakeWord
        }
    }

    private func collectTranscript() async -> String {
        var finalText = ""
        do {
            for try await result in speechToText.startTranscribing(maxDuration: 12) {
                partialTranscript = result.text
                if result.isFinal { finalText = result.text }
            }
        } catch {
            move(to: .failed(UserFacingError(message: "I couldn't hear that clearly, sir.")))
        }
        return finalText
    }

    private func handle(intent: IntentResult, transcript: String) async {
        var visualDescription: String?

        if intent.requiresVisualContext {
            if case .active = gestureControlState {
                // capturePhoto() and continuous gesture streaming both own
                // the glasses camera — letting one interrupt the other
                // silently is exactly the class of camera-state collision
                // that has broken vision analysis before. Refuse instead of
                // guessing which one should win.
                await respond("I'm still browsing, sir. Say \"stop browsing\" first.")
                move(to: .completed)
                stateMachine.reset()
                state = .idle
                return
            }
            move(to: .capturingVisualContext)
            do {
                NSLog("[JarvisVisual] checking camera permission")
                let permission = try await wearableClient.checkPermissionStatus(.camera)
                NSLog("[JarvisVisual] permission status: \(permission)")
                if permission != .granted {
                    let requested = try await wearableClient.requestPermission(.camera)
                    NSLog("[JarvisVisual] requested permission, got: \(requested)")
                    guard requested == .granted else {
                        await respond("I need camera permission for your glasses first, sir. You can grant it from the Meta AI app.")
                        move(to: .completed)
                        stateMachine.reset()
                        state = .idle
                        return
                    }
                }
                NSLog("[JarvisVisual] calling capturePhoto()")
                let image = try await wearableClient.capturePhoto()
                NSLog("[JarvisVisual] capturePhoto() succeeded, \(image.data.count) bytes; calling visionProvider.analyze()")
                let question = intent.parameters["question"] ?? transcript
                let analysis = try await visionProvider.analyze(image: image, question: question)
                NSLog("[JarvisVisual] analyze() succeeded: \(analysis.answer)")
                visualDescription = analysis.answer
                await respond(analysis.answer)

                if intent.intent == .rememberScene {
                    await saveScene(question: question, analysis: analysis)
                }
                move(to: .completed)
                stateMachine.reset()
                state = .idle
                return
            } catch {
                NSLog("[JarvisVisual] threw: \(error)")
                move(to: .failed(.visionUnavailable))
                await respond(UserFacingError.visionUnavailable.message)
                stateMachine.reset()
                state = .idle
                return
            }
        }

        if intent.intent == .searchMemory {
            move(to: .reasoning)
            do {
                let results = try await memoryRepository.search(MemoryQuery(text: intent.parameters["question"] ?? transcript))
                let answer = summarize(searchResults: results)
                await respond(answer)
            } catch {
                await respond("I couldn't search memory right now, sir.")
            }
            move(to: .completed)
            stateMachine.reset()
            state = .idle
            return
        }

        // Everything else (or anything low-confidence) goes to the language
        // reasoning provider, which may propose a tool call that policy must
        // separately authorize.
        move(to: .reasoning)
        let context = contextAssembler.assemble(for: intent, transcript: transcript, visualDescription: visualDescription)
        do {
            let availableTools = policyEngine.availableToolNames(grantedPermissions: grantedPermissions)
            let response = try await languageProvider.reason(ReasoningRequest(context: context, question: transcript, availableTools: availableTools))
            if let proposedCall = response.proposedToolCall {
                await authorizeAndMaybeExecute(proposedCall, spokenAnswer: response.spokenAnswer)
            } else {
                await respond(response.spokenAnswer)
                move(to: .completed)
                stateMachine.reset()
                state = .idle
            }
        } catch {
            move(to: .failed(UserFacingError(message: "I could not complete that right now.")))
            await respond("I could not complete that right now, sir.")
            stateMachine.reset()
            state = .idle
        }
    }

    private func authorizeAndMaybeExecute(_ call: ToolCall, spokenAnswer: String) async {
        let decision = policyEngine.evaluate(call, grantedPermissions: grantedPermissions)
        switch decision {
        case .allow:
            move(to: .executing(ActionDescriptor(toolName: call.toolName, summary: spokenAnswer)))
            let result = (try? await toolExecutor.execute(call)) ?? ToolResult(success: false, output: "execution failed")
            if result.success {
                // For informational tools (get_current_time, etc.), the
                // reasoning step's spokenAnswer is only ever a pre-execution
                // acknowledgment ("Checking the time now.") — Claude can't
                // know the actual answer before the tool runs. Speaking only
                // that (as this used to do unconditionally) meant read-only
                // tools never actually told the user what they asked for.
                let isReadOnly = policyEngine.riskLevel(for: call.toolName) == .readOnly
                await respond(isReadOnly ? result.output : spokenAnswer)
            } else {
                await respond("I could not complete that safely, sir.")
            }
            move(to: .completed)
            stateMachine.reset()
            state = .idle
        case .requireConfirmation(let pending):
            await confirmationManager.propose(pending)
            move(to: .awaitingConfirmation(pending))
            await respond("\(pending.summary), sir? Say confirm to proceed.")
            move(to: .completed)
            stateMachine.reset()
            state = .idle
        case .deny(let reason):
            await respond("I can't do that, sir: \(reason)")
            move(to: .completed)
            stateMachine.reset()
            state = .idle
        }
    }

    private func resolveConfirmation(userSaidYes: Bool) async {
        guard let outcome = await confirmationManager.resolve(userSaidYes: userSaidYes) else {
            await respond("There's nothing pending to confirm, sir.")
            stateMachine.reset()
            state = .idle
            return
        }
        switch outcome {
        case .confirmed(let action):
            move(to: .executing(ActionDescriptor(toolName: action.toolName, summary: action.summary)))
            let result = (try? await toolExecutor.execute(ToolCall(toolName: action.toolName, target: action.target))) ?? ToolResult(success: false, output: "failed")
            await respond(result.success ? "Done." : "I could not complete that safely.")
        case .rejected:
            await respond("Okay, cancelled, sir.")
        case .expired:
            await respond("That confirmation expired, sir. Ask again if you'd still like to proceed.")
        }
        move(to: .completed)
        stateMachine.reset()
        state = .idle
    }

    private func saveScene(question: String, analysis: VisionAnalysisResult) async {
        let record = MemoryRecord(
            type: .objectLocation,
            title: question,
            originalUserText: question,
            normalizedSummary: analysis.answer,
            extractedText: analysis.detectedText,
            tags: []
        )
        try? await memoryRepository.save(record)
    }

    private func summarize(searchResults: [MemorySearchResult]) -> String {
        guard let best = searchResults.first else {
            return "I don't have a memory matching that."
        }
        let formatter = RelativeDateTimeFormatter()
        let when = formatter.localizedString(for: best.record.timestamp, relativeTo: Date())
        let location = best.record.coarseLocationName.map { " near \($0)" } ?? ""
        return "Last recorded \(when)\(location): \(best.record.normalizedSummary)"
    }

    private func respond(_ text: String) async {
        lastResponse = text
        move(to: .speaking)
        await textToSpeech.speak(text, settings: voiceSettings)
    }

    public func stopEverything() async {
        speechToText.cancel()
        textToSpeech.stopSpeaking()
        await handGestureController.stop()
        await wearableClient.stopVideoStream()
        await confirmationManager.clear()
        stateMachine.reset()
        state = .idle
        partialTranscript = ""
    }
}
