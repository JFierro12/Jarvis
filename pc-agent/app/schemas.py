from typing import Dict

from pydantic import BaseModel


class PCStatusResponse(BaseModel):
    host_name: str
    operating_system: str
    cpu_utilization: float
    memory_utilization: float
    disk_utilization: float
    uptime_seconds: float
    service_health: Dict[str, bool]


class CommandResponse(BaseModel):
    command_id: str
    success: bool
    output: str
