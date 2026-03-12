"""Service implementations — plugins for managing specific Linux services."""

from services.implementations.base import BaseServicePlugin
from services.implementations.dns import DNSPlugin
from services.implementations.dhcp import DHCPPlugin
from services.implementations.firewall import FirewallPlugin
from services.implementations.vpn import VPNPlugin
from services.implementations.web import WebPlugin
from services.implementations.mail import MailPlugin
from services.implementations.database import PostgreSQLPlugin, MySQLPlugin
from services.implementations.monitoring import PrometheusPlugin, GrafanaPlugin
from services.implementations.backup import BackupPlugin
from services.implementations.security import SSHPlugin, Fail2banPlugin, CertificatesPlugin
from services.implementations.network import NTPPlugin, SambaPlugin, NFSPlugin

PLUGIN_REGISTRY: dict[str, type[BaseServicePlugin]] = {
    "dns": DNSPlugin,
    "dhcp": DHCPPlugin,
    "firewall": FirewallPlugin,
    "vpn": VPNPlugin,
    "web": WebPlugin,
    "mail_smtp": MailPlugin,
    "mail_imap": MailPlugin,
    "database_pg": PostgreSQLPlugin,
    "mysql": MySQLPlugin,
    "monitoring": PrometheusPlugin,
    "grafana": GrafanaPlugin,
    "backup": BackupPlugin,
    "ssh": SSHPlugin,
    "fail2ban": Fail2banPlugin,
    "certificates": CertificatesPlugin,
    "ntp": NTPPlugin,
    "samba": SambaPlugin,
    "nfs": NFSPlugin,
}


def get_plugin(service_name: str) -> BaseServicePlugin | None:
    """Get a service plugin by name."""
    cls = PLUGIN_REGISTRY.get(service_name)
    if cls is None:
        return None
    return cls()


__all__ = [
    "BaseServicePlugin",
    "PLUGIN_REGISTRY",
    "get_plugin",
]
