"""
Evidence Explorer persistence (Phase 4) — see Documents/AI/evidence-explorer.md.

VENUAI.evidence.EvidenceGraph/Claim/Evidence are ephemeral, in-request
dataclasses (VENUAI.verification_pipeline builds and discards one per
call) — there was previously nowhere to browse a past answer's claim-by-
claim support breakdown after the response was returned. This module adds
a real, tenant-scoped SQLite store so standard RAG (`POST /query`) and
Deep Research (`POST /research`) answers can be revisited: which claims
were SUPPORTED/PARTIALLY_SUPPORTED/UNSUPPORTED/CONTRADICTED, and which
real Evidence items backed each one.

Same storage pattern as every other store in this codebase (auth.py,
guardrails.py, knowledge_graph/store.py) — one SQLite file, one
connection-per-call, no ORM.
"""

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import List, Optional

DB_PATH = Path("VENUAI/data/evidence_explorer.db")


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS evidence_records (
                record_id                  TEXT PRIMARY KEY,
                user_id                     TEXT NOT NULL,
                tenant_id                   TEXT NOT NULL DEFAULT 'default',
                question                    TEXT NOT NULL,
                answer                      TEXT NOT NULL,
                source                      TEXT NOT NULL,
                evidence_coverage           REAL NOT NULL,
                unsupported_claim_count     INTEGER NOT NULL,
                contradicted_claim_count    INTEGER NOT NULL,
                critique_passed             INTEGER NOT NULL DEFAULT 0,
                claims_json                 TEXT NOT NULL,
                created_at                  REAL NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_tenant ON evidence_records(tenant_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_user ON evidence_records(user_id)")
        conn.commit()


init_db()


def serialize_claims(claims) -> List[dict]:
    """
    Plain-dict form of `VENUAI.evidence.Claim`/`Evidence` objects — shared
    by `VENUAI.rag_server.verify_node` (standard RAG) and `POST /research`
    (`VENUAI.agents.deep_research_agent.DeepResearchResult.claims`), both
    of which build real `Claim` objects via `VerificationPipeline` and
    need the same JSON-shaped form for LangGraph state / API responses /
    this store. Evidence content is truncated — this is a browsing/audit
    view, not a full-document re-render.
    """
    return [
        {
            "claim_id": c.claim_id,
            "text": c.text,
            "support_status": c.support_status.value,
            "support_score": c.support_score,
            "evidence": [
                {
                    "evidence_id": e.evidence_id,
                    "document_name": e.document_name,
                    "page_number": e.page_number,
                    "content": e.content[:500],
                    "modality": e.modality.value,
                    "relevance_score": e.relevance_score,
                }
                for e in c.evidence
            ],
        }
        for c in claims
    ]


def save_record(
    user_id: str, tenant_id: str, question: str, answer: str, source: str,
    claims: List[dict], evidence_coverage: float, unsupported_claim_count: int,
    contradicted_claim_count: int, critique_passed: bool,
) -> str:
    """
    `claims` is already the plain-dict shape produced by
    `VENUAI.rag_server._serialize_claims()` — this module doesn't know
    about `VENUAI.evidence.Claim` objects, keeping it a pure storage layer.
    A record with zero claims (e.g. the KB-fallback path, or a question
    with no retrieved evidence at all) is still saved — "nothing was
    verifiable" is itself a real, useful fact for the explorer to show,
    not a reason to skip persistence.
    """
    record_id = uuid.uuid4().hex[:16]
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO evidence_records (record_id, user_id, tenant_id, question, answer, source, "
            "evidence_coverage, unsupported_claim_count, contradicted_claim_count, critique_passed, "
            "claims_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_id, user_id, tenant_id, question, answer, source,
                evidence_coverage, unsupported_claim_count, contradicted_claim_count,
                int(critique_passed), json.dumps(claims), time.time(),
            ),
        )
        conn.commit()
    return record_id


def _row_to_summary(row: sqlite3.Row) -> dict:
    return {
        "record_id": row["record_id"],
        "user_id": row["user_id"],
        "tenant_id": row["tenant_id"],
        "question": row["question"],
        "source": row["source"],
        "evidence_coverage": row["evidence_coverage"],
        "unsupported_claim_count": row["unsupported_claim_count"],
        "contradicted_claim_count": row["contradicted_claim_count"],
        "critique_passed": bool(row["critique_passed"]),
        "created_at": row["created_at"],
    }


def list_records(tenant_id: str, user_id: Optional[str], limit: int = 50) -> List[dict]:
    """`user_id=None` means unrestricted within the tenant (admin — same
    per-tenant-not-cross-tenant rule as every other Phase 4 listing); a
    non-admin always passes their own id."""
    with _get_conn() as conn:
        if user_id is None:
            rows = conn.execute(
                "SELECT * FROM evidence_records WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM evidence_records WHERE tenant_id = ? AND user_id = ? ORDER BY created_at DESC LIMIT ?",
                (tenant_id, user_id, limit),
            ).fetchall()
    return [_row_to_summary(r) for r in rows]


def get_record(record_id: str, tenant_id: str, user_id: Optional[str]) -> Optional[dict]:
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM evidence_records WHERE record_id = ?", (record_id,)).fetchone()
    if row is None or row["tenant_id"] != tenant_id:
        return None
    if user_id is not None and row["user_id"] != user_id:
        return None
    summary = _row_to_summary(row)
    summary["answer"] = row["answer"]
    summary["claims"] = json.loads(row["claims_json"])
    return summary
