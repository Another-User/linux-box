"""Metric collector — gathers system metrics via psutil.

Emits Event objects when configurable thresholds are exceeded.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any, Optional

import psutil

from models.events import Event, EventSeverity, EventSource

logger = logging.getLogger(__name__)

# Default alert thresholds (percent)
_DEFAULT_CPU_THRESHOLD = 90.0
_DEFAULT_MEM_THRESHOLD = 90.0
_DEFAULT_DISK_THRESHOLD = 85.0


class MetricCollector:
    """Collect CPU, memory, disk, network and process metrics via psutil.

    Parameters
    ----------
    callback:
        Optional callable invoked with threshold-breach :class:`Event` objects.
    cpu_threshold:
        CPU usage percent that triggers a warning event (default 90).
    mem_threshold:
        Memory usage percent that triggers a warning event (default 90).
    disk_threshold:
        Disk usage percent (per mount) that triggers a warning event (default 85).
    """

    def __init__(
        self,
        callback: Optional[Callable[[Event], None]] = None,
        cpu_threshold: float = _DEFAULT_CPU_THRESHOLD,
        mem_threshold: float = _DEFAULT_MEM_THRESHOLD,
        disk_threshold: float = _DEFAULT_DISK_THRESHOLD,
    ) -> None:
        self._callback = callback
        self._cpu_threshold = cpu_threshold
        self._mem_threshold = mem_threshold
        self._disk_threshold = disk_threshold
        self._running = False

    # ------------------------------------------------------------------
    # Individual collectors
    # ------------------------------------------------------------------

    def collect_cpu(self) -> dict[str, Any]:
        """Return CPU usage percent, load average, and per-core percentages."""
        per_core: list[float] = psutil.cpu_percent(interval=0.1, percpu=True)  # type: ignore[arg-type]
        overall: float = sum(per_core) / len(per_core) if per_core else 0.0

        try:
            load_avg = list(psutil.getloadavg())
        except AttributeError:
            # getloadavg is not available on Windows
            load_avg = [0.0, 0.0, 0.0]

        cpu_freq = psutil.cpu_freq()
        freq_info: dict[str, Any] = {}
        if cpu_freq is not None:
            freq_info = {
                "current_mhz": round(cpu_freq.current, 1),
                "min_mhz": round(cpu_freq.min, 1),
                "max_mhz": round(cpu_freq.max, 1),
            }

        return {
            "usage_percent": round(overall, 2),
            "load_avg_1m": round(load_avg[0], 2),
            "load_avg_5m": round(load_avg[1], 2),
            "load_avg_15m": round(load_avg[2], 2),
            "core_count_logical": psutil.cpu_count(logical=True),
            "core_count_physical": psutil.cpu_count(logical=False),
            "per_core_percent": [round(p, 1) for p in per_core],
            **freq_info,
        }

    def collect_memory(self) -> dict[str, Any]:
        """Return virtual and swap memory statistics."""
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        return {
            "total_bytes": vm.total,
            "available_bytes": vm.available,
            "used_bytes": vm.used,
            "free_bytes": vm.free,
            "percent": round(vm.percent, 2),
            "buffers_bytes": getattr(vm, "buffers", 0),
            "cached_bytes": getattr(vm, "cached", 0),
            "swap_total_bytes": sw.total,
            "swap_used_bytes": sw.used,
            "swap_free_bytes": sw.free,
            "swap_percent": round(sw.percent, 2),
        }

    def collect_disk(self) -> dict[str, Any]:
        """Return per-mount usage and aggregate I/O counters."""
        partitions: list[dict[str, Any]] = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except PermissionError:
                continue
            partitions.append(
                {
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "percent": round(usage.percent, 2),
                }
            )

        io_counters: dict[str, Any] = {}
        try:
            disk_io = psutil.disk_io_counters(perdisk=True)
            if disk_io:
                io_counters = {
                    dev: {
                        "read_count": c.read_count,
                        "write_count": c.write_count,
                        "read_bytes": c.read_bytes,
                        "write_bytes": c.write_bytes,
                        "read_time_ms": c.read_time,
                        "write_time_ms": c.write_time,
                    }
                    for dev, c in disk_io.items()
                }
        except Exception as exc:
            logger.debug("collect_disk: io_counters unavailable: %s", exc)

        return {
            "partitions": partitions,
            "io_counters": io_counters,
        }

    def collect_network(self) -> dict[str, Any]:
        """Return bytes sent/received, connections, and per-interface stats."""
        net_io = psutil.net_io_counters(pernic=True)
        interfaces: dict[str, Any] = {}
        for iface, counters in net_io.items():
            interfaces[iface] = {
                "bytes_sent": counters.bytes_sent,
                "bytes_recv": counters.bytes_recv,
                "packets_sent": counters.packets_sent,
                "packets_recv": counters.packets_recv,
                "errin": counters.errin,
                "errout": counters.errout,
                "dropin": counters.dropin,
                "dropout": counters.dropout,
            }

        # Aggregate totals
        totals = psutil.net_io_counters(pernic=False)
        agg: dict[str, Any] = (
            {
                "bytes_sent": totals.bytes_sent,
                "bytes_recv": totals.bytes_recv,
                "packets_sent": totals.packets_sent,
                "packets_recv": totals.packets_recv,
            }
            if totals
            else {}
        )

        try:
            connections = psutil.net_connections(kind="inet")
            conn_count = len(connections)
        except Exception:
            conn_count = -1

        return {
            "aggregate": agg,
            "interfaces": interfaces,
            "connection_count": conn_count,
        }

    def collect_processes(self) -> dict[str, Any]:
        """Return top-10 processes by CPU and memory, plus zombie count."""
        procs: list[dict[str, Any]] = []
        zombie_count = 0

        for proc in psutil.process_iter(
            ["pid", "name", "cpu_percent", "memory_percent", "status", "username"]
        ):
            try:
                info = proc.info  # type: ignore[attr-defined]
                if info.get("status") == psutil.STATUS_ZOMBIE:
                    zombie_count += 1
                procs.append(
                    {
                        "pid": info.get("pid"),
                        "name": info.get("name", ""),
                        "cpu_percent": round(info.get("cpu_percent") or 0.0, 2),
                        "memory_percent": round(
                            info.get("memory_percent") or 0.0, 4
                        ),
                        "status": info.get("status", ""),
                        "username": info.get("username", ""),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        top_cpu = sorted(procs, key=lambda p: p["cpu_percent"], reverse=True)[:10]
        top_mem = sorted(
            procs, key=lambda p: p["memory_percent"], reverse=True
        )[:10]

        return {
            "top_by_cpu": top_cpu,
            "top_by_memory": top_mem,
            "zombie_count": zombie_count,
            "total_count": len(procs),
        }

    def collect_all(self) -> dict[str, Any]:
        """Return all metric categories merged into one dict."""
        return {
            "timestamp": time.time(),
            "cpu": self.collect_cpu(),
            "memory": self.collect_memory(),
            "disk": self.collect_disk(),
            "network": self.collect_network(),
            "processes": self.collect_processes(),
        }

    # ------------------------------------------------------------------
    # Periodic collection loop
    # ------------------------------------------------------------------

    async def run_collection_loop(self, interval: int = 30) -> None:
        """Collect all metrics every *interval* seconds and check thresholds."""
        self._running = True
        logger.info("MetricCollector loop started (interval=%ds).", interval)
        while self._running:
            try:
                metrics = self.collect_all()
                self._check_thresholds(metrics)
            except Exception as exc:
                logger.error("MetricCollector: collection error: %s", exc)
            await asyncio.sleep(interval)

    def stop(self) -> None:
        """Signal the collection loop to stop after the current sleep."""
        self._running = False

    # ------------------------------------------------------------------
    # Threshold checks
    # ------------------------------------------------------------------

    def _emit(self, event: Event) -> None:
        if self._callback is not None:
            self._callback(event)

    def _check_thresholds(self, metrics: dict[str, Any]) -> None:
        cpu_pct: float = metrics["cpu"].get("usage_percent", 0.0)
        if cpu_pct >= self._cpu_threshold:
            self._emit(
                Event(
                    severity=EventSeverity.warning
                    if cpu_pct < 95.0
                    else EventSeverity.critical,
                    source=EventSource.metric_collector,
                    message=f"CPU usage is {cpu_pct:.1f}% (threshold {self._cpu_threshold:.0f}%)",
                    details=metrics["cpu"],
                )
            )

        mem_pct: float = metrics["memory"].get("percent", 0.0)
        if mem_pct >= self._mem_threshold:
            self._emit(
                Event(
                    severity=EventSeverity.warning
                    if mem_pct < 95.0
                    else EventSeverity.critical,
                    source=EventSource.metric_collector,
                    message=f"Memory usage is {mem_pct:.1f}% (threshold {self._mem_threshold:.0f}%)",
                    details=metrics["memory"],
                )
            )

        for partition in metrics["disk"].get("partitions", []):
            disk_pct: float = partition.get("percent", 0.0)
            if disk_pct >= self._disk_threshold:
                self._emit(
                    Event(
                        severity=EventSeverity.warning
                        if disk_pct < 95.0
                        else EventSeverity.critical,
                        source=EventSource.metric_collector,
                        message=(
                            f"Disk usage on {partition['mountpoint']} is {disk_pct:.1f}%"
                            f" (threshold {self._disk_threshold:.0f}%)"
                        ),
                        details=partition,
                    )
                )

        zombie_count: int = metrics["processes"].get("zombie_count", 0)
        if zombie_count > 0:
            self._emit(
                Event(
                    severity=EventSeverity.warning,
                    source=EventSource.metric_collector,
                    message=f"Detected {zombie_count} zombie process(es)",
                    details={"zombie_count": zombie_count},
                )
            )
