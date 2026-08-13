import XCTest
@testable import JarvisKit

@MainActor
final class AssistantCoordinatorTests: XCTestCase {
    func testVisualAnalysisFlowEndToEnd() async {
        let stt = MockSpeechToTextProvider(scriptedTranscript: "what am I looking at")
        let tts = MockTextToSpeechProvider()
        let vision = MockVisionReasoningProvider()
        let memory = InMemoryMemoryRepository()
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(demoImageData: Data([0xFF, 0xD8])),
            speechToText: stt,
            textToSpeech: tts,
            visionProvider: vision,
            languageProvider: MockLanguageReasoningProvider(),
            memoryRepository: memory,
            toolExecutor: MockToolExecutor(memoryRepository: memory)
        )

        await coordinator.activate(mode: .pressToActivateSession, spokenText: "what am I looking at")

        XCTAssertEqual(coordinator.state, .idle)
        XCTAssertEqual(tts.spokenUtterances.last, MockVisionReasoningProvider.defaultResult.answer)
    }

    func testRememberSceneThenSearchThenDelete() async {
        let vision = MockVisionReasoningProvider(scriptedResult: VisionAnalysisResult(answer: "Your keys are on the kitchen counter.", confidence: 0.9))
        let memory = InMemoryMemoryRepository()
        let tts = MockTextToSpeechProvider()
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(demoImageData: Data([0xFF, 0xD8])),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts,
            visionProvider: vision,
            languageProvider: MockLanguageReasoningProvider(),
            memoryRepository: memory,
            toolExecutor: MockToolExecutor(memoryRepository: memory)
        )

        await coordinator.activate(mode: .pushToTalk, spokenText: "remember where I put my keys")
        var stored = try? await memory.all()
        XCTAssertEqual(stored?.count, 1)

        await coordinator.activate(mode: .pushToTalk, spokenText: "where did I last see my keys")
        XCTAssertTrue(tts.spokenUtterances.last?.contains("kitchen counter") ?? false)

        guard let recordId = stored?.first?.id else { return XCTFail("expected a saved memory") }
        try? await memory.delete(id: recordId)
        stored = try? await memory.all()
        XCTAssertEqual(stored?.count, 0)
    }

    func testDestructiveToolRequiresConfirmationBeforeExecuting() async {
        let memory = InMemoryMemoryRepository()
        try? await memory.save(MemoryRecord(type: .note, title: "test", originalUserText: "test", normalizedSummary: "test"))
        let tts = MockTextToSpeechProvider()
        let language = MockLanguageReasoningProvider(
            scriptedResponse: ReasoningResponse(spokenAnswer: "Delete all memories?", proposedToolCall: ToolCall(toolName: "delete_all_memories"), requiresConfirmation: true)
        )
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts,
            visionProvider: MockVisionReasoningProvider(),
            languageProvider: language,
            memoryRepository: memory,
            toolExecutor: MockToolExecutor(memoryRepository: memory)
        )

        await coordinator.activate(mode: .pushToTalk, spokenText: "delete all my memories")
        // Confirmation requested, but nothing deleted yet.
        var stored = try? await memory.all()
        XCTAssertEqual(stored?.count, 1)

        await coordinator.activate(mode: .pushToTalk, spokenText: "confirm")
        stored = try? await memory.all()
        XCTAssertEqual(stored?.count, 0)
    }

    func testStopEverythingReturnsToIdle() async {
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: MockTextToSpeechProvider(),
            visionProvider: MockVisionReasoningProvider(),
            languageProvider: MockLanguageReasoningProvider(),
            memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: MockToolExecutor(memoryRepository: InMemoryMemoryRepository())
        )
        await coordinator.stopEverything()
        XCTAssertEqual(coordinator.state, .idle)
    }

    func testIdentifyCoverageSendsExplicitCoverageQuestionThroughSameCapturePath() async {
        let tts = MockTextToSpeechProvider()
        let vision = MockVisionReasoningProvider(scriptedResult: VisionAnalysisResult(
            answer: "Two-high shell, Cover 4 quarters to the field...",
            confidence: 0.75
        ))
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(demoImageData: Data([0xFF, 0xD8])),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts,
            visionProvider: vision,
            languageProvider: MockLanguageReasoningProvider(),
            memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: MockToolExecutor(memoryRepository: InMemoryMemoryRepository())
        )

        await coordinator.activate(mode: .pushToTalk, spokenText: "identify the coverage")

        XCTAssertEqual(vision.analyzeCallCount, 1, "must go through the same capturePhoto()/analyze() path as every other visual-context intent")
        XCTAssertEqual(vision.lastQuestion, "Identify the defensive coverage shown in this football play and give a full offensive-coordinator-level breakdown: personnel and formation, the coverage shell with your reasoning for it, why the defense is likely in this look, and the quarterback's progression against it.")
        XCTAssertEqual(tts.spokenUtterances.last, "Two-high shell, Cover 4 quarters to the field...")
    }

    // MARK: - Football analysis mode

    func testFootballModeEntryPrompt() async {
        let tts = MockTextToSpeechProvider()
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts,
            visionProvider: MockVisionReasoningProvider(),
            languageProvider: MockLanguageReasoningProvider(),
            memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: MockToolExecutor(memoryRepository: InMemoryMemoryRepository())
        )

        await coordinator.activate(mode: .pushToTalk, spokenText: "football mode")

        XCTAssertEqual(tts.spokenUtterances.last, "Football mode, sir. Give me the read.")
    }

    func testFootballModeInterceptsShorthandWithoutNeedingACommandPhrase() async {
        let tts = MockTextToSpeechProvider()
        let language = MockLanguageReasoningProvider(scriptedResponse: ReasoningResponse(spokenAnswer: "Two-high shell, quarters look..."))
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts,
            visionProvider: MockVisionReasoningProvider(),
            languageProvider: language,
            memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: MockToolExecutor(memoryRepository: InMemoryMemoryRepository())
        )

        await coordinator.activate(mode: .pushToTalk, spokenText: "football mode")
        await coordinator.activate(mode: .pushToTalk, spokenText: "2 high, Left Corner 5, Right Corner 7, 3 in the box 4 up front")

        XCTAssertEqual(
            language.lastRequest?.question,
            "Coach's shorthand pre-snap read: \"2 high, Left Corner 5, Right Corner 7, 3 in the box 4 up front\". Give the coverage read and how the offense should attack it — as fast and dense as possible, this needs to land before the snap."
        )
        XCTAssertEqual(tts.spokenUtterances.last, "Two-high shell, quarters look...")
    }

    func testFootballModeStopExitsAndStopsIntercepting() async {
        let tts = MockTextToSpeechProvider()
        let language = MockLanguageReasoningProvider()
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts,
            visionProvider: MockVisionReasoningProvider(),
            languageProvider: language,
            memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: MockToolExecutor(memoryRepository: InMemoryMemoryRepository())
        )

        await coordinator.activate(mode: .pushToTalk, spokenText: "football mode")
        await coordinator.activate(mode: .pushToTalk, spokenText: "stop football mode")

        XCTAssertEqual(tts.spokenUtterances.last, "Out of football mode, sir.")

        // No longer active — an ordinary question should route normally,
        // not get wrapped as a shorthand read.
        await coordinator.activate(mode: .pushToTalk, spokenText: "what time is it")
        XCTAssertEqual(language.lastRequest?.question, "what time is it")
    }

    func testFootballModeEscapeHatchCancelStillWorksWhileActive() async {
        let tts = MockTextToSpeechProvider()
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts,
            visionProvider: MockVisionReasoningProvider(),
            languageProvider: MockLanguageReasoningProvider(),
            memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: MockToolExecutor(memoryRepository: InMemoryMemoryRepository())
        )

        await coordinator.activate(mode: .pushToTalk, spokenText: "football mode")
        await coordinator.activate(mode: .pushToTalk, spokenText: "cancel")

        XCTAssertEqual(tts.spokenUtterances.last, "Okay, sir.", "cancel must still route normally instead of being swallowed as a shorthand read")
    }

    func testFootballModePersistsAcrossEndConversationButNotShutDown() async {
        let tts = MockTextToSpeechProvider()
        let language = MockLanguageReasoningProvider()
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts,
            visionProvider: MockVisionReasoningProvider(),
            languageProvider: language,
            memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: MockToolExecutor(memoryRepository: InMemoryMemoryRepository())
        )

        await coordinator.activate(mode: .pushToTalk, spokenText: "football mode")
        await coordinator.activate(mode: .pushToTalk, spokenText: "thank you jarvis")

        // Still active after a normal end-of-conversation — a shorthand
        // read should still get intercepted without re-entering the mode.
        await coordinator.activate(mode: .pushToTalk, spokenText: "1 high, nickel")
        XCTAssertTrue(language.lastRequest?.question.hasPrefix("Coach's shorthand pre-snap read:") ?? false)

        await coordinator.activate(mode: .pushToTalk, spokenText: "shut down")

        // Cleared by shut down — the same question now routes as an
        // ordinary open-ended question, not a shorthand read.
        await coordinator.activate(mode: .pushToTalk, spokenText: "1 high, nickel")
        XCTAssertEqual(language.lastRequest?.question, "1 high, nickel")
    }

    // MARK: - Browse mode (hand-gesture control)

    func testBrowseOffersDirections() async {
        let tts = MockTextToSpeechProvider()
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts,
            visionProvider: MockVisionReasoningProvider(),
            languageProvider: MockLanguageReasoningProvider(),
            memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: MockToolExecutor(memoryRepository: InMemoryMemoryRepository())
        )

        await coordinator.activate(mode: .pushToTalk, spokenText: "browse")

        XCTAssertEqual(tts.spokenUtterances.last, "Browsing sir, would you like directions on how to do so sir?")
    }

    func testBrowseYesSpeaksInstructionsThenStartsGestureControl() async {
        let tts = MockTextToSpeechProvider()
        let gestureController = MockHandGestureController()
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts,
            visionProvider: MockVisionReasoningProvider(),
            languageProvider: MockLanguageReasoningProvider(),
            memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: MockToolExecutor(memoryRepository: InMemoryMemoryRepository()),
            handGestureController: gestureController
        )

        await coordinator.activate(mode: .pushToTalk, spokenText: "browse")
        await coordinator.activate(mode: .pushToTalk, spokenText: "yes")

        XCTAssertEqual(tts.spokenUtterances.count, 3)
        XCTAssertTrue(tts.spokenUtterances[1].contains("Hold up one finger"))
        XCTAssertEqual(tts.spokenUtterances.last, "Enjoy your browse, sir.")
        XCTAssertEqual(gestureController.startCallCount, 1)
        XCTAssertEqual(coordinator.gestureControlState, .active)
    }

    func testBrowseNoSkipsInstructionsButStillStarts() async {
        let tts = MockTextToSpeechProvider()
        let gestureController = MockHandGestureController()
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts,
            visionProvider: MockVisionReasoningProvider(),
            languageProvider: MockLanguageReasoningProvider(),
            memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: MockToolExecutor(memoryRepository: InMemoryMemoryRepository()),
            handGestureController: gestureController
        )

        await coordinator.activate(mode: .pushToTalk, spokenText: "browse")
        await coordinator.activate(mode: .pushToTalk, spokenText: "no")

        XCTAssertEqual(tts.spokenUtterances.count, 2)
        XCTAssertEqual(tts.spokenUtterances.last, "Enjoy your browse, sir.")
        XCTAssertFalse(tts.spokenUtterances.contains { $0.contains("Hold up one finger") })
        XCTAssertEqual(gestureController.startCallCount, 1)
    }

    func testStopBrowsingWhileActive() async {
        let tts = MockTextToSpeechProvider()
        let gestureController = MockHandGestureController()
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts,
            visionProvider: MockVisionReasoningProvider(),
            languageProvider: MockLanguageReasoningProvider(),
            memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: MockToolExecutor(memoryRepository: InMemoryMemoryRepository()),
            handGestureController: gestureController
        )

        await coordinator.activate(mode: .pushToTalk, spokenText: "browse")
        await coordinator.activate(mode: .pushToTalk, spokenText: "yes")
        await coordinator.activate(mode: .pushToTalk, spokenText: "stop browsing")

        XCTAssertEqual(tts.spokenUtterances.last, "Stopped browsing, sir.")
        XCTAssertEqual(gestureController.stopCallCount, 1)
        XCTAssertEqual(coordinator.gestureControlState, .inactive)
    }

    func testStopBrowsingWhileInactive() async {
        let tts = MockTextToSpeechProvider()
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts,
            visionProvider: MockVisionReasoningProvider(),
            languageProvider: MockLanguageReasoningProvider(),
            memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: MockToolExecutor(memoryRepository: InMemoryMemoryRepository())
        )

        await coordinator.activate(mode: .pushToTalk, spokenText: "stop browsing")

        XCTAssertEqual(tts.spokenUtterances.last, "I wasn't browsing, sir.")
    }

    func testCannotLookWhileBrowsingIsActive() async {
        let tts = MockTextToSpeechProvider()
        let vision = MockVisionReasoningProvider()
        let gestureController = MockHandGestureController()
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(demoImageData: Data([0xFF, 0xD8])),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts,
            visionProvider: vision,
            languageProvider: MockLanguageReasoningProvider(),
            memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: MockToolExecutor(memoryRepository: InMemoryMemoryRepository()),
            handGestureController: gestureController
        )

        await coordinator.activate(mode: .pushToTalk, spokenText: "browse")
        await coordinator.activate(mode: .pushToTalk, spokenText: "yes")
        await coordinator.activate(mode: .pushToTalk, spokenText: "what am I looking at")

        XCTAssertEqual(tts.spokenUtterances.last, "I'm still browsing, sir. Say \"stop browsing\" first.")
        XCTAssertEqual(vision.analyzeCallCount, 0, "capturePhoto()/analyze() must never run while gesture control owns the camera")
    }

    // MARK: - "Genius in the room" playlist trigger

    func testGeniusPlaylistSpeaksLineAndShufflesPlaylist() async {
        let tts = MockTextToSpeechProvider()
        let music = MockMusicPlayerController()
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts,
            visionProvider: MockVisionReasoningProvider(),
            languageProvider: MockLanguageReasoningProvider(),
            memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: MockToolExecutor(memoryRepository: InMemoryMemoryRepository()),
            musicPlayerController: music
        )

        await coordinator.activate(mode: .pushToTalk, spokenText: "Let's remind everyone who the genius in the room is")

        XCTAssertEqual(tts.spokenUtterances, ["As if there were ever a doubt Mr. Fierro"])
        XCTAssertEqual(music.shuffledPlaylistNames, ["Genius, Billionaire, Playboy, Philanthropist"])
    }

    func testGeniusPlaylistSurfacesPermissionDeniedError() async {
        let tts = MockTextToSpeechProvider()
        let music = MockMusicPlayerController(scriptedError: .permissionDenied)
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts,
            visionProvider: MockVisionReasoningProvider(),
            languageProvider: MockLanguageReasoningProvider(),
            memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: MockToolExecutor(memoryRepository: InMemoryMemoryRepository()),
            musicPlayerController: music
        )

        await coordinator.activate(mode: .pushToTalk, spokenText: "genius in the room")

        XCTAssertEqual(tts.spokenUtterances.last, "I need permission to access your Apple Music library, sir.")
    }

    func testGeniusPlaylistEndsConversationEvenInWakeWordMode() async {
        let tts = MockTextToSpeechProvider()
        let music = MockMusicPlayerController()
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(),
            speechToText: MockSpeechToTextProvider(scriptedTranscript: "genius in the room"),
            textToSpeech: tts,
            visionProvider: MockVisionReasoningProvider(),
            languageProvider: MockLanguageReasoningProvider(),
            memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: MockToolExecutor(memoryRepository: InMemoryMemoryRepository()),
            musicPlayerController: music
        )

        // Unlike other wake-word commands, this must NOT loop back into
        // another collectTranscript() turn — the mic should rest until
        // "Jarvis" wakes it again, not stay engaged for a follow-up.
        await coordinator.activate(mode: .foregroundWakeWord, spokenText: "genius in the room")

        XCTAssertEqual(tts.spokenUtterances, ["As if there were ever a doubt Mr. Fierro"])
        XCTAssertEqual(music.shuffledPlaylistNames, ["Genius, Billionaire, Playboy, Philanthropist"])
        XCTAssertEqual(coordinator.state, .idle)
    }

    func testGeniusPlaylistSurfacesPlaylistNotFoundError() async {
        let tts = MockTextToSpeechProvider()
        let music = MockMusicPlayerController(scriptedError: .playlistNotFound("Genius, Billionaire, Playboy, Philanthropist"))
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts,
            visionProvider: MockVisionReasoningProvider(),
            languageProvider: MockLanguageReasoningProvider(),
            memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: MockToolExecutor(memoryRepository: InMemoryMemoryRepository()),
            musicPlayerController: music
        )

        await coordinator.activate(mode: .pushToTalk, spokenText: "genius in the room")

        XCTAssertEqual(tts.spokenUtterances.last, "I couldn't find that playlist in your library, sir.")
    }
}
