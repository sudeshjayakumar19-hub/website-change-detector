"""Security utilities for URL validation and SSRF protection."""

import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit


class SSRFValidationError(ValueError):
    """Raised when a URL fails public-network or SSRF validation."""


FORBIDDEN_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "broadcasthost",
    "0.0.0.0",
    "metadata.google.internal",
    "metadata",
    "instance-data",
}

FORBIDDEN_HOSTNAME_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".intranet",
    ".corp",
    ".home.arpa",
    ".localdomain",
    ".lan",
)

CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def is_ip_public(ip_str: str) -> bool:
    """Return True only if the IP address is a globally routable public address."""
    try:
        ip_obj = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    # Handle IPv4-mapped IPv6 addresses (e.g., ::ffff:127.0.0.1)
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        ip_obj = ip_obj.ipv4_mapped

    if (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    ):
        return False

    if isinstance(ip_obj, ipaddress.IPv4Address) and ip_obj in CGNAT_NETWORK:
        return False

    return bool(ip_obj.is_global)


def validate_public_url(url: str, *, require_dns_resolution: bool = False) -> str:
    """Validate and normalize a URL, ensuring it points to a public HTTP/HTTPS destination.

    Raises SSRFValidationError if the URL is malformed, uses a disallowed scheme,
    contains credentials, or targets localhost/private/internal networks.
    """
    if not url or not isinstance(url, str):
        raise SSRFValidationError("URL must be a non-empty string.")

    cleaned = url.strip()
    if len(cleaned) > 2048:
        raise SSRFValidationError("URL exceeds maximum length of 2048 characters.")

    try:
        parsed = urlsplit(cleaned)
    except Exception as exc:
        raise SSRFValidationError(f"Invalid URL syntax: {exc}") from exc

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise SSRFValidationError("Only http:// and https:// URLs are permitted.")

    if parsed.username or parsed.password:
        raise SSRFValidationError("URLs containing credentials or userinfo are not allowed.")

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise SSRFValidationError("URL must include a valid hostname.")

    if hostname in FORBIDDEN_HOSTNAMES or hostname.endswith(FORBIDDEN_HOSTNAME_SUFFIXES):
        raise SSRFValidationError(
            f"Target host '{hostname}' is internal or loopback and cannot be monitored."
        )

    # Check if hostname is an IP literal (including octal/hex/integer IPv4 forms)
    try:
        ip_obj = ipaddress.ip_address(hostname)
        if not is_ip_public(str(ip_obj)):
            raise SSRFValidationError(
                f"IP address '{hostname}' belongs to a private, loopback, or reserved range."
            )
    except ValueError as exc:
        if isinstance(exc, SSRFValidationError):
            raise
        # Reject integer/hex/octal IP representations via socket.inet_aton if applicable
        if all(part.isdigit() or part.startswith("0x") for part in hostname.split(".")):
            try:
                packed = socket.inet_aton(hostname)
                canonical_ip = socket.inet_ntoa(packed)
                if not is_ip_public(canonical_ip):
                    raise SSRFValidationError(
                        f"IP address '{hostname}' resolves to non-public range '{canonical_ip}'."
                    )
            except OSError:
                raise SSRFValidationError(f"Invalid IP address format: '{hostname}'.") from None

        # Domain must contain at least one dot and valid TLD characters
        if "." not in hostname or len(hostname.split(".")[-1]) < 2:
            raise SSRFValidationError(f"Hostname '{hostname}' is not a valid public domain.")

    # Validate port if specified
    try:
        port = parsed.port
    except ValueError as exc:
        raise SSRFValidationError(f"Invalid port in URL: {exc}") from exc

    if port is not None and (port < 1 or port > 65535):
        raise SSRFValidationError("Port must be between 1 and 65535.")

    # Attempt DNS resolution check to catch domains pointing to internal IPs (DNS rebinding / local records)
    try:
        addr_info = socket.getaddrinfo(hostname, port or (443 if scheme == "https" else 80))
        resolved_ips = {info[4][0] for info in addr_info if info and len(info) >= 5 and info[4]}
        for ip_str in resolved_ips:
            # Strip IPv6 scope ID if present
            clean_ip = ip_str.split("%")[0]
            if not is_ip_public(clean_ip):
                raise SSRFValidationError(
                    f"Hostname '{hostname}' resolves to non-public IP address '{clean_ip}'."
                )
    except socket.gaierror as exc:
        if require_dns_resolution:
            raise SSRFValidationError(f"Could not resolve hostname '{hostname}': {exc}") from exc

    path = parsed.path if parsed.path else "/"
    # Strip URL fragments (#...) as they are never sent to servers
    normalized = urlunsplit((scheme, parsed.netloc, path, parsed.query, ""))
    return normalized
