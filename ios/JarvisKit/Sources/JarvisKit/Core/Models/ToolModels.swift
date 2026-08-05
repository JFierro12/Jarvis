import Foundation

public enum RiskLevel: String, Codable, Sendable, CaseIterable, Comparable {
    case readOnly = "read_only"
    case reversibleWrite = "reversible_write"
    case sensitiveWrite = "sensitive_write"
    case destructive
    case externalSideEffect = "external_side_effect"

    private var ordinal: Int {
        switch self {
        case .readOnly: return 0
        case .reversibleWrite: return 1
        case .externalSideEffect: return 2
        case .sensitiveWrite: return 3
        case .destructive: return 4
        }
    }

    public static func < (lhs: RiskLevel, rhs: RiskLevel) -> Bool {
        lhs.ordinal < rhs.ordinal
    }

    /// Whether this risk level requires an explicit spoken/on-screen confirmation
    /// before the policy engine will let `ToolExecutor` run it.
    public var requiresConfirmationByDefault: Bool {
        self >= .sensitiveWrite
    }
}

public struct ToolDefinition: Sendable, Equatable {
    public let name: String
    public let description: String
    public let inputSchema: [String: String]
    public let outputSchema: [String: String]
    public let requiredPermissions: [String]
    public let riskLevel: RiskLevel
    public let requiresConfirmation: Bool
    public let timeoutSeconds: TimeInterval
    public let maxRetries: Int
    public let isIdempotent: Bool

    public init(
        name: String,
        description: String,
        inputSchema: [String: String] = [:],
        outputSchema: [String: String] = [:],
        requiredPermissions: [String] = [],
        riskLevel: RiskLevel,
        requiresConfirmation: Bool? = nil,
        timeoutSeconds: TimeInterval = 10,
        maxRetries: Int = 0,
        isIdempotent: Bool = true
    ) {
        self.name = name
        self.description = description
        self.inputSchema = inputSchema
        self.outputSchema = outputSchema
        self.requiredPermissions = requiredPermissions
        self.riskLevel = riskLevel
        self.requiresConfirmation = requiresConfirmation ?? riskLevel.requiresConfirmationByDefault
        self.timeoutSeconds = timeoutSeconds
        self.maxRetries = isIdempotent ? maxRetries : 0
        self.isIdempotent = isIdempotent
    }
}

public struct ToolCall: Sendable, Equatable {
    public let toolName: String
    public let arguments: [String: String]
    public let target: String

    public init(toolName: String, arguments: [String: String] = [:], target: String = "") {
        self.toolName = toolName
        self.arguments = arguments
        self.target = target
    }
}

public struct ToolResult: Sendable, Equatable {
    public let success: Bool
    public let output: String
    public let confirmedAction: Bool

    public init(success: Bool, output: String, confirmedAction: Bool = false) {
        self.success = success
        self.output = output
        self.confirmedAction = confirmedAction
    }
}

public enum PolicyDecision: Sendable, Equatable {
    case allow
    case requireConfirmation(PendingAction)
    case deny(reason: String)
}

public struct AuditEvent: Sendable, Equatable {
    public let id: UUID
    public let timestamp: Date
    public let toolName: String
    public let target: String
    public let riskLevel: RiskLevel
    public let decision: String
    public let outcome: String

    public init(id: UUID = UUID(), timestamp: Date = Date(), toolName: String, target: String, riskLevel: RiskLevel, decision: String, outcome: String) {
        self.id = id
        self.timestamp = timestamp
        self.toolName = toolName
        self.target = target
        self.riskLevel = riskLevel
        self.decision = decision
        self.outcome = outcome
    }
}
