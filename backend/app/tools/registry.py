from dataclasses import dataclass, field
from typing import Dict, List

RISK_ORDER = {
    "read_only": 0,
    "reversible_write": 1,
    "external_side_effect": 2,
    "sensitive_write": 3,
    "destructive": 4,
}


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    risk_level: str
    required_permissions: List[str] = field(default_factory=list)
    requires_confirmation: bool = False
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not self.requires_confirmation and RISK_ORDER[self.risk_level] >= RISK_ORDER["sensitive_write"]:
            object.__setattr__(self, "requires_confirmation", True)


TOOL_DEFINITIONS: Dict[str, ToolDefinition] = {
    d.name: d
    for d in [
        ToolDefinition("get_current_time", "Returns the current time.", "read_only"),
        ToolDefinition("get_calendar_events", "Reads upcoming calendar events.", "read_only", ["calendar"]),
        ToolDefinition("create_reminder", "Creates a reminder.", "reversible_write", ["reminders"]),
        ToolDefinition("save_note", "Saves a note to memory.", "reversible_write"),
        ToolDefinition("save_memory", "Saves an observation to long-term memory.", "reversible_write"),
        ToolDefinition("search_memory", "Searches saved memories.", "read_only"),
        ToolDefinition("delete_memory", "Deletes one memory.", "destructive"),
        ToolDefinition("delete_all_memories", "Deletes all memories.", "destructive"),
        ToolDefinition("get_device_status", "Reads glasses connection/diagnostics state.", "read_only"),
        ToolDefinition("get_pc_status", "Reads read-only PC health metrics.", "read_only", ["pc_agent"]),
        ToolDefinition(
            "control_home_device", "Sets state on an allowlisted smart-home entity.", "external_side_effect", ["smart_home"]
        ),
        ToolDefinition("lock_pc", "Locks the paired PC.", "external_side_effect", ["pc_agent"]),
        ToolDefinition("shutdown_pc", "Shuts down the paired PC.", "sensitive_write", ["pc_agent"]),
        ToolDefinition("open_navigation", "Opens navigation to a destination.", "external_side_effect"),
    ]
}
