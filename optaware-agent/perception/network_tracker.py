"""Network connection tracker — monitor connections and detect suspicious activity."""

from __future__ import annotations

import logging
from collections import Counter

import psutil

logger = logging.getLogger("optaware.perception.network_tracker")


class NetworkTracker:
    """Track and analyze network connections."""

    def get_connections(self) -> list[dict]:
        """Get all network connections with process info."""
        connections = []
        for conn in psutil.net_connections(kind="inet"):
            entry = {
                "fd": conn.fd,
                "family": "IPv4" if conn.family.value == 2 else "IPv6",
                "type": "TCP" if conn.type.value == 1 else "UDP",
                "local_addr": f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "",
                "remote_addr": f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "",
                "status": conn.status if hasattr(conn, "status") else "",
                "pid": conn.pid,
                "process": "",
            }
            if conn.pid:
                try:
                    proc = psutil.Process(conn.pid)
                    entry["process"] = proc.name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            connections.append(entry)
        return connections

    def get_listening_ports(self) -> list[dict]:
        """Get all listening ports with process info."""
        listeners = []
        for conn in psutil.net_connections(kind="inet"):
            if conn.status == "LISTEN":
                entry = {
                    "port": conn.laddr.port if conn.laddr else 0,
                    "address": conn.laddr.ip if conn.laddr else "",
                    "pid": conn.pid,
                    "process": "",
                    "family": "IPv4" if conn.family.value == 2 else "IPv6",
                }
                if conn.pid:
                    try:
                        proc = psutil.Process(conn.pid)
                        entry["process"] = proc.name()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                listeners.append(entry)
        return listeners

    def detect_suspicious(self, known_ports: set[int] | None = None) -> list[dict]:
        """Detect connections on unexpected ports or to suspicious destinations."""
        if known_ports is None:
            known_ports = {
                22, 25, 53, 67, 68, 80, 443, 587, 993, 995,
                3000, 3306, 5432, 6333, 8080, 9090, 9100,
            }

        suspicious = []
        for conn in psutil.net_connections(kind="inet"):
            if conn.status == "LISTEN":
                port = conn.laddr.port if conn.laddr else 0
                if port and port not in known_ports and port > 1024:
                    proc_name = ""
                    if conn.pid:
                        try:
                            proc_name = psutil.Process(conn.pid).name()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    suspicious.append({
                        "type": "unknown_listening_port",
                        "port": port,
                        "address": conn.laddr.ip if conn.laddr else "",
                        "pid": conn.pid,
                        "process": proc_name,
                        "reason": f"Unexpected listening port {port}",
                    })

            elif conn.status == "ESTABLISHED" and conn.raddr:
                # Check for connections to unusual high ports
                remote_port = conn.raddr.port
                if remote_port > 49151:  # Dynamic/private port range
                    proc_name = ""
                    if conn.pid:
                        try:
                            proc_name = psutil.Process(conn.pid).name()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    suspicious.append({
                        "type": "high_port_connection",
                        "remote": f"{conn.raddr.ip}:{conn.raddr.port}",
                        "local_port": conn.laddr.port if conn.laddr else 0,
                        "pid": conn.pid,
                        "process": proc_name,
                        "reason": f"Connection to high port {remote_port}",
                    })

        if suspicious:
            logger.warning("Found %d suspicious connections", len(suspicious))
        return suspicious

    def get_connection_summary(self) -> dict:
        """Get summary of connection states."""
        connections = psutil.net_connections(kind="inet")
        state_counts = Counter(
            conn.status for conn in connections if hasattr(conn, "status")
        )

        return {
            "total": len(connections),
            "by_state": dict(state_counts),
            "listening": state_counts.get("LISTEN", 0),
            "established": state_counts.get("ESTABLISHED", 0),
            "time_wait": state_counts.get("TIME_WAIT", 0),
            "close_wait": state_counts.get("CLOSE_WAIT", 0),
        }

    def get_interface_stats(self) -> dict:
        """Get network interface statistics."""
        stats = {}
        counters = psutil.net_io_counters(pernic=True)
        for iface, counters_data in counters.items():
            stats[iface] = {
                "bytes_sent": counters_data.bytes_sent,
                "bytes_recv": counters_data.bytes_recv,
                "packets_sent": counters_data.packets_sent,
                "packets_recv": counters_data.packets_recv,
                "errin": counters_data.errin,
                "errout": counters_data.errout,
                "dropin": counters_data.dropin,
                "dropout": counters_data.dropout,
            }
        return stats
