import platform
import time

import psutil

from app.schemas import PCStatusResponse

_BOOT_TIME = psutil.boot_time()


def collect_status() -> PCStatusResponse:
    disk = psutil.disk_usage("/")
    return PCStatusResponse(
        host_name=platform.node(),
        operating_system=f"{platform.system()} {platform.release()}",
        cpu_utilization=psutil.cpu_percent(interval=0.1),
        memory_utilization=psutil.virtual_memory().percent,
        disk_utilization=disk.percent,
        uptime_seconds=time.time() - _BOOT_TIME,
        service_health={},
    )
