"""
DRDO VenuAI — MCP Server
==========================
Exposes the DRDO Knowledge Base as an MCP (Model Context Protocol) server.
Compatible with Claude Desktop, Cursor, Windsurf, and Antigravity (AGY).

Transports:
  stdio            (default) — local-process only, no network, no auth needed
                     (the "caller" is whoever started this process locally).
  streamable-http  — real network transport for an actually-deployed MCP
                     server (Phase 5). Requires MCP_API_KEY to be set — the
                     server refuses to start in this mode without it (see
                     Documents/AI/mcp-deployment.md "Security
                     Considerations"). Bearer-token checked via constant-
                     time comparison in VenuAIBearerAuthMiddleware below.

Run        : python VENUAI/mcp_server.py                 (stdio)
             MCP_TRANSPORT=http python VENUAI/mcp_server.py  (streamable-http)

Resources:
  drdo://documents           — list all ingested documents
  drdo://document/{doc_id}   — fetch a specific document's content
  drdo://kb/preset           — return the preloaded DRDO master KB JSON

Tools:
  search_drdo_knowledge_base — hybrid RAG search
  upload_document            — ingest a PDF via file path
  query_with_rag             — full verified RAG query

Prompts:
  drdo_research_assistant    — standard VenuAI system prompt

Known limitation (see Documents/Backend/authentication.md): the tools
above use VENUAI.agent_tools' unbound, module-level functions (no
per-caller identity to filter by — there is exactly one "caller" per
process/connection here, same as any other MCP server). If this server
is ever extended to serve multiple distinct end users over one shared
HTTP deployment, it needs the same per-request identity binding
`agent_tools.build_agent_tools()` already does for the ReAct HTTP path —
not built here, since today's only real transport story is "one trusted
caller per connection" (a local stdio client, or one bearer-token-holding
HTTP client).
"""

import contextvars
import hmac
import json
import logging
import os
import sys
from pathlib import Path

# Ensure VENUAI package is importable when running as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("drdo-rag")

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    print(
        "ERROR: 'mcp' package not installed or incompatible version.\n"
        "Install with: pip install mcp\n"
        "(This module targets the mcp>=2.0 MCPServer API — mcp 1.x's FastMCP "
        "was renamed; see https://py.sdk.modelcontextprotocol.io/v2/migration/)\n"
    )
    sys.exit(1)

from dotenv import load_dotenv
load_dotenv()

mcp = MCPServer("VenuAI DRDO Knowledge Base")

BASE_DIR = Path(__file__).parent
META_FILE = BASE_DIR / "doc_metadata.json"
KB_FILE   = BASE_DIR / "data" / "drdo_knowledge_base.json"


# ── Helper — load metadata ─────────────────────────────────────────────────────

def _load_metadata() -> dict:
    if META_FILE.exists():
        with open(META_FILE) as f:
            return json.load(f)
    return {}


# ── Principal / tenant scoping, upload allowlist (Wave B / B3) ───────────────────

SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
_principal_var: contextvars.ContextVar = contextvars.ContextVar("mcp_principal", default=None)


def current_principal():
    """Caller identity for this MCP request: a real VenuAI user (JWT bearer) or the
    operator principal (static MCP_API_KEY / stdio) scoped to MCP_TENANT_ID."""
    p = _principal_var.get()
    if p is not None:
        return p
    from VENUAI.auth import AuthenticatedUser
    return AuthenticatedUser(id="mcp-operator", email="mcp-operator@local", role="admin",
                             tenant_id=os.getenv("MCP_TENANT_ID", "default"))


def _visible_doc_ids(principal):
    """Same visibility rule as rag_server.get_visible_doc_ids (tenant + owner/PUBLIC)."""
    meta = _load_metadata()
    tenant_docs = {k: v for k, v in meta.items() if v.get("tenant_id", "default") == principal.tenant_id}
    if principal.role == "admin":
        return list(tenant_docs)
    return [k for k, v in tenant_docs.items()
            if v.get("owner_id") == principal.id or v.get("classification") == "PUBLIC"]


def _scoped_metadata() -> dict:
    """Document metadata restricted to what the current principal may see."""
    p = current_principal()
    allowed = set(_visible_doc_ids(p))
    return {k: v for k, v in _load_metadata().items() if k in allowed}


