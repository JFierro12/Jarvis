import Foundation

public final class MockLanguageReasoningProvider: LanguageReasoningProvider, @unchecked Sendable {
    public var scriptedResponse: ReasoningResponse
    public private(set) var lastRequest: ReasoningRequest?

    public init(scriptedResponse: ReasoningResponse = ReasoningResponse(spokenAnswer: "I'm not certain — could you rephrase that?")) {
        self.scriptedResponse = scriptedResponse
    }

    public func reason(_ request: ReasoningRequest) async throws -> ReasoningResponse {
        lastRequest = request
        return scriptedResponse
    }
}
