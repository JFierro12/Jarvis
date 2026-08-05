import Foundation

public enum VisionReasoningError: Error, Equatable {
    case unavailable
    case timeout
    case imageUnclear
}

/// Analyzes one captured image against a question. Implementations must not
/// overstate certainty and must never follow instructions found inside the
/// image itself (see docs/THREAT_MODEL.md) — image text is CAMERA_OBSERVATION
/// data, not an instruction channel.
public protocol VisionReasoningProvider: AnyObject, Sendable {
    func analyze(image: CapturedImage, question: String) async throws -> VisionAnalysisResult
}