def upload_dir() -> Path:
    return Path(os.getenv("MCP_UPLOAD_DIR", str(BASE_DIR / "data" / "mcp_uploads"))).resolve()


MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def resolve_upload_path(file_path: str):
    """Resolve `file_path` strictly inside the upload allowlist directory.
    Returns (Path, None) or (None, error). Symlinks/`..`/absolute escapes are rejected."""
    root = upload_dir()
    try:
        cand = Path(file_path)
        cand = (cand if cand.is_absolute() else root / cand).resolve()
        cand.relative_to(root)
    except (ValueError, OSError):
        return None, "Upload refused: path is outside the allowed upload directory."
    if not cand.is_file():
        return None, "File not found in the upload directory."
    if cand.suffix.lower() != ".pdf":
        return None, "Only PDF files are supported."
    if cand.stat().st_size > MAX_UPLOAD_BYTES:
        return None, "File too large (25 MB limit)."
    return cand, None


# ── Resources ───────────────────────────────────────────────────────────────────

@mcp.resource("drdo://documents")
def list_all_documents() -> str:
    """
    List all documents currently ingested into the DRDO knowledge base.
    Returns a formatted Markdown table with ID, filename, pages, upload date, and tags.
    """
    meta = _scoped_metadata()
    if not meta:
        return "The DRDO knowledge base is empty. No documents have been uploaded."

    lines = ["| ID (short) | Filename | Pages | Uploaded | Domains |",
             "|-----------|----------|-------|----------|---------|"]
    for doc_id, doc in meta.items():
        tags = doc.get("tags", {})
        domains = ", ".join(tags.get("domain_tags", []))[:30] or "N/A"
        lines.append(
            f"| `{doc_id[:8]}…` | {doc['filename']} | {doc['pages']} | "
            f"{doc['upload_time'][:10]} | {domains} |"
        )
    return "\n".join(lines)


@mcp.resource("drdo://document/{doc_id}")
def get_document(doc_id: str) -> str:
    """
    Retrieve structured information about a specific document by its ID.
    Returns metadata, auto-tags, and a summary of the document.

    Args:
        doc_id: The document UUID (full or first 8 characters).
    """
    meta = _scoped_metadata()
    # Support partial ID matching (first 8 chars)
    matched = None
    for k, v in meta.items():
        if k == doc_id or k.startswith(doc_id):
            matched = v
            break

    if not matched:
        return f"Document '{doc_id}' not found. Use drdo://documents to list available documents."

    tags = matched.get("tags", {})
    return (
        f"**Document:** {matched['filename']}\n"
        f"**ID:** {matched['id']}\n"
        f"**Pages:** {matched['pages']} | **Chunks:** {matched['chunks']} | "
        f"**Size:** {matched['size_kb']} KB\n"
        f"**Uploaded:** {matched['upload_time'][:19]}\n\n"
        f"**Domain Tags:** {', '.join(tags.get('domain_tags', [])) or 'N/A'}\n"
        f"**DRDO Labs:** {', '.join(tags.get('drdo_labs', [])) or 'N/A'}\n"
        f"**Key Entities:** {', '.join(tags.get('key_entities', [])) or 'N/A'}\n"
        f"**Classification:** {tags.get('classification_level', 'UNKNOWN')}\n\n"
        f"**Auto-Summary:**\n{tags.get('summary', 'Not available.')}"
    )


