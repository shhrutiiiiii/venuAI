"""
DRDO VenuAI — MCP Client Manager (MCP spec 2025-11-25)
========================================================
Discovers and consumes external Model Context Protocol servers and exposes
their tools to VenuAI's ReAct loop.

Transports (both real, no simulated results):
  * stdio            - subprocess, newline-delimited JSON-RPC
  * Streamable HTTP  - single endpoint, POST JSON-RPC; handles JSON or SSE
                       responses, ``Mcp-Session-Id`` and the
                       ``MCP-Protocol-Version`` header on every later request.

Security (Wave B / B3):
  * Tool pinning: ``sha256(name + description + inputSchema)`` is stored per
    tool in ``mcp_tool_pins`` (VENUAI/approvals.py). Any drift disables the
    tool (it is not registered) until an admin re-approves via
    ``POST /approvals/pins/approve``.
  * Every call goes through ``agent_tools.guarded_call``: role policy, JSON
    Schema validation against the server's own ``inputSchema``, circuit
    breaker, human approval (default ON for MCP tools; ``TOOL_APPROVAL=off``
    to opt out), timeout, result cap and the tool-call ledger.
  * Tools are built per user (``get_tools``) so approvals/ledger rows carry the
    caller's identity - there is no anonymous global tool list.

Config (VENUAI/mcp_config.json): ``{"mcpServers": {name: {"command", "args",
"env"}}}`` for stdio or ``{"url", "headers"}`` for Streamable HTTP;
``${VAR}`` is expanded from the environment. With no config there are no
external servers (no demo placeholders).
"""

import json
import logging
import os
import re
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any, Dict, List, Optional

logger = logging.getLogger("drdo-rag")

PROTOCOL_VERSION = "2025-11-25"
_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")
_SAFE_ENV_KEYS = ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP", "LANG", "TMPDIR", "APPDATA", "LOCALAPPDATA")
ALLOWED_COMMANDS = {"npx", "node", "python", "python3", "uvx", "uv", "docker"}
_PINNED_PKG_RE = re.compile(r"^(@[a-z0-9._-]+/)?[a-z0-9._-]+@\d+\.\d+\.\d+[A-Za-z0-9._-]*$")


def validate_server_config(name: str, command: str, args: List[str]) -> None:
    """Raise ValueError unless a stdio server config is allowed: command allowlist (basename only,
    no path), and for npx/uvx a version-pinned package (no `@latest`, no unpinned names)."""
    if not command:
        return
    base = os.path.basename(command)
    cmd = base.lower().removesuffix(".exe")
    if base != command or cmd not in ALLOWED_COMMANDS:
        raise ValueError(f"command '{command}' is not allowed for MCP server '{name}'")
    if cmd in ("npx", "uvx"):
        pkgs = [a for a in args if not a.startswith("-")]
        if not pkgs or not _PINNED_PKG_RE.match(pkgs[0].replace("==", "@")):
            raise ValueError(f"MCP server '{name}': package must be pinned to an exact version (pkg@1.2.3)")


def _expand(value: str) -> str:
    return _ENV_RE.sub(lambda m: os.getenv(m.group(1), ""), value)


class MCPError(RuntimeError):
    pass


