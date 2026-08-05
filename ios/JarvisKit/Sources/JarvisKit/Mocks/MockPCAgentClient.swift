import Foundation

public final class MockPCAgentClient: PCAgentClient, @unchecked Sendable {
    public var scriptedStatus: PCStatus

    public init(scriptedStatus: PCStatus = PCStatus(hostName: "Gaming-PC", operatingSystem: "Windows 11", cpuUtilization: 12, memoryUtilization: 41, diskUtilization: 63, uptimeSeconds: 3600 * 5)) {
        self.scriptedStatus = scriptedStatus
    }

    public func status() async throws -> PCStatus {
        scriptedStatus
    }

    public func runCommand(_ id: PCCommandID) async throws -> ToolResult {
        ToolResult(success: true, output: "\(id.rawValue) completed", confirmedAction: true)
    }
}
