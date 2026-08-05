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
            let response: ResponseBody = try await client.post(
                path: "/v1/vision/analyze",
                body: RequestBody(imageBase64: image.data.base64EncodedString(), question: question),
                timeout: 15
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
