import Foundation

public struct PCStatus: Sendable, Equatable, Codable {
    public let hostName: String
    public let operatingSystem: String
    public let cpuUtilization: Double
    public let memoryUtilization: Double
    public let diskUtilization: Double
    public let uptimeSeconds: Double
    public let serviceHealth: [String: Bool]

    public init(hostName: String, operatingSystem: String, cpuUtilization: Double, memoryUtilization: Double, diskUtilization: Double, uptimeSeconds: Double, serviceHealth: [String: Bool] = [:]) {
        self.hostName = hostName
        self.operatingSystem = operatingSystem
        self.cpuUtilization = cpuUtilization
        self.memoryUtilization = memoryUtilization
        self.diskUtilization = diskUtilization
        self.uptimeSeconds = uptimeSeconds
        self.serviceHealth = serviceHealth
    }
}

/// Predefined command IDs only — the mobile app and backend request an ID,
/// never a raw command string. See pc-agent/README for the allowlist.
public enum PCCommandID: String, Sendable, CaseIterable, Codable {
    case lockComputer = "lock_computer"
    case launchVSCode = "launch_vscode"
    case startDevServer = "start_dev_server"
    case stopDevServer = "stop_dev_server"
    case runTestScript = "run_test_script"
}

public enum PCAgentError: Error, Equatable {
    case offline
    case unauthorized
    case unknownCommand
    case rateLimited
}

public protocol PCAgentClient: AnyObject, Sendable {
    func status() async throws -> PCStatus
    func runCommand(_ id: PCCommandID) async throws -> ToolResult
}
