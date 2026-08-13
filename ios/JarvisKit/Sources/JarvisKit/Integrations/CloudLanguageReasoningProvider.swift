import Foundation

/// Calls `POST /v1/reason` on the configured backend. The backend, not this
/// client, is responsible for actually invoking a language model — this type
/// only serializes context (already tagged by `ContextSource`) and
/// deserializes a structured response. Tool-call proposals are validated
/// against `ToolRegistry`/`PolicyEngine` by the caller, never trusted directly.
public final class CloudLanguageReasoningProvider: LanguageReasoningProvider, @unchecked Sendable {
    private struct ContextItemBody: Encodable {
        let source: String
        let label: String
        let content: String
    }
    private struct RequestBody: Encodable {
        let context: [ContextItemBody]
        let question: String
        let availableTools: [String]
    }
    private struct ToolCallBody: Decodable {
        let toolName: String
        let arguments: [String: String]
        let target: String
    }
    private struct ResponseBody: Decodable {
        let spokenAnswer: String
        let proposedToolCall: ToolCallBody?
        let requiresConfirmation: Bool
    }

    private let client: BackendAPIClient

    public init(client: BackendAPIClient) {
        self.client = client
    }

    public func reason(_ request: ReasoningRequest) async throws -> ReasoningResponse {
        do {
            let body = RequestBody(
                context: request.context.items.map { ContextItemBody(source: $0.source.rawValue, label: $0.label, content: $0.content) },
                question: request.question,
                availableTools: request.availableTools
            )
            // 15s was sized for the one-or-two-sentence default; bumped so
            // the verbal football-coverage exception (see the backend's
            // anthropic_reasoning.py system prompt) has room for a full
            // breakdown without the same truncate-and-report-unavailable
            // bug the vision endpoint had at its old 15s.
            let response: ResponseBody = try await client.post(path: "/v1/reason", body: body, timeout: 25)
            let toolCall = response.proposedToolCall.map { ToolCall(toolName: $0.toolName, arguments: $0.arguments, target: $0.target) }
            return ReasoningResponse(spokenAnswer: response.spokenAnswer, proposedToolCall: toolCall, requiresConfirmation: response.requiresConfirmation)
        } catch BackendAPIError.requestFailed, BackendAPIError.decodingFailed {
            throw LanguageReasoningError.unavailable
        }
    }
}
