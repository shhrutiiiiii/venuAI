"""
SSRF protection for every outbound URL fetch (web-search tool, MCP HTTP client,
OAuth endpoints, any future fetcher).

Rules enforced by `validate_url` / `safe_get`:
  * https only (http only when explicitly allowed by the caller, e.g. dev MCP)
  * no credentials in the URL, no empty host
  * EVERY address the host resolves to must be public: private, loopback,
    link-local (incl. cloud metadata 169.254.169.254), multicast, reserved,
    unspecified, CGNAT and IPv4-mapped IPv6 forms are blocked
  * DNS-rebinding defence: `safe_get` connects to the already-validated IP
    (SNI/Host preserved) instead of resolving a second time
  * redirects are never followed automatically; each hop is re-validated
  * response size and wall-clock time are capped
"""

import ipaddress
import logging
import os
import socket
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

logger = logging.getLogger("drdo-rag")

MAX_RESPONSE_BYTES = int(os.getenv("NET_SAFETY_MAX_BYTES", str(5 * 1024 * 1024)))
DEFAULT_TIMEOUT = float(os.getenv("NET_SAFETY_TIMEOUT", "10"))
MAX_REDIRECTS = 3

_EXTRA_BLOCKED = [ipaddress.ip_network("100.64.0.0/10")]  # CGNAT


class UnsafeURLError(ValueError):
    """The URL (or where it resolves/redirects to) is not allowed."""


def is_blocked_ip(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text.split("%")[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv6Address) and ip.sixtofour:
        ip = ip.sixtofour
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified):
        return True
    return any(ip in net for net in _EXTRA_BLOCKED if net.version == ip.version)


def _resolve(host: str, port: int) -> List[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({i[4][0] for i in infos})


def validate_url(url: str, *, allow_http: bool = False,
                 allowed_hosts: Optional[Iterable[str]] = None) -> Tuple[str, str, int, List[str]]:
    """Return (scheme, host, port, resolved_ips) or raise UnsafeURLError."""
    if not isinstance(url, str) or not url or len(url) > 2048 or any(c in url for c in "\r\n\x00 "):
        raise UnsafeURLError("invalid URL")
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in (("https", "http") if allow_http else ("https",)):
        raise UnsafeURLError("URL scheme not allowed")
    if parts.username or parts.password:
        raise UnsafeURLError("credentials in URL not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise UnsafeURLError("invalid port")
    if allowed_hosts is not None and host.lower() not in {h.lower() for h in allowed_hosts}:
        raise UnsafeURLError("host not in allowlist")
    # IP literal or hostname: resolve either way so every address is checked.
    try:
        ips = _resolve(host, port)
    except OSError:
        raise UnsafeURLError("host could not be resolved")
    if not ips:
        raise UnsafeURLError("host could not be resolved")
    for ip in ips:
        if is_blocked_ip(ip):
            logger.warning("[net_safety] blocked %s -> %s", host, ip)
            raise UnsafeURLError("URL resolves to a non-public address")
    return scheme, host, port, ips


def safe_get(url: str, *, allow_http: bool = False, allowed_hosts: Optional[Iterable[str]] = None,
             timeout: float = DEFAULT_TIMEOUT, max_bytes: int = MAX_RESPONSE_BYTES,
             headers: Optional[dict] = None) -> Tuple[int, dict, bytes]:
    """GET `url` with SSRF protection. Returns (status, headers, body). Raises UnsafeURLError."""
    import httpx

    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        scheme, host, port, ips = validate_url(current, allow_http=allow_http, allowed_hosts=allowed_hosts)
        parts = urlsplit(current)
        ip = ips[0]
        ip_host = f"[{ip}]" if ":" in ip else ip
        target = parts._replace(netloc=f"{ip_host}:{port}").geturl()  # connect to the validated IP
        req_headers = {**(headers or {}), "Host": host if port in (80, 443) else f"{host}:{port}"}
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", target, headers=req_headers,
                               extensions={"sni_hostname": host}) as resp:
                if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                    current = urljoin(current, resp.headers["location"])
                    continue
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise UnsafeURLError("response too large")
                return resp.status_code, dict(resp.headers), bytes(body)
    raise UnsafeURLError("too many redirects")