class ExternalMCPClient:
    """One connection to one external MCP server (stdio or Streamable HTTP)."""

    def __init__(self, name: str, command: str = "", args: Optional[List[str]] = None,
                 url: str = "", headers: Optional[Dict[str, str]] = None,
                 env: Optional[Dict[str, str]] = None, timeout: float = 30.0):
        self.name = name
        self.command = command
        self.args = args or []
        self.url = url
        self.headers = headers or {}
        self.env = env or {}
        self.timeout = timeout
        self.process: Optional[subprocess.Popen] = None
        self.session_id: Optional[str] = None
        self.negotiated_version: str = PROTOCOL_VERSION
        self.server_info: Dict[str, Any] = {}
        self._connected = False
        self._next_id = 0
        self._lock = threading.Lock()
        self._http = None
        self._own_http = False

    @property
    def transport(self) -> str:
        return "http" if self.url else "stdio"

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _http_headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "MCP-Protocol-Version": self.negotiated_version}
        h.update({k: _expand(v) for k, v in self.headers.items()})
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    @staticmethod
    def _parse_http_body(resp, req_id) -> Optional[dict]:
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            data_lines: List[str] = []
            for line in resp.text.splitlines() + [""]:
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif line == "" and data_lines:
                    try:
                        msg = json.loads("\n".join(data_lines))
                    except ValueError:
                        msg = None
                    data_lines = []
                    if isinstance(msg, dict) and msg.get("id") == req_id:
                        return msg
            return None
        return resp.json() if resp.content else None

    def _request(self, method: str, params: Optional[dict] = None) -> Any:
        with self._lock:
            req_id = self._new_id()
            msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
            if params is not None:
                msg["params"] = params
            if self.transport == "http":
                import httpx
                if self._http is None:
                    self._http = httpx.Client(timeout=self.timeout, follow_redirects=False, trust_env=False)
                    self._own_http = True
                if self._own_http:
                    # SSRF guard: re-resolved on EVERY request (DNS rebinding); blocks private/metadata IPs.
                    from VENUAI.net_safety import UnsafeURLError, validate_url
                    try:
                        if os.getenv("MCP_ALLOW_PRIVATE_URLS", "0") == "1":
                            if urlsplit(self.url).scheme not in ("http", "https"):
                                raise UnsafeURLError("URL scheme not allowed")
                        else:
                            validate_url(self.url)
                    except UnsafeURLError as e:
                        raise MCPError(f"MCP server URL rejected: {e}")
                resp = self._http.post(self.url, json=msg, headers=self._http_headers())
                if resp.status_code == 404 and self.session_id:
                    self._connected, self.session_id = False, None
                    raise MCPError("MCP session expired; reconnect required")
                resp.raise_for_status()
                sid = resp.headers.get("mcp-session-id")
                if sid:
                    self.session_id = sid
                reply = self._parse_http_body(resp, req_id)
            else:
                if not self.process or self.process.poll() is not None:
                    raise MCPError("stdio server is not running")
                self.process.stdin.write(json.dumps(msg) + "\n")
                self.process.stdin.flush()
                reply = None
                while True:
                    line = self.process.stdout.readline()
                    if not line:
                        raise MCPError("stdio server closed the stream")
                    try:
                        cand = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(cand, dict) and cand.get("id") == req_id:
                        reply = cand
                        break
            if reply is None:
                raise MCPError(f"no response to {method}")
            if "error" in reply:
                raise MCPError(f"{method}: {reply['error'].get('message', reply['error'])}")
            return reply.get("result")

    def _notify(self, method: str) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        try:
            if self.transport == "http":
                self._http.post(self.url, json=msg, headers=self._http_headers())
            else:
                self.process.stdin.write(json.dumps(msg) + "\n")
                self.process.stdin.flush()
        except Exception as e:
            logger.debug("[MCPClient:%s] notify %s failed: %s", self.name, method, e)

    def connect(self) -> bool:
        """Start/open the transport and perform the MCP initialize handshake."""
        try:
            if self.transport == "stdio":
                if not self.command:
                    raise MCPError("no command or url configured")
                # Minimal environment: never inherit the parent's secrets (GROQ key, JWT secret...).
                env = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}
                env.update({k: _expand(v) for k, v in self.env.items()})
                self.process = subprocess.Popen(
                    [self.command] + self.args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, env=env, bufsize=1)
            result = self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                "clientInfo": {"name": "venuai", "version": "1.0"}})
            self.negotiated_version = (result or {}).get("protocolVersion", PROTOCOL_VERSION)
            self.server_info = (result or {}).get("serverInfo", {})
            self._notify("notifications/initialized")
            self._connected = True
            logger.info("[MCPClient:%s] connected over %s (protocol %s)", self.name, self.transport,
                        self.negotiated_version)
            return True
        except Exception as e:
            logger.error("[MCPClient:%s] connection failed: %s", self.name, e)
            self._connected = False
            self.close()
            return False

    def list_tools(self) -> List[Dict[str, Any]]:
        if not self._connected:
            return []
        tools: List[Dict[str, Any]] = []
        cursor = None
        try:
            for _ in range(20):  # pagination guard
                res = self._request("tools/list", {"cursor": cursor} if cursor else {})
                tools.extend(res.get("tools", []))
                cursor = res.get("nextCursor")
                if not cursor:
                    break
        except Exception as e:
            logger.error("[MCPClient:%s] tools/list failed: %s", self.name, e)
        return tools

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Call a tool for real. Errors are returned as explicit ``Error:`` strings."""
        if not self._connected:
            return f"Error: MCP server '{self.name}' is offline."
        try:
            res = self._request("tools/call", {"name": tool_name, "arguments": arguments})
        except Exception as e:
            return f"Error: MCP call failed: {e}"
        parts = [c.get("text", "") for c in (res or {}).get("content", []) if c.get("type") == "text"]
        text = "\n".join(p for p in parts if p) or json.dumps((res or {}).get("structuredContent", ""))
        return f"Error: {text}" if (res or {}).get("isError") else text

    def close(self) -> None:
        try:
            if self._http is not None:
                if self.session_id:
                    try:
                        self._http.delete(self.url, headers=self._http_headers())
                    except Exception:
                        pass
                self._http.close()
                self._http = None
            if self.process and self.process.poll() is None:
                self.process.terminate()
        finally:
            self._connected = False
            self.session_id = None


def _tool_id(server: str, tool: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", f"mcp__{server}__{tool}")[:64]


class MCPClientManager:
    """Configures, connects and exposes external MCP servers (pinned, approval-gated)."""

    def __init__(self, config_path: Optional[str] = None):
        self.clients: Dict[str, ExternalMCPClient] = {}
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.disabled: Dict[str, str] = {}      # tool_id -> reason
        self.config_path = config_path or str(Path(__file__).parent / "mcp_config.json")
        self._load_config()

    def _load_config(self):
        path = Path(self.config_path)
        if not path.exists():
            return
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
            for name, srv in cfg.get("mcpServers", {}).items():
                if srv.get("command") or srv.get("url"):
                    try:
                        self.add_server(name, srv.get("command", ""), srv.get("args", []),
                                        url=srv.get("url", ""), headers=srv.get("headers"), env=srv.get("env"))
                    except ValueError as e:
                        logger.warning("[MCPClientManager] server %s skipped: %s", name, e)
        except Exception as e:
            logger.warning("[MCPClientManager] Failed to read config %s: %s", path, e)

    def add_server(self, name: str, command: str = "", args: Optional[List[str]] = None, *,
                   url: str = "", headers: Optional[dict] = None, env: Optional[dict] = None):
        validate_server_config(name, command, list(args or []))
        self.clients[name] = ExternalMCPClient(name, command, args, url=url, headers=headers, env=env)

    def register_discovered(self, server: str, client: Any, tdef: dict) -> str:
        """Pin + register one discovered tool. Returns pin state (new|ok|drift)."""
        from VENUAI import approvals, tool_registry
        tname = tdef.get("name", "")
        desc = tdef.get("description", "")
        schema = tdef.get("inputSchema") or {"type": "object", "properties": {}}
        tid = _tool_id(server, tname)
        state = approvals.pin_tool(server, tname, desc, schema)
        if state == "drift":
            self.disabled[tid] = "tool definition changed since it was pinned (re-approval required)"
            tool_registry.unregister(tid)
            self.tools.pop(tid, None)
            return state
        self.disabled.pop(tid, None)
        self.tools[tid] = {"client": client, "name": tname, "description": desc,
                           "schema": schema, "server": server}
        tool_registry.register(tool_registry.ToolDescriptor(
            tool_id=tid, description=desc or tname, required_permission="MCP_TOOLS",
            risk_level=tool_registry.RiskLevel.HIGH, allowed_agents=["react_agent"],
            network_access=True, timeout_seconds=30, audit_required=True,
            requires_approval=True, arg_schema=schema, max_retries=0, source="mcp"))
        approvals.register_executor(tid, lambda args, c=client, n=tname: c.call_tool(n, args))
        return state

    def initialize_all(self):
        """Connect, discover, pin. Drifted tools are disabled; pinned ones are registered."""
        for name, client in self.clients.items():
            if not client.connect():
                continue
            for tdef in client.list_tools():
                self.register_discovered(name, client, tdef)

    def get_tools(self, user_id: str, tenant_id: str = "default", user_role: str = "user") -> List[Any]:
        """LangChain tools for the caller. Calls are policy-checked, validated, approval-gated and logged."""
        from langchain_core.tools import StructuredTool
        from VENUAI.agent_tools import guarded_call

        out = []
        for tid, t in self.tools.items():
            def _make(tid=tid, t=t):
                def _run(**kwargs) -> str:
                    return guarded_call(
                        tid, kwargs, lambda: t["client"].call_tool(t["name"], kwargs), user_id=user_id,
                        tenant_id=tenant_id, user_role=user_role, kind="mcp",
                        description=t["description"], schema=t["schema"])
                return StructuredTool(name=tid, description=t["description"] or tid,
                                      args_schema=t["schema"], func=_run)
            out.append(_make())
        return out

    def close_all(self):
        for client in self.clients.values():
            client.close()


_mcp_manager_instance: Optional[MCPClientManager] = None


def get_mcp_client_manager() -> MCPClientManager:
    global _mcp_manager_instance
    if _mcp_manager_instance is None:
        _mcp_manager_instance = MCPClientManager()
        _mcp_manager_instance.initialize_all()
    return _mcp_manager_instance


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mgr = get_mcp_client_manager()
    print(f"Registered {len(mgr.tools)} external MCP tools; {len(mgr.disabled)} disabled by pin drift")
    for tid, t in mgr.tools.items():
        print(f" - {tid}: {t['description']}")
