import XCTest
@testable import JarvisKit

final class ConfirmationManagerTests: XCTestCase {
    func testConfirmedResolvesToConfirmed() async {
        let manager = ConfirmationManager()
        let action = PendingAction(toolName: "shutdown_pc", summary: "Shut down Gaming-PC", target: "Gaming-PC", riskLevel: .sensitiveWrite, expiresAt: Date().addingTimeInterval(20))
        await manager.propose(action)

        let outcome = await manager.resolve(userSaidYes: true)
        XCTAssertEqual(outcome, .confirmed(action))
    }

    func testRejectedResolvesToRejected() async {
        let manager = ConfirmationManager()
        let action = PendingAction(toolName: "delete_all_memories", summary: "Delete all memories", target: "", riskLevel: .destructive, expiresAt: Date().addingTimeInterval(20))
        await manager.propose(action)

        let outcome = await manager.resolve(userSaidYes: false)
        XCTAssertEqual(outcome, .rejected(action))
    }

    func testExpiredConfirmationCannotBeConfirmed() async {
        let manager = ConfirmationManager()
        let action = PendingAction(toolName: "shutdown_pc", summary: "Shut down", target: "", riskLevel: .sensitiveWrite, expiresAt: Date().addingTimeInterval(-1))
        await manager.propose(action)

        let outcome = await manager.resolve(userSaidYes: true)
        XCTAssertEqual(outcome, .expired(action))
    }

    func testResolvingWithoutAPendingActionReturnsNil() async {
        let manager = ConfirmationManager()
        let outcome = await manager.resolve(userSaidYes: true)
        XCTAssertNil(outcome)
    }

    func testResolvingConsumesThePendingAction() async {
        let manager = ConfirmationManager()
        let action = PendingAction(toolName: "shutdown_pc", summary: "Shut down", target: "", riskLevel: .sensitiveWrite, expiresAt: Date().addingTimeInterval(20))
        await manager.propose(action)
        _ = await manager.resolve(userSaidYes: true)

        // A second confirm without a fresh proposal must not re-execute.
        let secondOutcome = await manager.resolve(userSaidYes: true)
        XCTAssertNil(secondOutcome)
    }
}
