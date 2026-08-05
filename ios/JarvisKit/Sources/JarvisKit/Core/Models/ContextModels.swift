import Foundation

/// Prompt-injection defense boundary. Every piece of context handed to a
/// reasoning provider is tagged with where it came from, so the provider
/// (and the policy engine downstream of it) can treat CAMERA_OBSERVATION /
/// EXTERNAL_CONTENT / TOOL_RESULT / MEMORY_RESULT text as data, never as
/// instructions. Only SYSTEM_POLICY and USER_REQUEST may ever authorize a
/// tool call, and only the policy engine — never the model itself — grants
/// tool permissions.
public enum ContextSource: String, Codable, Sendable {
    case systemPolicy = "SYSTEM_POLICY"
    case userRequest = "USER_REQUEST"
    case toolResult = "TOOL_RESULT"
    case cameraObservation = "CAMERA_OBSERVATION"
    case memoryResult = "MEMORY_RESULT"
    case externalContent = "EXTERNAL_CONTENT"

    /// Whether text from this source is allowed to influence tool authorization.
    /// Camera/tool/memory/external content is always untrusted data.
    public var canAuthorizeActions: Bool {
        switch self {
        case .systemPolicy, .userRequest: return true
        case .toolResult, .cameraObservation, .memoryResult, .externalContent: return false
        }
    }
}

public struct ContextItem: Sendable, Equatable {
    public let source: ContextSource
    public let label: String
    public let content: String

    public init(source: ContextSource, label: String, content: String) {
        self.source = source
        self.label = label
        self.content = content
    }
}

/// A least-privilege bundle of context assembled for one request. Only the
/// fields actually needed by the current intent are populated — see
/// `ContextAssembler`.
public struct AssembledContext: Sendable, Equatable {
    public var items: [ContextItem]
    public var requestedAt: Date

    public init(items: [ContextItem] = [], requestedAt: Date = Date()) {
        self.items = items
        self.requestedAt = requestedAt
    }

    public func rendered() -> String {
        items.map { "[\($0.source.rawValue): \($0.label)]\n\($0.content)" }.joined(separator: "\n\n")
    }
}

public struct VisionAnalysisResult: Sendable, Equatable {
    public let answer: String
    public let confidence: Double
    public let detectedText: String?
    public let uncertaintyNote: String?
    public let suggestedFollowUp: String?

    public init(answer: String, confidence: Double, detectedText: String? = nil, uncertaintyNote: String? = nil, suggestedFollowUp: String? = nil) {
        self.answer = answer
        self.confidence = confidence
        self.detectedText = detectedText
        self.uncertaintyNote = uncertaintyNote
        self.suggestedFollowUp = suggestedFollowUp
    }
}
