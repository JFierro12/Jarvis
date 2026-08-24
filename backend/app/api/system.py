import time
from typing import Optional

import psutil
from fastapi import APIRouter, Depends

from app.core.security import require_bearer_token
from app.models.schemas import GpuStats, SystemStatsResponse

router = APIRouter()

_BOOT_TIME = psutil.boot_time()


def _collect_gpu_stats() -> Optional[GpuStats]:
    """Best-effort NVIDIA GPU stats. Returns None on any non-NVIDIA machine or
    missing driver (e.g. this runs fine on a Mac dev box with no GPU widget
    data) rather than failing the whole endpoint.
    """
    try:
        import pynvml

        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8")
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            try:
                temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            except pynvml.NVMLError:
                temp = None
            return GpuStats(
                name=name,
                load_percent=float(util.gpu),
                memory_used_mb=mem.used / (1024**2),
                memory_total_mb=mem.total / (1024**2),
                temperature_c=temp,
            )
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return None


@router.get("/v1/system/stats", response_model=SystemStatsResponse)
def system_stats(_token: str = Depends(require_bearer_token)) -> SystemStatsResponse:
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return SystemStatsResponse(
        cpu_percent=psutil.cpu_percent(interval=0.1),
        ram_percent=ram.percent,
        ram_used_gb=ram.used / (1024**3),
        ram_total_gb=ram.total / (1024**3),
        disk_percent=disk.percent,
        disk_used_gb=disk.used / (1024**3),
        disk_total_gb=disk.total / (1024**3),
        uptime_seconds=time.time() - _BOOT_TIME,
        gpu=_collect_gpu_stats(),
    )
