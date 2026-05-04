"""Monitoring, metrics, and alerting management."""

import os
import shutil
from typing import Any, Dict

import psutil


class MonitoringManager:
    """Manages system monitoring and resource tracking."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize with threshold configuration."""
        self.config = config
        self.cpu_threshold = config.get("cpu_threshold", 90)
        self.memory_threshold = config.get("memory_threshold", 90)
        self.disk_threshold = config.get("disk_threshold", 90)
        # Prime psutil's internal CPU counters so subsequent non-blocking reads
        # return usage since the previous call rather than an initial 0.0.
        psutil.cpu_percent(interval=None)

    def read_meminfo(self) -> dict[str, int]:
        """Read memory information from /proc/meminfo."""
        info: dict[str, int] = {}
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith(("MemTotal", "MemAvailable", "MemFree")):
                        parts = line.split()
                        if len(parts) >= 2:
                            info[parts[0].rstrip(":")] = int(parts[1])
        except OSError:
            return {}
        return info

    def get_resource_usage(self) -> dict[str, Any]:
        """Collect CPU, memory, and disk usage metrics with legacy-compatible keys."""
        # Use non-blocking CPU percent measured over the interval since the
        # previous call, which is typically more representative than a 0.1s
        # instantaneous sample for dashboard and alerting usage.
        cpu_percent_actual = psutil.cpu_percent(interval=None)

        meminfo = self.read_meminfo()
        mem_total_kb = meminfo.get("MemTotal", 0)
        mem_available_kb = meminfo.get("MemAvailable", 0)
        mem_used_kb = max(mem_total_kb - mem_available_kb, 0)
        mem_used_percent = round((mem_used_kb / mem_total_kb) * 100, 1) if mem_total_kb > 0 else 0.0

        disk = shutil.disk_usage("/")
        disk_used_percent = round((disk.used / disk.total) * 100, 1) if disk.total > 0 else 0.0

        load1, load5, load15 = os.getloadavg()
        cpu_count = os.cpu_count() or 1

        cpu_payload = {
            "usage_percent": round(cpu_percent_actual, 1),
            "usage_percent_est": round(cpu_percent_actual, 1),
            "load_1m": round(load1, 2),
            "load_5m": round(load5, 2),
            "load_15m": round(load15, 2),
            "core_count": cpu_count,
        }
        memory_payload = {
            "total_mb": round(mem_total_kb / 1024, 1),
            "used_mb": round(mem_used_kb / 1024, 1),
            "used_percent": mem_used_percent,
        }
        disk_payload = {
            "total_gb": round(disk.total / (1024**3), 1),
            "used_gb": round(disk.used / (1024**3), 1),
            "used_percent": disk_used_percent,
        }

        return {
            "cpu": cpu_payload,
            "memory": memory_payload,
            "disk": disk_payload,
            "disk_root": disk_payload,
        }

    def check_resource_threshold(self, resources: dict[str, Any]) -> bool:
        """Check if any resource exceeds the configured threshold."""
        cpu_percent = float(resources.get("cpu", {}).get("usage_percent", 0.0))
        memory_percent = float(resources.get("memory", {}).get("used_percent", 0.0))
        disk_percent = float((resources.get("disk", {}) or resources.get("disk_root", {})).get("used_percent", 0.0))
        return (
            cpu_percent >= self.cpu_threshold
            or memory_percent >= self.memory_threshold
            or disk_percent >= self.disk_threshold
        )
