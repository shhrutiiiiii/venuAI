"""
Central authorization helpers (security stream S2).

One place decides "may this caller touch this resource?" so that no handler
re-implements (and subtly gets wrong) the tenancy / ownership rules.

Rules
-----
* Identity (user id, tenant id, role) always comes from the verified token /
  the user row. Nothing in a request body, query string or header is used.
* Tenant isolation is absolute: a resource of another tenant behaves exactly
  as if it did not exist.
* Inside a tenant a resource is visible to its owner, to a tenant admin
  (role "admin" is a *per-tenant* role, never cross-tenant) and, when its
  classification is PUBLIC, to every member of the tenant.
* A resource the caller may not see yields 404 with the SAME body as a
  resource that does not exist (no existence oracle). 403 is only used when
  the caller can see the resource but the action is not allowed for them
  (e.g. deleting someone else's PUBLIC document) or for role-gated routes.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from typing import Any, Iterable, List, Mapping, Optional

from fastapi import Depends, HTTPException

from VENUAI.auth import AuthenticatedUser, get_current_user

logger = logging.getLogger("drdo-rag")

DEFAULT_TENANT = "default"
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ── 404 helpers ──────────────────────────────────────────────────────────────

def not_found(what: str = "Resource") -> HTTPException:
    """The one response used for 'missing' AND 'not yours'."""
    return HTTPException(status_code=404, detail=f"{what} not found.")


def record_tenant(info: Mapping[str, Any]) -> str:
    """Tenant of a stored record; pre-multi-tenancy records belong to 'default'."""
    return (info.get("tenant_id") or DEFAULT_TENANT)


def is_tenant_admin(user: AuthenticatedUser) -> bool:
    return user.role == "admin"


def can_see(info: Mapping[str, Any], user: AuthenticatedUser) -> bool:
    """Visibility of a document/dataset-like record (has tenant_id/owner_id/classification)."""
    if record_tenant(info) != user.tenant_id:
        return False
    if is_tenant_admin(user):
        return True
    return info.get("owner_id") == user.id or info.get("classification") == "PUBLIC"


def can_modify(info: Mapping[str, Any], user: AuthenticatedUser) -> bool:
    """Write/delete: owner or tenant admin only (PUBLIC grants read, never write)."""
    if record_tenant(info) != user.tenant_id:
        return False
    return is_tenant_admin(user) or info.get("owner_id") == user.id


def filter_visible(records: Iterable[Mapping[str, Any]], user: AuthenticatedUser) -> List[Mapping[str, Any]]:
    return [r for r in records if can_see(r, user)]


def _check_access(store: Mapping[str, Any], resource_id: Any, user: AuthenticatedUser,
                  what: str, action: str, forbidden_msg: str) -> dict:
    # `resource_id` comes straight from the URL: never index blindly, never
    # assume it is hashable-and-present.
    info = store.get(resource_id) if isinstance(resource_id, str) else None
    if not isinstance(info, dict) or not can_see(info, user):
        raise not_found(what)
    if action != "read" and not can_modify(info, user):
        raise HTTPException(status_code=403, detail=forbidden_msg)
    return info


def check_doc_access(meta: Mapping[str, Any], doc_id: Any, user: AuthenticatedUser,
                     action: str = "read") -> dict:
    """Return the document record or raise 404 (not visible) / 403 (visible, not allowed)."""
    return _check_access(meta, doc_id, user, "Document", action,
                         "You do not have permission to modify this document.")


def check_dataset_access(meta: Mapping[str, Any], dataset_id: Any, user: AuthenticatedUser,
                         action: str = "read") -> dict:
    return _check_access(meta, dataset_id, user, "Dataset", action,
                         "You do not have permission to modify this dataset.")


# ── role gates ───────────────────────────────────────────────────────────────

def require_tenant_admin(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    if not is_tenant_admin(user):
        audit("authz.denied", user, detail="tenant admin required")
        raise HTTPException(status_code=403, detail="Admin privileges required.")
    return user


def _superadmin_ids() -> set:
    return {s.strip() for s in os.getenv("SUPERADMIN_USER_IDS", "").split(",") if s.strip()}


def is_superadmin(user: AuthenticatedUser) -> bool:
    """Platform operator: explicitly listed in SUPERADMIN_USER_IDS and also a
    tenant admin. Empty list (default) means nobody is a super-admin."""
    return is_tenant_admin(user) and user.id in _superadmin_ids()


def require_super_admin(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    if not is_superadmin(user):
        audit("authz.denied", user, detail="super admin required")
        raise HTTPException(status_code=403, detail="Admin privileges required.")
    return user


# ── scoped doc-id list (defence in depth for vector retrieval) ───────────────

class ScopedDocIds(list):
    """A list of authorized doc ids that also carries the tenant it was built
    for. Behaves exactly like a list (so every existing `allowed_doc_ids`
    consumer keeps working); `VENUAI.retrieval` additionally reads `.tenant_id`
    and filters vector hits on it (chroma metadata `tenant_id`)."""

    tenant_id: Optional[str] = None

    def __init__(self, items: Iterable[str] = (), tenant_id: Optional[str] = None) -> None:
        super().__init__(items)
        self.tenant_id = tenant_id


def tenant_of(allowed_doc_ids: Any) -> Optional[str]:
    return getattr(allowed_doc_ids, "tenant_id", None)


def chunk_in_tenant(metadata: Optional[Mapping[str, Any]], tenant_id: Optional[str]) -> bool:
    """Chunks written before tenant metadata existed carry no tenant_id; they
    belong to the 'default' tenant (their doc_id was already authorized)."""
    if not tenant_id:
        return True
    return ((metadata or {}).get("tenant_id") or DEFAULT_TENANT) == tenant_id


# ── memory / session scoping ─────────────────────────────────────────────────

def new_session_id() -> str:
    return uuid.uuid4().hex


def normalize_session_id(raw: Optional[str]) -> str:
    """Client-supplied session ids are only a label inside the caller's own
    (tenant, user) namespace. Anything not matching ^[A-Za-z0-9_-]{1,64}$ is
    replaced by a deterministic digest so odd input can never smuggle
    separators/wildcards into a key or query."""
    if raw is None or raw == "":
        return new_session_id()
    if isinstance(raw, str) and SESSION_ID_RE.match(raw):
        return raw
    return "s_" + hashlib.sha256(str(raw).encode("utf-8", "ignore")).hexdigest()[:32]


def memory_scope(tenant_id: Optional[str], user_id: Optional[str], session_id: Optional[str]) -> str:
    """Collision-free key for working memory: length-prefixed components."""
    parts = (tenant_id or DEFAULT_TENANT, user_id or "anonymous", session_id or "")
    return "|".join(f"{len(p)}:{p}" for p in parts)


# ── audit ────────────────────────────────────────────────────────────────────

def audit(event: str, user: Any = None, *, tenant_id: Optional[str] = None,
          user_id: Optional[str] = None, ip: Optional[str] = None, detail: Any = "") -> None:
    """Best-effort security audit. Uses VENUAI.security_audit.log (owned by S1)
    when present, otherwise the application log. Never raises."""
    uid = user_id or getattr(user, "id", None)
    tid = tenant_id or getattr(user, "tenant_id", None)
    try:
        from VENUAI import security_audit  # type: ignore
        security_audit.log(event, uid, tid, ip, detail)
        return
    except Exception:  # module absent or failing: fall back to the app log
        pass
    logger.warning("[security-audit] event=%s user=%s tenant=%s detail=%s", event, uid, tid, detail)
