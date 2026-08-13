import Foundation

/// Deterministic vision responses for demo mode and tests. Real providers
/// (Integrations/CloudVisionReasoningProvider) must follow the same
/// uncertainty-aware contract — never overclaim, never invent precise
/// instructions from an unclear image.
public final class MockVisionReasoningProvider: VisionReasoningProvider, @unchecked Sendable {
    public var scriptedResult: VisionAnalysisResult
    public private(set) var analyzeCallCount = 0
    public private(set) var lastQuestion: String?

    public init(scriptedResult: VisionAnalysisResult = MockVisionReasoningProvider.defaultResult) {
        self.scriptedResult = scriptedResult
    }

    public func analyze(image: CapturedImage, question: String) async throws -> VisionAnalysisResult {
        analyzeCallCount += 1
        lastQuestion = question
        if image.data.isEmpty {
            throw VisionReasoningError.imageUnclear
        }
        return scriptedResult
    }

    public static let defaultResult = VisionAnalysisResult(
        answer: "That looks like a laptop on a desk, with a coffee mug to its left.",
        confidence: 0.82,
        detectedText: nil,
        uncertaintyNote: nil,
        suggestedFollowUp: nil
    )
}
