import XCTest
@testable import JarvisKit

final class SharedRuntimeContractTests: XCTestCase {
    func testCanonicalResponseFixture() throws {
        let url = try XCTUnwrap(Bundle.module.url(forResource: "turn-response", withExtension: "json", subdirectory: "Fixtures"))
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let response = try decoder.decode(SharedTurn.self, from: Data(contentsOf: url))
        XCTAssertEqual(response.contractVersion, "2")
        XCTAssertEqual(response.actionId, "action-1")
        XCTAssertEqual(response.decision, "require_confirmation")
    }
}

private actor FakeSharedRuntime: SharedAssistantRuntime {
    var approvals = 0
    var executions = 0
    var cancellations = 0

    func turn(question: String) async throws -> SharedTurn {
        SharedTurn(contractVersion: "2", conversationId: "one", turnId: "one", spokenAnswer: "Approve?",
                   actionId: "action", decision: "require_confirmation", confirmationSummary: "Delete test memory?",
                   provider: "mock", model: "mock", promptVersion: "test")
    }
    func approve(actionId: String) async throws { approvals += 1 }
    func execute(actionId: String) async throws -> ToolResult {
        executions += 1
        return ToolResult(success: true, output: "Verified result")
    }
    func cancel(actionId: String) async throws { cancellations += 1 }
}

@MainActor
final class SharedRuntimeCoordinatorTests: XCTestCase {
    func testOnlyExplicitConfirmationApprovesRemoteAction() async {
        let runtime = FakeSharedRuntime()
        let tts = MockTextToSpeechProvider()
        let coordinator = AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(), speechToText: MockSpeechToTextProvider(),
            textToSpeech: tts, visionProvider: MockVisionReasoningProvider(),
            languageProvider: MockLanguageReasoningProvider(), memoryRepository: InMemoryMemoryRepository(),
            toolExecutor: UnavailableToolExecutor(), sharedRuntime: runtime)
        await coordinator.activate(mode: .pushToTalk, spokenText: "delete all memories")
        let before = await runtime.executions
        XCTAssertEqual(before, 0)
        await coordinator.activate(mode: .pushToTalk, spokenText: "confirm")
        let approvals = await runtime.approvals
        let executions = await runtime.executions
        XCTAssertEqual(approvals, 1)
        XCTAssertEqual(executions, 1)
        XCTAssertEqual(tts.spokenUtterances.last, "Verified result")
        XCTAssertEqual(coordinator.state, .idle)
    }
}
