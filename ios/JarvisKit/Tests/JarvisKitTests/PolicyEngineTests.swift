import XCTest
@testable import JarvisKit

final class PolicyEngineTests: XCTestCase {
    func testReadOnlyToolIsAllowedWithoutConfirmation() {
        let engine = PolicyEngine()
        let decision = engine.evaluate(ToolCall(toolName: "get_current_time"), grantedPermissions: [])
        XCTAssertEqual(decision, .allow)
    }

    func testDestructiveToolRequiresConfirmation() {
        let engine = PolicyEngine()
        let decision = engine.evaluate(ToolCall(toolName: "delete_all_memories"), grantedPermissions: [])
        guard case .requireConfirmation(let pending) = decision else {
            return XCTFail("expected requireConfirmation, got \(decision)")
        }
        XCTAssertEqual(pending.riskLevel, .destructive)
    }

    func testUnknownToolIsDenied() {
        let engine = PolicyEngine()
        let decision = engine.evaluate(ToolCall(toolName: "rm_rf_everything"), grantedPermissions: [])
        guard case .deny = decision else {
            return XCTFail("expected deny, got \(decision)")
        }
    }

    func testMissingPermissionIsDenied() {
        let engine = PolicyEngine()
        let decision = engine.evaluate(ToolCall(toolName: "get_pc_status"), grantedPermissions: [])
        guard case .deny(let reason) = decision else {
            return XCTFail("expected deny, got \(decision)")
        }
        XCTAssertTrue(reason.contains("pc_agent"))
    }

    func testGrantedPermissionAllowsReadOnlyPCStatus() {
        let engine = PolicyEngine()
        let decision = engine.evaluate(ToolCall(toolName: "get_pc_status"), grantedPermissions: ["pc_agent"])
        XCTAssertEqual(decision, .allow)
    }

    func testShutdownRequiresConfirmationEvenWithPermission() {
        let engine = PolicyEngine()
        let decision = engine.evaluate(ToolCall(toolName: "shutdown_pc", target: "Gaming-PC"), grantedPermissions: ["pc_agent"])
        guard case .requireConfirmation(let pending) = decision else {
            return XCTFail("expected requireConfirmation, got \(decision)")
        }
        XCTAssertEqual(pending.target, "Gaming-PC")
    }

    func testRiskLevelOrdering() {
        XCTAssertTrue(RiskLevel.readOnly < RiskLevel.reversibleWrite)
        XCTAssertTrue(RiskLevel.reversibleWrite < RiskLevel.sensitiveWrite)
        XCTAssertTrue(RiskLevel.sensitiveWrite < RiskLevel.destructive)
    }
}
