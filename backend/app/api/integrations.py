from fastapi import APIRouter, Depends

from app.core.security import require_bearer_token
from app.models.schemas import PCStatusResponse

router = APIRouter()


@router.get("/v1/integrations/pc/status", response_model=PCStatusResponse)
def pc_status(_token: str = Depends(require_bearer_token)) -> PCStatusResponse:
    # TODO: proxy to the pc-agent service (see pc-agent/) once its base URL
    # and pairing token are configured. Returns deterministic mock data so
    # the endpoint is exercisable without a running pc-agent.
    return PCStatusResponse(
        host_name="Gaming-PC",
        operating_system="Windows 11",
        cpu_utilization=12.0,
        memory_utilization=41.0,
        disk_utilization=63.0,
        uptime_seconds=18000.0,
    )
