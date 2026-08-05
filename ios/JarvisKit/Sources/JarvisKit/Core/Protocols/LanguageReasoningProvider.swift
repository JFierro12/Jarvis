import Foundation

public struct ReasoningRequest: Sendable, Equatable {
    public let context: AssembledContext
    public let question: String
    public let availableTools: [String]

    public init(context: AssembledContext, question: String, availableTools: [String] = []) {
        self.context = context
        self.question = question
        self.availableTools = availableTools
    }
}

public struct ReasoningResponse: Sendable, Equatable {
    public let spokenAnswer: String
    public let proposedToolCall: ToolCall?
    public let requiresConfirmation: Bool

    public init(spokenAnswer: String, proposedToolCall: ToolCall? = nil, requiresConfirmation: Bool = false) {
        self.spokenAnswer = spokenAnswer
        self.proposedToolCall = proposedToolCall
        self.requiresConfirmation = requiresConfirmation
    }
}

public enum LanguageReasoningError: Error, Equatable {
    case unavailable
    case timeout
    case invalidResponse
}

/// The only component allowed to *propose* a tool call from natural language.
/// It never authorizes or executes anything — `PolicyEngine` and `ToolExecutor`
/// downstream make that decision, and a model-generated tool call is always
/// validated against the real `ToolDefinition` schema before it can run.
public protocol LanguageReasoningProvider: AnyObject, Sendable {
    func reason(_ request: ReasoningRequest) async throws -> ReasoningResponse
}
