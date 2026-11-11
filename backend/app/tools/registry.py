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
        ToolDefinition("open_website", "Opens a website or web app in a new browser tab.", "external_side_effect"),
        ToolDefinition("close_website", "Closes a previously opened website/app tab.", "reversible_write"),
        ToolDefinition("launch_app", "Launches a desktop application by name.", "reversible_write", ["os_control"]),
        ToolDefinition("focus_app", "Brings an open application window to the foreground.", "read_only", ["os_control"]),
        ToolDefinition("list_open_windows", "Lists titles of currently open application windows.", "read_only", ["os_control"]),
        ToolDefinition(
            "close_app",
            "Closes an open application window — may trigger the app's own unsaved-work prompt.",
            "external_side_effect",
            ["os_control"],
            requires_confirmation=True,
        ),
        ToolDefinition(
            "take_screenshot", "Captures the screen and describes what's visible.", "read_only", ["screen_capture"]
        ),
        ToolDefinition("list_files", "Lists files in a permitted folder.", "read_only", ["file_access"]),
        ToolDefinition("read_file", "Reads a text file from a permitted folder.", "read_only", ["file_access"]),
        ToolDefinition("write_file", "Creates a new text file in a permitted folder.", "reversible_write", ["file_access"]),
        ToolDefinition("append_file", "Appends text to a file in a permitted folder.", "reversible_write", ["file_access"]),
        ToolDefinition("create_folder", "Creates a folder inside a permitted folder.", "reversible_write", ["file_access"]),
        ToolDefinition("move_file", "Moves or renames a file within permitted folders.", "reversible_write", ["file_access"]),
        ToolDefinition(
            "delete_file",
            "Sends a file to the Recycle Bin (recoverable).",
            "destructive",
            ["file_access"],
        ),
    ]
}
