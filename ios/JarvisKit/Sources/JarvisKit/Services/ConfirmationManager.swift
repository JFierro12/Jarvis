import Foundation

public enum ConfirmationOutcome: Equatable, Sendable {
    case confirmed(PendingAction)
    case rejected(PendingAction)
    case expired(PendingAction)
}

/// Tracks exactly one pending confirmation at a time. A fresh confirmation
/// is required after expiry — there is no implicit retry.
public actor ConfirmationManager {
    private var pending: PendingAction?

    public init() {}

    public func propose(_ action: PendingAction) {
        pending = action
    }

    public func currentPending() -> PendingAction? {
        if let pending, pending.isExpired {
            self.pending = nil
            return nil
        }
        return pending
    }

    public func resolve(userSaidYes: Bool) -> ConfirmationOutcome? {
        guard let action = pending else { return nil }
        pending = nil
        if action.isExpired {
            return .expired(action)
        }
        return userSaidYes ? .confirmed(action) : .rejected(action)
    }

    public func clear() {
        pending = nil
    }
}