@mcp.resource("drdo://kb/preset")
def get_preset_kb() -> str:
    """
    Return the preloaded DRDO Master Knowledge Base JSON.
    Contains information about all DRDO clusters, laboratories, and major weapons platforms.
    """
    if not KB_FILE.exists():
        return "Preloaded DRDO knowledge base file not found."
    with open(KB_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return json.dumps(data, indent=2, ensure_ascii=False)


# ── Tools ────────────────────────────────────────────────────────────────────────

@mcp.tool()
def search_drdo_knowledge_base(query: str, top_k: int = 5) -> str:
    """
    Search the DRDO knowledge base using hybrid semantic + keyword retrieval.
    Returns relevant document chunks with source citations.

    Args:
        query : The search query about DRDO labs, weapons, specs, or research.
        top_k : Number of results to return (default 5).
    """
    try:
        from VENUAI.agent_tools import _search_kb
        p = current_principal()
        return _search_kb(query, max(1, min(int(top_k), 20)), allowed_doc_ids=_visible_doc_ids(p))
    except Exception as e:
        return f"Search failed: {str(e)}"


@mcp.tool()
def upload_document(file_path: str) -> str:
    """
    Ingest a PDF document into the DRDO knowledge base.
    Only files inside the operator-configured upload directory
    (MCP_UPLOAD_DIR, default VENUAI/data/mcp_uploads) can be read.

    Args:
        file_path: File name (or path) of a PDF inside the upload directory.
    """
    import urllib.request
    import urllib.error

    path, err = resolve_upload_path(file_path)
    if err:
        return err

    try:
        with open(path, "rb") as f:
            data = f.read()

        boundary = "VenuAIMCPBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: application/pdf\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()

        token = os.getenv("VENUAI_API_TOKEN", "")
        if not token:
            return ("Upload failed: VENUAI_API_TOKEN is not set (the backend's /upload requires "
                    "an authenticated user).")
        req = urllib.request.Request(
            os.getenv("MCP_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/") + "/upload",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     "Authorization": f"Bearer {token}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
        return (
            f"✅ Document uploaded successfully!\n"
            f"**Filename:** {result['filename']}\n"
            f"**Pages:** {result['pages']} | **Chunks:** {result['chunks']}"
        )
    except Exception as e:
        return f"Upload failed: {str(e)}"


@mcp.tool()
def query_with_rag(question: str, session_id: str = "") -> str:
    """
    Run a full VenuAI RAG query: retrieve → verify → hallucination check.
    Returns a grounded, verified answer with confidence score.

    Args:
        question   : The DRDO research question to answer.
        session_id : Optional session ID for memory context.
    """
    import urllib.request

    payload = json.dumps({
        "question": question,
        "top_k": 4,
        "session_id": session_id or "mcp-session",
    }).encode()

    try:
        req = urllib.request.Request(
            os.getenv("MCP_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/") + "/query",
            data=payload,
            headers={"Content-Type": "application/json",
                     **({"Authorization": "Bearer " + os.getenv("VENUAI_API_TOKEN", "")}
                        if os.getenv("VENUAI_API_TOKEN") else {})},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())

        answer = data.get("answer", "No response generated.")
        grounding = data.get("grounding_score", 0)
        hall_score = data.get("hallucination_score", 0)
        sources = data.get("sources", [])

        sources_md = "\n".join(
            f"- {s['document']}, p.{s['page']}" for s in sources[:5]
        )

        return (
            f"{answer}\n\n"
            f"---\n"
            f"📊 **Confidence:** Grounding={grounding:.2f} | Hallucination={hall_score:.2f}\n"
            f"📚 **Sources:**\n{sources_md or 'Preloaded knowledge base.'}"
        )
    except Exception as e:
        return f"RAG query failed: {str(e)}\n\nEnsure rag_server.py is running on port 8000."


# ── Prompts ──────────────────────────────────────────────────────────────────────

@mcp.prompt()
def drdo_research_assistant() -> str:
    """
    Standard VenuAI system prompt for DRDO research assistance.
    Use this as the system instruction when interacting with the VenuAI knowledge base.
    """
    return (
        "You are VenuAI, an intelligent scientific research assistant for DRDO "
        "(Defence Research and Development Organisation), India's premier defence R&D agency.\n\n"
        "You help DRDO scientists, engineers, and researchers with:\n"
        "- Technical specifications of weapon systems and platforms\n"
        "- Information about DRDO laboratories, clusters, and their mandates\n"
        "- R&D programme status, project timelines, and research outcomes\n"
        "- Materials science, propulsion, electronics, and systems engineering queries\n\n"
        "Always:\n"
        "1. Use the search_drdo_knowledge_base tool to retrieve relevant information\n"
        "2. Present data in Markdown tables and bullet points\n"
        "3. Bold key lab codes, weapon names, and technical parameters\n"
        "4. Cite sources with document name and page number\n"
        "5. State clearly if information is not available in the knowledge base"
    )


# ── HTTP transport auth (Phase 5) ────────────────────────────────────────────
# A deliberately simple shared-secret bearer check — not the mcp SDK's full
# OAuth-resource-server machinery (AuthSettings/token_verifier), which is
# built for a multi-client authorization-server flow this single-operator
# deployment doesn't have. One static, operator-provisioned API key is the
# right-sized real security mechanism here, the same way a plain API key
# secures countless internal services — see
# Documents/AI/mcp-deployment.md Design Principles.

class VenuAIBearerAuthMiddleware:
    """ASGI middleware: every HTTP request must carry
    `Authorization: Bearer <MCP_API_KEY>`, checked with `hmac.compare_digest`
    (constant-time — a naive `==` would leak the key's length/prefix via
    timing). Wraps the MCP server's own Starlette app, so this applies to
    every MCP endpoint without touching the SDK's internal routing."""

    def __init__(self, app, api_key: str):
        self.app = app
        self._api_key = api_key

    async def _reply(self, send, status, body, extra=None):
        headers = [(b"content-type", b"application/json")] + (extra or [])
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": json.dumps(body).encode()})

    def _authenticate(self, token: str):
        """Static operator key -> operator principal; else a VenuAI JWT -> that user. None if invalid."""
        if token and hmac.compare_digest(token, self._api_key):
            return _operator()
        if token:
            try:
                from VENUAI import auth
                payload = auth.decode_access_token(token)
                row = auth.get_user_by_id(payload["sub"])
                if row is not None:
                    return auth.AuthenticatedUser(id=row["id"], email=row["email"], role=row["role"],
                                                  tenant_id=row["tenant_id"])
            except Exception:
                return None
        return None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # OAuth 2.0 Protected Resource Metadata (RFC 9728) - public by design.
        if scope["path"] == "/.well-known/oauth-protected-resource":
            await self._reply(send, 200, protected_resource_metadata(scope))
            return

        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("latin-1")
        token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
        principal = self._authenticate(token)
        if principal is None:
            meta_url = _resource_url(scope) + "/.well-known/oauth-protected-resource"
            await self._reply(
                send, 401, {"error": "Unauthorized - missing or invalid bearer token."},
                [(b"www-authenticate", f'Bearer resource_metadata="{meta_url}"'.encode())])
            return

        version = headers.get(b"mcp-protocol-version", b"").decode("latin-1")
        if version and version not in SUPPORTED_PROTOCOL_VERSIONS:
            await self._reply(send, 400, {"error": f"Unsupported MCP-Protocol-Version '{version}'.",
                                          "supported": list(SUPPORTED_PROTOCOL_VERSIONS)})
            return

        tok = _principal_var.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            _principal_var.reset(tok)


def _operator():
    from VENUAI.auth import AuthenticatedUser
    return AuthenticatedUser(id="mcp-operator", email="mcp-operator@local", role="admin",
                             tenant_id=os.getenv("MCP_TENANT_ID", "default"))


def _resource_url(scope) -> str:
    configured = os.getenv("MCP_PUBLIC_URL", "").rstrip("/")
    if configured:
        return configured
    host = dict(scope.get("headers", [])).get(b"host", b"127.0.0.1").decode("latin-1")
    return f"http://{host}"


def protected_resource_metadata(scope) -> dict:
    """RFC 9728 document. The bearer is a VenuAI JWT (or the operator key); the authorization
    server is the VenuAI backend (``MCP_AUTH_SERVER``)."""
    base = _resource_url(scope)
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [os.getenv("MCP_AUTH_SERVER", "http://127.0.0.1:8000")],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["venuai:read"],
        "resource_name": "VenuAI DRDO Knowledge Base",
    }


def build_http_app():
    """
    Builds the real network-reachable ASGI app for `MCP_TRANSPORT=http`.
    Refuses to build (raises, never silently falls back to unauthenticated)
    if MCP_API_KEY isn't set — an HTTP-reachable MCP server with no auth
    at all would be a real, serious exposure, not a reasonable default.
    """
    api_key = os.getenv("MCP_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "MCP_TRANSPORT=http requires MCP_API_KEY to be set — refusing to start an "
            "unauthenticated, network-reachable MCP server. Generate one with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    app = mcp.streamable_http_app()
    return VenuAIBearerAuthMiddleware(app, api_key)


# ── Entry point ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()

    if transport == "http":
        import uvicorn
        logger.info("Starting VenuAI MCP Server (streamable-http transport, bearer-auth required)…")
        uvicorn.run(build_http_app(), host="127.0.0.1", port=int(os.getenv("MCP_PORT", "8765")))
    else:
        logger.info("Starting VenuAI MCP Server (stdio transport)…")
        mcp.run()
