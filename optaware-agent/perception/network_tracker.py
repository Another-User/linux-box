"""Network tracker — monitor active connections and detect suspicious activity.

Uses psutil to enumerate TCP/UDP connections and listening ports, resolve
owning processes, and flag connections on unexpected ports.
"""

from __future__ import annotations

import logging
import socket
from collections import Counter
from typing import Any, Optional

import psutil

logger = logging.getLogger(__name__)

# Well-known ports considered "normal" by default.
_DEFAULT_KNOWN_PORTS: set[int] = {
    20, 21,          # FTP
    22,              # SSH
    25, 587, 465,    # SMTP / submission
    53,              # DNS
    67, 68,          # DHCP
    80, 443, 8080, 8443,  # HTTP / HTTPS
    110, 143, 993, 995,   # POP3 / IMAP
    123,             # NTP
    161, 162,        # SNMP
    389, 636,        # LDAP / LDAPS
    3000,            # misc dev / Grafana
    3306,            # MySQL
    5432,            # PostgreSQL
    5672, 15672,     # RabbitMQ
    6333, 6334,      # Qdrant
    6379,            # Redis
    8086,            # InfluxDB
    9090,            # Prometheus
    9100,            # Node Exporter
    27017,           # MongoDB
}


def _family_name(family: int) -> str:
    try:
        return socket.AddressFamily(family).name
    except ValueError:
        return str(family)


def _type_name(sock_type: int) -> str:
    try:
        return socket.SocketKind(sock_type).name
    except ValueError:
        return str(sock_type)


