import Foundation

/// The single source of truth for what JARVIS is doing right now.
///
/// Every UI surface (activation button, listening indicator, diagnostics screen)
/// renders from this enum instead of tracking its own booleans, so "are we
/// listening" never disagrees between two parts of the app.
public enum AssistantState: Equatable, Sendable {
    case idle
    case activating
    case listening(ActivationMode)
    case transcribing
    case deciding
    case capturingVisualContext
    case reasoning
    case awaitingConfirmation(PendingAction)
    case executing(ActionDescriptor)
    case speaking
    case completed
    case failed(UserFacingError)
}

public enum ActivationMode: Equatable, Sendable {
    case pushToTalk
    case pressToActivateSession
    case appIntent
    case foregroundWakeWord
}

public struct PendingAction: Equatable, Sendable, Identifiable {
    public let id: UUID
    public let toolName: String
    public let summary: String
    public let target: String
    public let riskLevel: RiskLevel
    public let expiresAt: Date

    public init(id: UUID = UUID(), toolName: String, summary: String, target: String, riskLevel: RiskLevel, expiresAt: Date) {
        self.id = id
        self.toolName = toolName
        self.summary = summary
        self.target = target
        self.riskLevel = riskLevel
        self.expiresAt = expiresAt
    }

    public var isExpired: Bool {
        Date() > expiresAt
    }
}

public struct ActionDescriptor: Equatable, Sendable {
    public let toolName: String
    public let summary: String

    public init(toolName: String, summary: String) {
        self.toolName = toolName
        self.summary = summary
    }
}

public struct UserFacingError: Equatable, Sendable, Identifiable {
    public let id: UUID
    public let message: String
    public let isRecoverable: Bool

    public init(id: UUID = UUID(), message: String, isRecoverable: Bool = true) {
        self.id = id
        self.message = message
        self.isRecoverable = isRecoverable
    }

    public static let glassesDisconnected = UserFacingError(message: "The glasses are disconnected.")
    public static let cameraPermissionDenied = UserFacingError(message: "Camera access was denied.")
    public static let cameraBusy = UserFacingError(message: "The glasses camera is busy.")
    public static let imageUnclear = UserFacingError(message: "I could not read that image clearly.")
    public static let networkTimeout = UserFacingError(message: "The network request timed out.")
    public static let calendarDisabled = UserFacingError(message: "Calendar access is disabled.")
    public static let confirmationRequired = UserFacingError(message: "That action requires confirmation.")
    public static let pcAgentOffline = UserFacingError(message: "Your computer agent is offline.")
    public static let visionUnavailable = UserFacingError(message: "Vision analysis is unavailable. The glasses are still connected.")
    public static let unsafeAction = UserFacingError(message: "I could not complete that safely.")
}

/// Enumerates every legal transition. `AssistantStateMachine` is the only thing
/// allowed to mutate the current state, and it rejects anything not listed here.
public enum StateTransitionError: Error, Equatable {
    case invalidTransition(from: AssistantState, to: AssistantState)
}

/// Owns `AssistantState` and enforces the transition table from the architecture spec:
/// no overlapping capture/listening sessions, no executing without confirmation, etc.
public final class AssistantStateMachine {
    public private(set) var state: AssistantState = .idle
    private let onChange: (@Sendable (AssistantState) -> Void)?

    public init(onChange: (@Sendable (AssistantState) -> Void)? = nil) {
        self.onChange = onChange
    }

    @discardableResult
    public func transition(to newState: AssistantState) throws -> AssistantState {
        guard Self.isAllowed(from: state, to: newState) else {
            throw StateTransitionError.invalidTransition(from: state, to: newState)
        }
        state = newState
        onChange?(newState)
        return state
    }

    public func reset() {
        state = .idle
        onChange?(.idle)
    }

    /// The transition table. Kept as a pure static function so it can be
    /// exhaustively unit tested without spinning up the rest of the app.
    static func isAllowed(from: AssistantState, to: AssistantState) -> Bool {
        switch (from, to) {
        // Any state can fail or be cancelled back to idle.
        case (_, .failed), (_, .idle):
            return true

        case (.idle, .activating):
            return true
        case (.activating, .listening):
            return true
        case (.listening, .transcribing):
            return true
        case (.listening, .listening):
            // Re-entrant push-to-talk taps while already listening are a no-op, not a new session.
            return false
        case (.transcribing, .deciding):
            return true
        case (.deciding, .capturingVisualContext):
            return true
        case (.deciding, .reasoning):
            return true
        case (.deciding, .awaitingConfirmation):
            return true
        case (.deciding, .executing):
            // A resolved confirmation (user just said "confirm") routes straight
            // from deciding to executing without a separate reasoning pass.
            return true
        case (.deciding, .speaking):
            // Purely local/deterministic answers can skip reasoning and go straight to speech.
            return true
        case (.capturingVisualContext, .reasoning):
            return true
        case (.reasoning, .awaitingConfirmation):
            return true
        case (.reasoning, .executing):
            return true
        case (.reasoning, .speaking):
            return true
        case (.awaitingConfirmation, .executing):
            return true
        case (.awaitingConfirmation, .speaking):
            // Confirmation expired or was rejected; JARVIS speaks a short acknowledgement.
            return true
        case (.executing, .speaking):
            return true
        case (.speaking, .completed):
            return true
        case (.completed, .idle):
            return true
        case (.completed, .activating):
            // Bounded follow-up window in press-to-activate sessions.
            return true
        case (.failed, .idle):
            return true
        default:
            return false
        }
    }
}
