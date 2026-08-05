import Foundation

/// The strict, closed set of tools JARVIS may call (spec §17). A model can
/// *propose* a call by name; only a name present here, with matching schema,
/// can ever reach `ToolExecutor`.
public final class ToolRegistry: Sendable {
    private let definitions: [String: ToolDefinition]

    public init(definitions: [ToolDefinition] = ToolRegistry.defaults) {
        self.definitions = Dictionary(uniqueKeysWithValues: definitions.map { ($0.name, $0) })
    }

    public func definition(for name: String) -> ToolDefinition? {
        definitions[name]
    }

    public var all: [ToolDefinition] {
        Array(definitions.values)
    }

    public static let defaults: [ToolDefinition] = [
        ToolDefinition(name: "get_current_time", description: "Returns the current local time.", riskLevel: .readOnly, timeoutSeconds: 2),
        ToolDefinition(name: "get_calendar_events", description: "Reads upcoming calendar events.", requiredPermissions: ["calendar"], riskLevel: .readOnly, timeoutSeconds: 5),
        ToolDefinition(name: "create_reminder", description: "Creates a reminder.", requiredPermissions: ["reminders"], riskLevel: .reversibleWrite, timeoutSeconds: 5),
        ToolDefinition(name: "save_note", description: "Saves a note to memory.", riskLevel: .reversibleWrite, timeoutSeconds: 5),
        ToolDefinition(name: "save_memory", description: "Saves an observation to long-term memory.", riskLevel: .reversibleWrite, timeoutSeconds: 5),
        ToolDefinition(name: "search_memory", description: "Searches saved memories.", riskLevel: .readOnly, timeoutSeconds: 5),
        ToolDefinition(name: "delete_memory", description: "Deletes one memory.", riskLevel: .destructive, requiresConfirmation: true, timeoutSeconds: 5),
        ToolDefinition(name: "delete_all_memories", description: "Deletes all memories.", riskLevel: .destructive, requiresConfirmation: true, timeoutSeconds: 5),
        ToolDefinition(name: "get_device_status", description: "Reads glasses connection/diagnostics state.", riskLevel: .readOnly, timeoutSeconds: 2),
        ToolDefinition(name: "get_pc_status", description: "Reads read-only PC health metrics.", requiredPermissions: ["pc_agent"], riskLevel: .readOnly, timeoutSeconds: 5),
        ToolDefinition(name: "control_home_device", description: "Sets state on an allowlisted smart-home entity.", requiredPermissions: ["smart_home"], riskLevel: .externalSideEffect, timeoutSeconds: 5),
        ToolDefinition(name: "lock_pc", description: "Locks the paired PC.", requiredPermissions: ["pc_agent"], riskLevel: .externalSideEffect, timeoutSeconds: 5),
        ToolDefinition(name: "shutdown_pc", description: "Shuts down the paired PC.", requiredPermissions: ["pc_agent"], riskLevel: .sensitiveWrite, requiresConfirmation: true, timeoutSeconds: 5),
        ToolDefinition(name: "open_navigation", description: "Opens navigation to a destination.", riskLevel: .externalSideEffect, timeoutSeconds: 3)
    ]
}
