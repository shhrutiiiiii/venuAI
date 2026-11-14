"""SSRF protection: metadata/private/loopback blocked, DNS rebinding, redirects."""
import httpx
import pytest

from VENUAI import net_safety
from VENUAI.net_safety import UnsafeURLError, is_blocked_ip, validate_url


@pytest.mark.parametrize("ip", ["169.254.169.254", "127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1",
                                "0.0.0.0", "::1", "fe80::1", "fd00::1", "::ffff:127.0.0.1", "100.64.0.1", "224.0.0.1"])
def test_blocked_ips(ip):
    assert is_blocked_ip(ip)


def test_public_ip_ok():
    assert not is_blocked_ip("8.8.8.8")
    assert not is_blocked_ip("2606:4700:4700::1111")


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/", "https://169.254.169.254/", "https://localhost/",
    "https://127.0.0.1:8000/", "https://[::1]/", "https://user:pw@example.com/", "file:///etc/passwd",
    "ftp://example.com/", "gopher://x/", "https:///nohost", "https://exa mple.com/", "http://example.com/",
])
def test_blocked_urls(url):
    with pytest.raises(UnsafeURLError):
        validate_url(url)


def test_dns_rebinding_one_bad_record_blocks(monkeypatch):
    monkeypatch.setattr(net_safety, "_resolve", lambda h, p: ["8.8.8.8", "169.254.169.254"])
    with pytest.raises(UnsafeURLError):
        validate_url("https://rebind.example.com/")


def test_public_resolution_allowed(monkeypatch):
    monkeypatch.setattr(net_safety, "_resolve", lambda h, p: ["93.184.216.34"])
    assert validate_url("https://example.com/x")[1] == "example.com"


def test_redirect_to_metadata_is_blocked(monkeypatch):
    def fake_resolve(host, port):
        return ["169.254.169.254"] if host == "169.254.169.254" else ["93.184.216.34"]
    monkeypatch.setattr(net_safety, "_resolve", fake_resolve)

    class FakeClient:
        def __init__(self, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def stream(self, method, url, **k):
            class Resp:
                status_code = 302
                headers = {"location": "https://169.254.169.254/latest/meta-data/"}
                def __enter__(s): return s
                def __exit__(s, *a): return False
            return Resp()
    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(UnsafeURLError):
        net_safety.safe_get("https://example.com/")


def test_response_size_cap(monkeypatch):
    monkeypatch.setattr(net_safety, "_resolve", lambda h, p: ["93.184.216.34"])

    class FakeClient:
        def __init__(self, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def stream(self, method, url, **k):
            class Resp:
                status_code = 200
                headers = {}
                def iter_bytes(s):
                    for _ in range(100):
                        yield b"x" * 1024
                def __enter__(s): return s
                def __exit__(s, *a): return False
            return Resp()
    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(UnsafeURLError):
        net_safety.safe_get("https://example.com/", max_bytes=2048)
    assert net_safety.safe_get("https://example.com/", max_bytes=10**6)[0] == 200


def test_mcp_http_client_rejects_metadata_url():
    from VENUAI.mcp_client import ExternalMCPClient, MCPError
    c = ExternalMCPClient("evil", url="https://169.254.169.254/mcp")
    with pytest.raises(MCPError):
        c._request("initialize", {})
    assert c.connect() is False


def test_mcp_command_allowlist_and_pinning():
    from VENUAI.mcp_client import MCPClientManager
    m = MCPClientManager(config_path="nonexistent.json")
    with pytest.raises(ValueError):
        m.add_server("x", "bash", ["-c", "id"])
    with pytest.raises(ValueError):
        m.add_server("x", "npx", ["-y", "@modelcontextprotocol/server-filesystem"])
    m.add_server("ok", "npx", ["-y", "@modelcontextprotocol/server-filesystem@2025.1.14", "/data"])
    assert "ok" in m.clients
