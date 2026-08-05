import Foundation

public enum ToolExecutionError: Error, Equatable {
    case unknownTool(String)
    case confirmationRequired
    case confirmationExpired
    case policyDenied(String)
    case timeout
    case executionFailed(String)
}

/// Runs a single, already-authorized tool call. `ToolExecutor` never decides
/// *whether* a call is allowed — that is `PolicyEngine`'s job. By the time
/// something reaches `execute`, policy has already approved it (and, for
/// risky tools, a fresh user confirmation has already been captured).
public protocol ToolExecutor: AnyObject, Sendable {
    func execute(_ call: ToolCall) async throws -> ToolResult
}