def _process_info(pid: Optional[int]) -> dict[str, Any]:
    """Safely resolve process name, username, and cmdline from *pid*."""
    if pid is None:
        return {"name": "", "username": "", "cmdline": ""}
    try:
        proc = psutil.Process(pid)
        return {
            "name": proc.name(),
            "username": proc.username(),
            "cmdline": " ".join(proc.cmdline())[:200],
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return {"name": "", "username": "", "cmdline": ""}


class NetworkTracker:
    """Track and analyse network connections using psutil.

    All methods are synchronous; call them directly or schedule them from
    an asyncio context via ``loop.run_in_executor``.
    """

    # ------------------------------------------------------------------
    # Connection enumeration
    # ------------------------------------------------------------------

    def get_connections(self) -> list[dict[str, Any]]:
        """Return all active network connections with process information.

        Each entry contains:

        * ``fd``, ``family``, ``type``
        * ``local_addr``, ``local_port``
        * ``remote_addr``, ``remote_port``
        * ``status``
        * ``pid``, ``process``, ``username``, ``cmdline``
        """
        results: list[dict[str, Any]] = []
        try:
            raw_connections = psutil.net_connections(kind="inet")
        except psutil.AccessDenied:
            logger.warning("NetworkTracker: insufficient privileges for net_connections")
            return results

        for conn in raw_connections:
            local_ip = conn.laddr.ip if conn.laddr else ""
            local_port = conn.laddr.port if conn.laddr else 0
            remote_ip = conn.raddr.ip if conn.raddr else ""
            remote_port = conn.raddr.port if conn.raddr else 0

            proc = _process_info(conn.pid)
            entry: dict[str, Any] = {
                "fd": conn.fd,
                "family": _family_name(conn.family),
                "type": _type_name(conn.type),
                "local_addr": f"{local_ip}:{local_port}" if local_ip else "",
                "local_port": local_port,
                "remote_addr": f"{remote_ip}:{remote_port}" if remote_ip else "",
                "remote_port": remote_port,
                "status": conn.status if hasattr(conn, "status") else "",
                "pid": conn.pid,
                "process": proc["name"],
                "username": proc["username"],
                "cmdline": proc["cmdline"],
            }
            results.append(entry)

        return results

    def get_listening_ports(self) -> list[dict[str, Any]]:
        """Return all currently listening TCP ports with owning process info.

        Each entry contains:

        * ``port``, ``address``, ``family``
        * ``pid``, ``process``, ``username``, ``cmdline``
        """
        listeners: list[dict[str, Any]] = []
        try:
            raw_connections = psutil.net_connections(kind="inet")
        except psutil.AccessDenied:
            logger.warning("NetworkTracker: insufficient privileges for net_connections")
            return listeners

        for conn in raw_connections:
            if conn.status != psutil.CONN_LISTEN:
                continue
            proc = _process_info(conn.pid)
            listeners.append(
                {
                    "port": conn.laddr.port if conn.laddr else 0,
                    "address": conn.laddr.ip if conn.laddr else "",
                    "family": _family_name(conn.family),
                    "pid": conn.pid,
                    "process": proc["name"],
                    "username": proc["username"],
                    "cmdline": proc["cmdline"],
                }
            )

        # Sort by port number for stable output
        listeners.sort(key=lambda e: e["port"])
        return listeners

    # ------------------------------------------------------------------
    # Suspicious connection detection
    # ------------------------------------------------------------------

    def detect_suspicious(self, known_ports: set[int] = _DEFAULT_KNOWN_PORTS) -> list[dict[str, Any]]:
        """Return connections that appear suspicious relative to *known_ports*.

        A connection is flagged when:

        * A TCP socket is **listening** on a port not in *known_ports*.
        * An **established** outbound connection targets a port in the
          IANA dynamic/private range (49152–65535).
        * Any connection is owned by ``root`` (uid=0) on a non-standard port.

        Each returned dict contains ``type``, ``reason``, and full
        connection/process details.
        """
        suspicious: list[dict[str, Any]] = []
        try:
            raw_connections = psutil.net_connections(kind="inet")
        except psutil.AccessDenied:
            logger.warning("NetworkTracker: insufficient privileges for net_connections")
            return suspicious

        for conn in raw_connections:
            local_port = conn.laddr.port if conn.laddr else 0
            remote_port = conn.raddr.port if conn.raddr else 0
            proc = _process_info(conn.pid)

            # Unknown listening port
            if conn.status == psutil.CONN_LISTEN and local_port:
                if local_port not in known_ports:
                    suspicious.append(
                        {
                            "type": "unknown_listening_port",
                            "reason": f"Unexpected listening port {local_port} "
                                      f"(process: {proc['name'] or 'unknown'})",
                            "port": local_port,
                            "address": conn.laddr.ip if conn.laddr else "",
                            "pid": conn.pid,
                            "process": proc["name"],
                            "username": proc["username"],
                            "cmdline": proc["cmdline"],
                        }
                    )

            # Established connection to a dynamic/private remote port
            elif conn.status == psutil.CONN_ESTABLISHED and conn.raddr:
                if remote_port >= 49152:
                    suspicious.append(
                        {
                            "type": "high_port_connection",
                            "reason": f"Connection to high/dynamic remote port {remote_port} "
                                      f"(process: {proc['name'] or 'unknown'})",
                            "remote_addr": f"{conn.raddr.ip}:{remote_port}",
                            "remote_port": remote_port,
                            "local_addr": f"{conn.laddr.ip}:{local_port}"
                            if conn.laddr
                            else "",
                            "local_port": local_port,
                            "pid": conn.pid,
                            "process": proc["name"],
                            "username": proc["username"],
                            "cmdline": proc["cmdline"],
                        }
                    )

        if suspicious:
            logger.warning(
                "NetworkTracker: %d suspicious connection(s) found.", len(suspicious)
            )
        return suspicious

    # ------------------------------------------------------------------
    # Summary statistics
    # ------------------------------------------------------------------

    def get_connection_summary(self) -> dict[str, Any]:
        """Return connection counts grouped by TCP state.

        Keys include ``total``, ``by_state``, and convenient aliases for
        common states: ``listening``, ``established``, ``time_wait``,
        ``close_wait``.
        """
        try:
            connections = psutil.net_connections(kind="inet")
        except psutil.AccessDenied:
            return {
                "total": 0,
                "by_state": {},
                "listening": 0,
                "established": 0,
                "time_wait": 0,
                "close_wait": 0,
                "error": "insufficient privileges",
            }

        state_counts: Counter[str] = Counter(
            (conn.status or "NONE") for conn in connections
        )

        return {
            "total": len(connections),
            "by_state": dict(state_counts),
            "listening": state_counts.get("LISTEN", 0),
            "established": state_counts.get("ESTABLISHED", 0),
            "time_wait": state_counts.get("TIME_WAIT", 0),
            "close_wait": state_counts.get("CLOSE_WAIT", 0),
        }

    def get_interface_stats(self) -> dict[str, Any]:
        """Return per-NIC I/O counters (bytes, packets, errors, drops)."""
        stats: dict[str, Any] = {}
        for iface, c in psutil.net_io_counters(pernic=True).items():
            stats[iface] = {
                "bytes_sent": c.bytes_sent,
                "bytes_recv": c.bytes_recv,
                "packets_sent": c.packets_sent,
                "packets_recv": c.packets_recv,
                "errin": c.errin,
                "errout": c.errout,
                "dropin": c.dropin,
                "dropout": c.dropout,
            }
        return stats
