"""
Trusted client-IP resolution (S1 <-> S4 contract).

The Next.js proxy forwards the real browser IP in `X-Forwarded-For` and proves
it is the proxy by sending `X-VenuAI-Proxy-Secret` (shared secret, env
TRUSTED_PROXY_SECRET, constant-time compared). Without a valid secret the
header is ignored and the socket peer address is used, so a direct caller
cannot spoof its IP to dodge per-IP rate limits or lockouts. Deployments without the Next
proxy can instead set TRUSTED_PROXY (list of proxy IPs) - see http_security.trusted_proxy_client_ip.
"""

import hmac
import os

PROXY_SECRET_HEADER = "x-venuai-proxy-secret"


def trusted_client_ip(request) -> str:
    peer = request.client.host if getattr(request, "client", None) else "unknown"
    secret = os.getenv("TRUSTED_PROXY_SECRET", "").strip()
    supplied = request.headers.get(PROXY_SECRET_HEADER, "")
    if secret and supplied and hmac.compare_digest(secret.encode(), supplied.encode()):
        xff = request.headers.get("x-forwarded-for", "")
        first = xff.split(",")[0].strip()
        if first and len(first) <= 64:
            return first
    # No valid proxy secret: fall back to the TRUSTED_PROXY peer-list semantics (S4), which
    # return the socket peer unless the peer itself is a configured trusted proxy.
    if hasattr(request, "scope"):
        from VENUAI.http_security import trusted_proxy_client_ip
        return trusted_proxy_client_ip(request)
    return peer
