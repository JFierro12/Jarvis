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
        voiceSettings: VoiceSettings = .default
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
        self.stateMachine = AssistantStateMachine()
    }

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
    public func activate(mode: ActivationMode, spokenText: String? = nil) async {
        move(to: .activating)
        move(to: .listening(mode))

        let transcript: String
        if let spokenText {
            // Text-injected path (App Intent, Shortcuts, typed demo input) skips STT.
            transcript = spokenText
        } else {
            transcript = await collectTranscript()
        }
        partialTranscript = transcript

        move(to: .transcribing)
        move(to: .deciding)

        let intent = intentRouter.route(transcript)

        switch intent.intent {
        case .confirm, .reject:
            await resolveConfirmation(userSaidYes: intent.intent == .confirm)
            return
        case .cancel:
            stateMachine.reset()
            state = .idle
            return
        default:
            break
        }

        await handle(intent: intent, transcript: transcript)
    }

    private func collectTranscript() async -> String {
        var finalText = ""
        do {
            for try await result in speechToText.startTranscribing(maxDuration: 12) {
                partialTranscript = result.text
                if result.isFinal { finalText = result.text }
            }
        } catch {
            move(to: .failed(UserFacingError(message: "I couldn't hear that clearly.")))
        }
        return finalText
    }

    private func handle(intent: IntentResult, transcript: String) async {
        var visualDescription: String?

        if intent.requiresVisualContext {
            move(to: .capturingVisualContext)
            do {
                let image = try await wearableClient.capturePhoto()
                let question = intent.parameters["question"] ?? transcript
                let analysis = try await visionProvider.analyze(image: image, question: question)
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
                await respond("I couldn't search memory right now.")
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
            let response = try await languageProvider.reason(ReasoningRequest(context: context, question: transcript))
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
            await respond("I could not complete that right now.")
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
            await respond(result.success ? spokenAnswer : "I could not complete that safely.")
            move(to: .completed)
            stateMachine.reset()
            state = .idle
        case .requireConfirmation(let pending):
            await confirmationManager.propose(pending)
            move(to: .awaitingConfirmation(pending))
            await respond("\(pending.summary)? Say confirm to proceed.")
            move(to: .completed)
            stateMachine.reset()
            state = .idle
        case .deny(let reason):
            await respond("I can't do that: \(reason)")
            move(to: .completed)
            stateMachine.reset()
            state = .idle
        }
    }

    private func resolveConfirmation(userSaidYes: Bool) async {
        guard let outcome = await confirmationManager.resolve(userSaidYes: userSaidYes) else {
            await respond("There's nothing pending to confirm.")
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
            await respond("Okay, cancelled.")
        case .expired:
            await respond("That confirmation expired. Ask again if you'd still like to proceed.")
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
        await wearableClient.stopVideoStream()
        await confirmationManager.clear()
        stateMachine.reset()
        state = .idle
        partialTranscript = ""
    }
}
