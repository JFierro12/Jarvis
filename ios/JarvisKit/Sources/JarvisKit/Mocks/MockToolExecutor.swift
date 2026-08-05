import Foundation

/// Executes a tiny set of local, deterministic tools for demo mode. Anything
/// requiring PC/smart-home/calendar access delegates to the corresponding
/// mock client so the whole call chain (policy -> executor -> client) is
/// exercised the same way it would be in production.
public final class MockToolExecutor: ToolExecutor, @unchecked Sendable {
    private let memoryRepository: MemoryRepository
    private let pcAgentClient: PCAgentClient?
    private let smartHomeClient: SmartHomeClient?

    public init(memoryRepository: MemoryRepository, pcAgentClient: PCAgentClient? = nil, smartHomeClient: SmartHomeClient? = nil) {
        self.memoryRepository = memoryRepository
        self.pcAgentClient = pcAgentClient
        self.smartHomeClient = smartHomeClient
    }

    public func execute(_ call: ToolCall) async throws -> ToolResult {
        switch call.toolName {
        case "get_current_time":
            let formatter = DateFormatter()
            formatter.timeStyle = .short
            return ToolResult(success: true, output: formatter.string(from: Date()))
        case "delete_memory":
            guard let id = UUID(uuidString: call.target) else {
                return ToolResult(success: false, output: "invalid id")
            }
            try await memoryRepository.delete(id: id)
            return ToolResult(success: true, output: "deleted", confirmedAction: true)
        case "delete_all_memories":
            try await memoryRepository.deleteAll()
            return ToolResult(success: true, output: "deleted all", confirmedAction: true)
        case "get_pc_status":
            guard let pcAgentClient else { return ToolResult(success: false, output: "pc agent unavailable") }
            let status = try await pcAgentClient.status()
            return ToolResult(success: true, output: "CPU \(Int(status.cpuUtilization))%")
        case "control_home_device":
            guard let smartHomeClient else { return ToolResult(success: false, output: "smart home unavailable") }
            let entity = try await smartHomeClient.setState(entityId: call.target, state: call.arguments["state"] ?? "off")
            return ToolResult(success: true, output: entity.state, confirmedAction: true)
        default:
            throw ToolExecutionError.unknownTool(call.toolName)
        }
    }
}
