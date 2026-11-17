import Foundation

public struct SharedTurn: Codable, Sendable {
    public let contractVersion: String
    public let conversationId: String
    public let turnId: String
    public let spokenAnswer: String
    public let actionId: String
    public let decision: String
    public let confirmationSummary: String
    public let provider: String
    public let model: String
    public let promptVersion: String
}

/// Shared runtime performs server-side policy checks. Approval is invoked only
/// from the coordinator's explicit confirmation branch, never by reasoning.
public protocol SharedAssistantRuntime: Sendable {
    func turn(question: String) async throws -> SharedTurn
    func execute(actionId: String) async throws -> ToolResult
    func approve(actionId: String) async throws
    func cancel(actionId: String) async throws
}
