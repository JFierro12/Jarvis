import XCTest
@testable import JarvisKit

final class ContextAssemblerTests: XCTestCase {
    let assembler = ContextAssembler()

    func testVisualQuestionDoesNotIncludeCalendar() {
        let intent = IntentResult(intent: .analyzeScene, confidence: 0.9, requiresVisualContext: true)
        let context = assembler.assemble(for: intent, transcript: "what am I looking at", visualDescription: "a laptop", calendarSummary: "Standup at 9am")
        XCTAssertFalse(context.items.contains { $0.source == .toolResult && $0.label == "calendar" })
        XCTAssertTrue(context.items.contains { $0.source == .cameraObservation })
    }

    func testCalendarQuestionDoesNotIncludeCameraObservation() {
        let intent = IntentResult(intent: .getCalendar, confidence: 0.9)
        let context = assembler.assemble(for: intent, transcript: "what's next", visualDescription: "a laptop", calendarSummary: "Standup at 9am")
        XCTAssertFalse(context.items.contains { $0.source == .cameraObservation })
        XCTAssertTrue(context.items.contains { $0.source == .toolResult && $0.label == "calendar" })
    }

    func testSystemPolicyIsAlwaysIncluded() {
        let intent = IntentResult(intent: .help, confidence: 0.9)
        let context = assembler.assemble(for: intent, transcript: "help")
        XCTAssertTrue(context.items.contains { $0.source == .systemPolicy })
    }

    func testCameraObservationCannotAuthorizeActions() {
        XCTAssertFalse(ContextSource.cameraObservation.canAuthorizeActions)
        XCTAssertFalse(ContextSource.externalContent.canAuthorizeActions)
        XCTAssertTrue(ContextSource.userRequest.canAuthorizeActions)
        XCTAssertTrue(ContextSource.systemPolicy.canAuthorizeActions)
    }
}
