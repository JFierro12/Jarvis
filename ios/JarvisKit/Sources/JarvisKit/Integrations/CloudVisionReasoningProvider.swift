import Foundation

/// Calls `POST /v1/vision/analyze` on the configured backend. See
/// backend/app/api for the server-side implementation.
public final class CloudVisionReasoningProvider: VisionReasoningProvider, @unchecked Sendable {
    private struct RequestBody: Encodable {
        let imageBase64: String
        let question: String
    }
    private struct ResponseBody: Decodable {
        let answer: String
        let confidence: Double
        let detectedText: String?
        let uncertaintyNote: String?
        let suggestedFollowUp: String?
    }

    private let client: BackendAPIClient

    public init(client: BackendAPIClient) {
        self.client = client
    }

    public func analyze(image: CapturedImage, question: String) async throws -> VisionAnalysisResult {
        do {
            // 15s was sized for the old low-effort/1024-token vision calls.
            // The football-coverage breakdown (effort: high, 4096 tokens on
            // the backend — see anthropic_vision.py) can legitimately take
            // longer than that to reason through and write; a too-short
            // client timeout was cutting those off and reporting "vision
            // analysis unavailable" even when the backend would have
            // returned a good answer given a few more seconds.
            let response: ResponseBody = try await client.post(
                path: "/v1/vision/analyze",
                body: RequestBody(imageBase64: image.data.base64EncodedString(), question: question),
                timeout: 45
            )
            return VisionAnalysisResult(
                answer: response.answer,
                confidence: response.confidence,
                detectedText: response.detectedText,
                uncertaintyNote: response.uncertaintyNote,
                suggestedFollowUp: response.suggestedFollowUp
            )
        } catch BackendAPIError.requestFailed, BackendAPIError.decodingFailed {
            throw VisionReasoningError.unavailable
        }
    }
}
