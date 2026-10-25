from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    pass


def parse_http_url(raw: str):
    value = (raw or "").strip()
    if not value:
        raise UnsafeUrlError("URL is empty.")
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError("Only http:// and https:// URLs are supported.")
    if not parsed.hostname:
        raise UnsafeUrlError("URL has no hostname.")
    if parsed.username or parsed.password:
        # Allowed, but flagged by the scanner. Do not reject.
        pass
    return parsed


def resolve_public_host(host: str, port: int | None = None) -> list[str]:
    """Resolve a hostname and reject private/loopback/link-local targets.

    This prevents the local server from being used as a gateway to the user's
    LAN or cloud metadata endpoints. DNS rebinding is still possible in a demo;
    for production, pin the resolved IP in the HTTP transport.
    """
    host = (host or "").strip().strip("[]").lower().rstrip(".")
    if not host:
        raise UnsafeUrlError("Hostname is empty.")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(
        (".local", ".internal", ".localhost", ".home.arpa")
    ):
        raise UnsafeUrlError("Local/private hostnames are blocked.")

    try:
        infos = socket.getaddrinfo(
            host, port or 443, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"DNS resolution failed: {exc}") from exc

    ips: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in ips:
            ips.append(ip)

    if not ips:
        raise UnsafeUrlError("Hostname did not resolve to an IP address.")

    for ip in ips:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError as exc:
            raise UnsafeUrlError(f"Invalid resolved IP: {ip}") from exc
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
            or not address.is_global
        ):
            raise UnsafeUrlError(
                "Target resolves to a private, loopback, or reserved address."
            )
    return ips
