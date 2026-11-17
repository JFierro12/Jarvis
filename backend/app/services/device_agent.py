"""Device boundary for the first PC-hosted runtime. No shell strings cross it."""

import sys
from typing import Protocol

import psutil

from app.models.schemas import ToolExecuteResponse
from app.services.os_automation import launch_app


class DeviceAgent(Protocol):
    def available(self) -> bool: ...
    def execute(self, tool_name: str, target: str) -> ToolExecuteResponse: ...


class LocalWindowsAgent:
    def available(self) -> bool:
        return sys.platform == "win32"

    def execute(self, tool_name: str, target: str) -> ToolExecuteResponse:
        if not self.available():
            return ToolExecuteResponse(success=False, output="Windows agent unavailable")
        if tool_name == "get_pc_status":
            output = f"CPU {psutil.cpu_percent():.0f}%, RAM {psutil.virtual_memory().percent:.0f}%"
            return ToolExecuteResponse(success=True, output=output)
        if tool_name == "launch_app":
            return ToolExecuteResponse(success=True, output=launch_app(target))
        return ToolExecuteResponse(success=False, output="Device capability unavailable")
