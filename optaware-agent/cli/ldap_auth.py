"""OpenLDAP authentication for OptAware portal.

Provides LDAP bind authentication against a local or remote OpenLDAP server.
Used as an alternative (or complement) to API-key auth for the web portal.

Typical flow:
  1. User submits username + password via ``POST /api/auth/login``.
  2. ``authenticate(username, password)`` attempts an LDAP simple bind.
  3. On success, a JWT is issued (via ``cli.auth.create_token``).
  4. Subsequent requests include the JWT in the ``Authorization: Bearer`` header.

Requires the ``ldap3`` library (``pip install ldap3``).  When ldap3 is not
installed, all functions raise ``LDAPNotAvailable``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


class LDAPNotAvailable(RuntimeError):
    """Raised when the ldap3 library is not installed."""


class LDAPAuthError(Exception):
    """Raised on authentication failures (bad credentials, unreachable server)."""


@dataclass(frozen=True)
class LDAPUser:
    """Authenticated user information returned after a successful bind."""

    username: str
    dn: str
    display_name: str = ""
    email: str = ""
    groups: tuple[str, ...] = ()


def _get_ldap_config() -> dict:
    """Load LDAP settings from the OptAware config singleton."""
    try:
        from config.loader import get_config  # noqa: PLC0415

        cfg = get_config()
        auth = cfg.auth if hasattr(cfg, "auth") else None
        if auth is None:
            return {}
        return {
            "enabled": getattr(auth, "ldap_enabled", False),
            "server": getattr(auth, "ldap_server", "ldap://localhost"),
            "port": getattr(auth, "ldap_port", 389),
            "use_ssl": getattr(auth, "ldap_use_ssl", False),
            "base_dn": getattr(auth, "ldap_base_dn", ""),
            "bind_dn_template": getattr(auth, "ldap_bind_dn_template", "uid={username},ou=users,{base_dn}"),
            "search_base": getattr(auth, "ldap_search_base", ""),
            "search_filter": getattr(auth, "ldap_search_filter", "(uid={username})"),
            "group_base": getattr(auth, "ldap_group_base", ""),
            "group_filter": getattr(auth, "ldap_group_filter", "(memberUid={username})"),
            "required_group": getattr(auth, "ldap_required_group", ""),
            "ca_cert_file": getattr(auth, "ldap_ca_cert_file", ""),
        }
    except Exception:
        return {}


def is_ldap_enabled() -> bool:
    """Return True if LDAP authentication is enabled in the config."""
    cfg = _get_ldap_config()
    return bool(cfg.get("enabled"))


def authenticate(username: str, password: str) -> LDAPUser:
    """Authenticate a user against the configured OpenLDAP server.

    Performs a simple LDAP bind with the user's credentials, then searches
    for display name, email, and group memberships.

    Args:
        username: The uid/login name.
        password: The user's plaintext password (sent over TLS if configured).

    Returns:
        An :class:`LDAPUser` on success.

    Raises:
        LDAPNotAvailable: If ldap3 is not installed.
        LDAPAuthError: On bind failure or missing required group.
    """
    try:
        import ldap3  # noqa: PLC0415
        from ldap3.core.exceptions import LDAPException  # noqa: PLC0415
    except ImportError:
        raise LDAPNotAvailable(
            "The ldap3 library is required for LDAP authentication. "
            "Install it with: pip install ldap3"
        )

    if not username or not password:
        raise LDAPAuthError("Username and password are required.")

    cfg = _get_ldap_config()
    if not cfg.get("enabled"):
        raise LDAPAuthError("LDAP authentication is not enabled.")

    server_url = cfg["server"]
    port = cfg["port"]
    use_ssl = cfg["use_ssl"]
    base_dn = cfg["base_dn"]

    if not base_dn:
        raise LDAPAuthError("LDAP base_dn is not configured.")

    # Build the user's bind DN
    bind_dn_template = cfg.get("bind_dn_template", "uid={username},ou=users,{base_dn}")
    bind_dn = bind_dn_template.format(username=username, base_dn=base_dn)

    # Configure TLS if needed
    tls = None
    if use_ssl or server_url.startswith("ldaps://"):
        ca_cert = cfg.get("ca_cert_file", "")
        tls = ldap3.Tls(
            validate=ldap3.ssl.CERT_REQUIRED if ca_cert else ldap3.ssl.CERT_NONE,
            ca_certs_file=ca_cert or None,
        )

    try:
        server = ldap3.Server(
            server_url,
            port=port,
            use_ssl=use_ssl,
            tls=tls,
            get_info=ldap3.NONE,
            connect_timeout=10,
        )

        # Attempt bind with user credentials
        conn = ldap3.Connection(
            server,
            user=bind_dn,
            password=password,
            auto_bind=True,
            raise_exceptions=True,
            receive_timeout=10,
        )
    except LDAPException as exc:
        logger.warning("LDAP bind failed for user %s: %s", username, exc)
        raise LDAPAuthError(f"Authentication failed for user '{username}'.") from exc

    # Bind succeeded — fetch user attributes
    display_name = ""
    email = ""
    groups: list[str] = []

    try:
        search_base = cfg.get("search_base") or base_dn
        search_filter = cfg.get("search_filter", "(uid={username})").format(
            username=ldap3.utils.conv.escape_filter_chars(username),
        )
        conn.search(
            search_base,
            search_filter,
            attributes=["cn", "displayName", "mail", "memberOf"],
        )
        if conn.entries:
            entry = conn.entries[0]
            display_name = str(entry.displayName) if hasattr(entry, "displayName") else ""
            if not display_name:
                display_name = str(entry.cn) if hasattr(entry, "cn") else username
            email = str(entry.mail) if hasattr(entry, "mail") else ""
            if hasattr(entry, "memberOf"):
                groups = [str(g) for g in entry.memberOf]
    except LDAPException as exc:
        logger.debug("LDAP attribute search failed for %s: %s", username, exc)

    # Group membership search (for posixGroup style)
    if not groups:
        try:
            group_base = cfg.get("group_base") or base_dn
            group_filter = cfg.get("group_filter", "(memberUid={username})").format(
                username=ldap3.utils.conv.escape_filter_chars(username),
            )
            conn.search(group_base, group_filter, attributes=["cn"])
            groups = [str(e.cn) for e in conn.entries if hasattr(e, "cn")]
        except LDAPException:
            pass

    # Check required group if configured
    required_group = cfg.get("required_group", "")
    if required_group:
        group_names = [_extract_cn(g) for g in groups]
        if required_group not in group_names and required_group not in groups:
            conn.unbind()
            raise LDAPAuthError(
                f"User '{username}' is not a member of required group '{required_group}'."
            )

    conn.unbind()

    logger.info("LDAP authentication successful for user %s", username)
    return LDAPUser(
        username=username,
        dn=bind_dn,
        display_name=display_name or username,
        email=email,
        groups=tuple(groups),
    )


def _extract_cn(dn_or_name: str) -> str:
    """Extract the CN value from a DN string, or return as-is."""
    if dn_or_name.lower().startswith("cn="):
        parts = dn_or_name.split(",", 1)
        return parts[0].split("=", 1)[1]
    return dn_or_name


def get_ldap_status() -> dict:
    """Return LDAP configuration status for the API/portal."""
    cfg = _get_ldap_config()
    enabled = bool(cfg.get("enabled"))

    status = {
        "enabled": enabled,
        "server": cfg.get("server", "") if enabled else "",
        "base_dn": cfg.get("base_dn", "") if enabled else "",
        "use_ssl": cfg.get("use_ssl", False) if enabled else False,
        "required_group": cfg.get("required_group", "") if enabled else "",
        "ldap3_installed": False,
    }

    try:
        import ldap3  # noqa: PLC0415, F401
        status["ldap3_installed"] = True
    except ImportError:
        pass

    # Quick connectivity test if enabled
    if enabled and status["ldap3_installed"]:
        try:
            import ldap3  # noqa: PLC0415

            server = ldap3.Server(
                cfg["server"],
                port=cfg["port"],
                use_ssl=cfg.get("use_ssl", False),
                get_info=ldap3.NONE,
                connect_timeout=3,
            )
            conn = ldap3.Connection(server, auto_bind=False, receive_timeout=3)
            reachable = conn.open()
            conn.unbind()
            status["reachable"] = reachable
        except Exception:
            status["reachable"] = False
    else:
        status["reachable"] = None

    return status
