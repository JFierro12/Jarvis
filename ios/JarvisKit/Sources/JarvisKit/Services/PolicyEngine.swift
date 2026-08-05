import Foundation

/// The single deterministic authority for "may this tool call run." A
/// language model can propose a call; only `PolicyEngine` decides. It never
/// consults the model to make that decision.
public final class PolicyEngine: Sendable {
    private let registry: ToolRegistry
    private let confirmationWindow: TimeInterval

    public init(registry: ToolRegistry = ToolRegistry(), confirmationWindow: TimeInterval = 20) {
        self.registry = registry
        self.confirmationWindow = confirmationWindow
    }

    public func evaluate(_ call: ToolCall, grantedPermissions: Set<String>) -> PolicyDecision {
        guard let definition = registry.definition(for: call.toolName) else {
            return .deny(reason: "Unknown tool: \(call.toolName)")
        }

        let missing = Set(definition.requiredPermissions).subtracting(grantedPermissions)
        guard missing.isEmpty else {
            return .deny(reason: "Missing permission(s): \(missing.sorted().joined(separator: ", "))")
        }

        if definition.requiresConfirmation {
            let pending = PendingAction(
                toolName: definition.name,
                summary: summary(for: call, definition: definition),
                target: call.target,
                riskLevel: definition.riskLevel,
                expiresAt: Date().addingTimeInterval(confirmationWindow)
            )
            return .requireConfirmation(pending)
        }

        return .allow
    }

    private func summary(for call: ToolCall, definition: ToolDefinition) -> String {
        call.target.isEmpty ? definition.description : "\(definition.description) (\(call.target))"
    }
}
