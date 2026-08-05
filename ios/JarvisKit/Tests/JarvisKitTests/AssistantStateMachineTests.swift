import XCTest
@testable import JarvisKit

final class AssistantStateMachineTests: XCTestCase {
    func testIdleToActivatingToListening() throws {
        let machine = AssistantStateMachine()
        try machine.transition(to: .activating)
        try machine.transition(to: .listening(.pushToTalk))
        XCTAssertEqual(machine.state, .listening(.pushToTalk))
    }

    func testCannotStartOverlappingListeningSessions() {
        let machine = AssistantStateMachine()
        try? machine.transition(to: .activating)
        try? machine.transition(to: .listening(.pushToTalk))
        XCTAssertThrowsError(try machine.transition(to: .listening(.pressToActivateSession)))
    }

    func testCannotExecuteWithoutGoingThroughConfirmationOrReasoning() {
        let machine = AssistantStateMachine()
        // idle -> executing is not a legal transition.
        XCTAssertThrowsError(try machine.transition(to: .executing(ActionDescriptor(toolName: "shutdown_pc", summary: "Shut down"))))
    }

    func testAnyStateCanFail() throws {
        let machine = AssistantStateMachine()
        try machine.transition(to: .activating)
        try machine.transition(to: .failed(.glassesDisconnected))
        XCTAssertEqual(machine.state, .failed(.glassesDisconnected))
    }

    func testFullHappyPathVisualAnalysis() throws {
        let machine = AssistantStateMachine()
        try machine.transition(to: .activating)
        try machine.transition(to: .listening(.pressToActivateSession))
        try machine.transition(to: .transcribing)
        try machine.transition(to: .deciding)
        try machine.transition(to: .capturingVisualContext)
        try machine.transition(to: .reasoning)
        try machine.transition(to: .speaking)
        try machine.transition(to: .completed)
        try machine.transition(to: .idle)
        XCTAssertEqual(machine.state, .idle)
    }

    func testConfirmedActionFlow() throws {
        let machine = AssistantStateMachine()
        try machine.transition(to: .activating)
        try machine.transition(to: .listening(.pushToTalk))
        try machine.transition(to: .transcribing)
        try machine.transition(to: .deciding)
        // Resolved confirmation goes straight from deciding to executing.
        try machine.transition(to: .executing(ActionDescriptor(toolName: "shutdown_pc", summary: "Shut down Gaming-PC")))
        try machine.transition(to: .speaking)
        try machine.transition(to: .completed)
        XCTAssertEqual(machine.state, .completed)
    }

    func testResetReturnsToIdle() throws {
        let machine = AssistantStateMachine()
        try machine.transition(to: .activating)
        machine.reset()
        XCTAssertEqual(machine.state, .idle)
    }
}
