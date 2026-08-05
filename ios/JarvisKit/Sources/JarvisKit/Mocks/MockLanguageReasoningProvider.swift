import Foundation

public final class MockLanguageReasoningProvider: LanguageReasoningProvider, @unchecked Sendable {
    public var scriptedResponse: ReasoningResponse

    public init(scriptedResponse: ReasoningResponse = ReasoningResponse(spokenAnswer: "I'm not certain — could you rephrase that?")) {
        self.scriptedResponse = scriptedResponse
    }

    public func reason(_ request: ReasoningRequest) async throws -> ReasoningResponse {
        scriptedResponse
    }
}
