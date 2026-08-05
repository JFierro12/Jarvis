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
}
