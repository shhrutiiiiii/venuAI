"""
DRDO VenuAI — Memory Manager
==============================
3-tier persistent memory system tied to user identity.

Tiers:
  Tier 1 — Working Memory  : Python dict (in-process, per session, last 6 turns)
  Tier 2 — Episodic Memory : SQLite (venuai_memory.db) — transcripts & key facts
  Tier 3 — Semantic Memory : ChromaDB collection "user_memory" — preferences & themes

Usage:
    mm = MemoryManager()
    mm.save_turn(session_id, user_id, user_msg, ai_msg)
    ctx = mm.get_context(session_id, user_id, query)
"""

import re
import sqlite3
import logging
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("drdo-rag")

_MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = str(_MODULE_DIR / "venuai_memory.db")
DEFAULT_CHROMA_DIR = str(_MODULE_DIR / "chroma_db")

# ── DRDO entity patterns for key-fact extraction ───────────────────────────────
_LAB_CODES = re.compile(
    r"\b(DRDL|RCI|ASL|LRDE|DLRL|DEAL|ADE|GTRE|CABS|CVRDE|ARDE|HEMRL|"
    r"NPOL|NSTL|DMRL|DEBEL|DFRL|DIPAS|DYSL-AI|DYSL-QT|DYSL-CT|DYSL-SM|DYSL-AT)\b"
)
_WEAPON_NAMES = re.compile(
    r"\b(Agni|Prithvi|Akash|BrahMos|Pinaka|Tejas|Uttam|Astra|Varunastra|"
    r"Arjun|TAPAS|Ghatak|Kaveri|ATAGS|INSAS|Helina|Nag|Rustom)\b"
)
_NUMBERS = re.compile(r"\b\d[\d,\.]*\s*(km|m|kg|GHz|MHz|kN|MW|ms|sec|hours?|ft)\b", re.IGNORECASE)


def _extract_key_facts(user_msg: str, ai_msg: str) -> List[str]:
    """
    Lightweight entity extraction from a conversation turn.
    Returns a list of short fact strings to store in semantic memory.
    No LLM call — uses regex patterns.
    """
    combined = f"{user_msg} {ai_msg}"
    facts = []

    lab_hits = _LAB_CODES.findall(combined)
    if lab_hits:
        facts.append(f"User asked about labs: {', '.join(set(lab_hits))}")

    weapon_hits = _WEAPON_NAMES.findall(combined)
    if weapon_hits:
        facts.append(f"User queried weapons/platforms: {', '.join(set(weapon_hits))}")

    number_hits = _NUMBERS.findall(combined)
    if number_hits:
        # Store unique measurement strings
        unique_nums = list({f"{n[0]} {n[1]}" for n in number_hits[:5]})
        facts.append(f"Technical parameters discussed: {', '.join(unique_nums)}")

    # Capture the user query theme (first 80 chars of user message)
    if user_msg:
        facts.append(f"Query theme: {user_msg[:80].strip()}")

    return facts


# ── MemoryManager ───────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent

class MemoryManager:
    """
    3-tier persistent memory for VenuAI.

    Tier 1 — Working Memory  (in-process dict, fast, ephemeral)
    Tier 2 — Episodic Memory (SQLite, persists across restarts)
    Tier 3 — Semantic Memory (ChromaDB 'user_memory' collection, vector-searchable)
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        """
        Initialize the SQLite database and create tables if they don't exist.
        The ChromaDB collection is created lazily on first use to avoid
        loading the embedding model at import time.

        Args:
            db_path: Path to the SQLite database file. Defaults to
                     VENUAI/venuai_memory.db next to this module, i.e. the same
                     file regardless of the process working directory.
        """
        self._db_path = str(Path(db_path).resolve()) if db_path else DEFAULT_DB_PATH
        # Working memory is keyed by (tenant_id, user_id, session_id). It used to be
        # keyed by session_id alone, and the API default session id is the shared
        # string "default-session", so every user who did not pass a session id
        # read and wrote the same conversation history (cross-user leak).
        self._working: dict[tuple, list[dict]] = defaultdict(list)
        self._chroma_collection = None  # lazy-loaded
        self._embeddings = None         # lazy-loaded
        try:
            self._init_db()
        except Exception as e:
            logger.warning(f"[MemoryManager] _init_db warning: {e}")
        logger.info(f"[MemoryManager] Initialized. SQLite: {self._db_path}")

    # ── SQLite initialisation ─────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Create the SQLite tables for turns and sessions if they don't exist."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS turns (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  TEXT NOT NULL,
                    user_id     TEXT NOT NULL,
                    user_msg    TEXT NOT NULL,
                    ai_msg      TEXT NOT NULL,
                    timestamp   TEXT NOT NULL,
                    tenant_id   TEXT NOT NULL DEFAULT 'default'
                )
            """)
            self._add_column(conn, "turns", "tenant_id", "TEXT NOT NULL DEFAULT 'default'")
            self._migrate_sessions(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_user ON turns(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_scope ON turns(tenant_id, user_id, session_id)")
            # Tier 4 — Research Memory: findings produced by DeepResearchAgent
            # (VENUAI/agents/deep_research_agent.py), keyed by a research_id so
            # a long-running research task's results can be retrieved later.
            # Same SQLite file as episodic memory above — not a new store.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_findings (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    research_id     TEXT NOT NULL,
                    user_id         TEXT NOT NULL,
                    question        TEXT NOT NULL,
                    answer          TEXT NOT NULL,
                    evidence_coverage REAL NOT NULL DEFAULT 0.0,
                    critique_passed INTEGER NOT NULL DEFAULT 0,
                    timestamp       TEXT NOT NULL,
                    tenant_id       TEXT NOT NULL DEFAULT 'default'
                )
            """)
            self._add_column(conn, "research_findings", "tenant_id", "TEXT NOT NULL DEFAULT 'default'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_research_user ON research_findings(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_research_id ON research_findings(research_id)")
            conn.commit()

    @staticmethod
    def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    @staticmethod
    def _migrate_sessions(conn: sqlite3.Connection) -> None:
        """`sessions` used to have PRIMARY KEY(session_id): two users (or tenants)
        sharing a session id overwrote each other's summary (INSERT OR REPLACE)
        and read each other's rows. The key is now (tenant_id, user_id, session_id).
        Legacy rows are carried over under the 'default' tenant."""
        info = conn.execute("PRAGMA table_info(sessions)").fetchall()
        pk_cols = {r[1] for r in info if r[5]}
        if info and pk_cols == {"tenant_id", "user_id", "session_id"}:
            return
        if info:
            conn.execute("ALTER TABLE sessions RENAME TO sessions_legacy")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                tenant_id   TEXT NOT NULL DEFAULT 'default',
                user_id     TEXT NOT NULL,
                session_id  TEXT NOT NULL,
                summary     TEXT NOT NULL,
                start_time  TEXT NOT NULL,
                end_time    TEXT NOT NULL,
                turn_count  INTEGER DEFAULT 0,
                PRIMARY KEY (tenant_id, user_id, session_id)
            )
        """)
        if info:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (tenant_id, user_id, session_id, summary, start_time, end_time, turn_count) "
                "SELECT 'default', user_id, session_id, summary, start_time, end_time, turn_count FROM sessions_legacy")
            conn.execute("DROP TABLE sessions_legacy")

    # ── Lazy ChromaDB accessor ────────────────────────────────────────────────

    def _get_chroma_collection(self):
        """
        Lazy-load the ChromaDB 'user_memory' collection with sentence-transformer
        embeddings. Initialises on first call.
        """
        if self._chroma_collection is not None:
            return self._chroma_collection
        try:
            from langchain_community.embeddings import HuggingFaceEmbeddings
            from chromadb import PersistentClient
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

            embed_fn = SentenceTransformerEmbeddingFunction(
                model_name="sentence-transformers/all-MiniLM-L6-v2"
            )
            client = PersistentClient(path=DEFAULT_CHROMA_DIR)
            self._chroma_collection = client.get_or_create_collection(
                name="user_memory",
                embedding_function=embed_fn,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info("[MemoryManager] ChromaDB user_memory collection ready.")
        except Exception as e:
            logger.warning(f"[MemoryManager] ChromaDB unavailable: {e}")
            self._chroma_collection = None
        return self._chroma_collection

    # ── Public API ────────────────────────────────────────────────────────────

    @staticmethod
    def _wkey(tenant_id: Optional[str], user_id: str, session_id: str) -> tuple:
        from VENUAI.authz import normalize_session_id
        return (tenant_id or "default", user_id or "anonymous", normalize_session_id(session_id or "x"))

    def scoped(self, tenant_id: str) -> "_TenantMemory":
        """A view bound to one tenant, so handlers cannot forget the tenant."""
        return _TenantMemory(self, tenant_id)

    def save_turn(
        self,
        session_id: str,
        user_id: str,
        user_msg: str,
        ai_msg: str,
        tenant_id: str = "default",
    ) -> None:
        """
        Persist one conversation turn across all three memory tiers.

        1. Append to in-process working memory (last 20 turns kept in RAM).
        2. Insert the full turn into SQLite `turns` table.
        3. Extract key DRDO facts and upsert them into ChromaDB `user_memory`.

        Args:
            session_id : UUID identifying the current session.
            user_id    : User identifier from AuthUser.id.
            user_msg   : The user's message text.
            ai_msg     : The AI's response text.
        """
        ts = datetime.now().isoformat()

        # Tier 1 — working memory (isolated per tenant + user + session)
        wkey = self._wkey(tenant_id, user_id, session_id)
        self._working[wkey].append({
            "role": "user",
            "content": user_msg,
            "timestamp": ts,
        })
        self._working[wkey].append({
            "role": "assistant",
            "content": ai_msg,
            "timestamp": ts,
        })
        # Keep only the last 20 turns in RAM
        if len(self._working[wkey]) > 40:
            self._working[wkey] = self._working[wkey][-40:]

        # Tier 2 — episodic / SQLite
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO turns (session_id, user_id, user_msg, ai_msg, timestamp, tenant_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (wkey[2], user_id, user_msg, ai_msg, ts, tenant_id or "default"),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"[MemoryManager] SQLite save_turn error: {e}")

        # Tier 3 — semantic / ChromaDB
        try:
            facts = _extract_key_facts(user_msg, ai_msg)
            if facts:
                collection = self._get_chroma_collection()
                if collection:
                    for fact in facts:
                        doc_id = str(uuid.uuid4())
                        collection.upsert(
                            ids=[doc_id],
                            documents=[fact],
                            metadatas=[{
                                "user_id": user_id,
                                "tenant_id": tenant_id or "default",
                                "session_id": session_id,
                                "timestamp": ts,
                            }],
                        )
        except Exception as e:
            logger.warning(f"[MemoryManager] ChromaDB upsert error: {e}")

    def get_context(
        self,
        session_id: str,
        user_id: str,
        query: str,
        tenant_id: str = "default",
    ) -> dict:
        """
        Retrieve memory context for use in the LangGraph pipeline.

        Returns a dict with:
          'working'  : List[dict] — last 6 turns from working memory (Tier 1)
          'episodic' : str        — summarised relevant past sessions from SQLite (Tier 2)
          'semantic' : List[str]  — top 3 relevant memory snippets from ChromaDB (Tier 3)

        Args:
            session_id : Current session UUID.
            user_id    : User identifier.
            query      : The current user question (used for semantic search).
        """
        # Tier 1 — last 6 working-memory turns
        all_turns = self._working.get(self._wkey(tenant_id, user_id, session_id), [])
        working = all_turns[-12:]  # 6 pairs = 12 entries

        # Tier 2 — episodic: fetch past session summaries for this user
        episodic = self._get_episodic_context(user_id, tenant_id)

        # Tier 3 — semantic: vector search over past facts
        semantic = self._get_semantic_context(user_id, query, tenant_id)

        return {
            "working": working,
            "episodic": episodic,
            "semantic": semantic,
        }

    def summarize_session(self, session_id: str, user_id: Optional[str] = None,
                          tenant_id: str = "default") -> None:
        """
        Summarise all turns of a session into a 3–5 sentence paragraph using
        extractive summarisation (no LLM call). Store the summary in SQLite
        `sessions` table.

        Call this at the end of each user session.

        Args:
            session_id: The session UUID to summarise.
            user_id   : Required: a session is only ever summarised for the
                        (tenant, user) that owns it; without it nothing happens.
        """
        if not user_id:
            logger.warning("[MemoryManager] summarize_session called without user_id; ignored")
            return
        session_id = self._wkey(tenant_id, user_id, session_id)[2]
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT user_id, user_msg, ai_msg, timestamp "
                    "FROM turns WHERE session_id = ? AND user_id = ? AND tenant_id = ? ORDER BY id",
                    (session_id, user_id, tenant_id),
                ).fetchall()

            if not rows:
                return

            user_id = rows[0][0]
            start_time = rows[0][3]
            end_time = rows[-1][3]
            turn_count = len(rows)

            # Extractive summary: first sentence of first user turn,
            # first sentence of last AI turn, plus topic keywords.
            first_user = rows[0][1]
            last_ai = rows[-1][2]

            def _first_sentence(text: str) -> str:
                parts = re.split(r"(?<=[.!?])\s+", text.strip())
                return parts[0] if parts else text[:100]

            labs = list(set(_LAB_CODES.findall(f"{first_user} {last_ai}")))
            weapons = list(set(_WEAPON_NAMES.findall(f"{first_user} {last_ai}")))

            summary_parts = [
                f"Session covered {turn_count} exchanges.",
                f"First query: {_first_sentence(first_user)}",
                f"Last response: {_first_sentence(last_ai)}",
            ]
            if labs:
                summary_parts.append(f"Labs discussed: {', '.join(labs[:5])}.")
            if weapons:
                summary_parts.append(f"Platforms discussed: {', '.join(weapons[:5])}.")

            summary = " ".join(summary_parts)

            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO sessions "
                    "(tenant_id, user_id, session_id, summary, start_time, end_time, turn_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (tenant_id, user_id, session_id, summary, start_time, end_time, turn_count),
                )
                conn.commit()

            logger.info(f"[MemoryManager] Session {session_id[:8]}… summarised ({turn_count} turns).")
        except Exception as e:
            logger.error(f"[MemoryManager] summarize_session error: {e}")

    def get_user_profile(self, user_id: str, tenant_id: str = "default") -> dict:
        """
        Return an aggregated profile for the user inferred from past queries.

        Returns:
            dict with:
              'total_queries'     : int
              'top_labs'          : List[str]
              'top_weapons'       : List[str]
              'expertise_level'   : 'beginner' | 'intermediate' | 'expert'
              'preferred_domains' : List[str]
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT user_msg, ai_msg FROM turns WHERE user_id = ? AND tenant_id = ?",
                    (user_id, tenant_id),
                ).fetchall()
        except Exception:
            return {}

        if not rows:
            return {"total_queries": 0}

        combined = " ".join(f"{r[0]} {r[1]}" for r in rows)
        labs = _LAB_CODES.findall(combined)
        weapons = _WEAPON_NAMES.findall(combined)

        # Infer expertise from average query length
        avg_len = sum(len(r[0]) for r in rows) / len(rows)
        expertise = "expert" if avg_len > 120 else ("intermediate" if avg_len > 60 else "beginner")

        from collections import Counter
        top_labs = [lab for lab, _ in Counter(labs).most_common(5)]
        top_weapons = [w for w, _ in Counter(weapons).most_common(5)]

        return {
            "total_queries": len(rows),
            "top_labs": top_labs,
            "top_weapons": top_weapons,
            "expertise_level": expertise,
            "preferred_domains": list(set(
                (["missiles"] if any(w in top_weapons for w in ["Agni", "Prithvi", "Akash"]) else []) +
                (["electronics"] if any(l in top_labs for l in ["LRDE", "DLRL"]) else []) +
                (["armaments"] if any(l in top_labs for l in ["ARDE", "HEMRL"]) else [])
            )),
        }

    # ── Tier 4: Research Memory ───────────────────────────────────────────────

    def save_research_finding(
        self,
        research_id: str,
        user_id: str,
        question: str,
        answer: str,
        evidence_coverage: float = 0.0,
        critique_passed: bool = False,
        tenant_id: str = "default",
    ) -> None:
        """Persist one DeepResearchAgent result, keyed by research_id so a
        long-running research task's findings can be retrieved later."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO research_findings "
                    "(research_id, user_id, question, answer, evidence_coverage, critique_passed, timestamp, tenant_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (research_id, user_id, question, answer, evidence_coverage,
                     int(critique_passed), datetime.now().isoformat(), tenant_id),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"[MemoryManager] save_research_finding failed: {e}")

    def get_research_findings(self, research_id: str, user_id: str, tenant_id: str = "default") -> List[dict]:
        """
        Retrieve findings for one research_id — scoped to user_id so one
        user cannot read another user's research findings by guessing an id.
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM research_findings WHERE research_id = ? AND user_id = ? AND tenant_id = ? "
                    "ORDER BY id ASC",
                    (research_id, user_id, tenant_id),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[MemoryManager] get_research_findings failed: {e}")
            return []

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_episodic_context(self, user_id: str, tenant_id: str = "default") -> str:
        """Fetch the 3 most recent session summaries for this user from SQLite."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT summary FROM sessions WHERE user_id = ? AND tenant_id = ? "
                    "ORDER BY end_time DESC LIMIT 3",
                    (user_id, tenant_id),
                ).fetchall()
            if rows:
                return " | ".join(r[0] for r in rows)
        except Exception as e:
            logger.debug(f"[MemoryManager] _get_episodic_context: {e}")
        return ""

    def _get_semantic_context(self, user_id: str, query: str, tenant_id: str = "default") -> List[str]:
        """Vector-search ChromaDB user_memory for facts relevant to the query."""
        try:
            collection = self._get_chroma_collection()
            if not collection:
                return []
            results = collection.query(
                query_texts=[query],
                n_results=3,
                where={"$and": [{"user_id": user_id}, {"tenant_id": tenant_id}]},
            )
            docs = results.get("documents", [[]])[0]
            return [d for d in docs if d]
        except Exception as e:
            logger.debug(f"[MemoryManager] _get_semantic_context: {e}")
        return []


class _TenantMemory:
    """MemoryManager bound to one tenant (see MemoryManager.scoped)."""

    def __init__(self, mm: MemoryManager, tenant_id: str) -> None:
        self._mm, self._tenant = mm, tenant_id

    def save_turn(self, session_id: str, user_id: str, user_msg: str, ai_msg: str) -> None:
        self._mm.save_turn(session_id, user_id, user_msg, ai_msg, tenant_id=self._tenant)

    def get_context(self, session_id: str, user_id: str, query: str) -> dict:
        return self._mm.get_context(session_id, user_id, query, tenant_id=self._tenant)

    def summarize_session(self, session_id: str, user_id: str) -> None:
        self._mm.summarize_session(session_id, user_id, tenant_id=self._tenant)

    def save_research_finding(self, research_id: str, user_id: str, question: str, answer: str,
                              evidence_coverage: float = 0.0, critique_passed: bool = False) -> None:
        self._mm.save_research_finding(research_id, user_id, question, answer, evidence_coverage,
                                       critique_passed, tenant_id=self._tenant)

    def get_research_findings(self, research_id: str, user_id: str) -> List[dict]:
        return self._mm.get_research_findings(research_id, user_id, tenant_id=self._tenant)

    def get_user_profile(self, user_id: str) -> dict:
        return self._mm.get_user_profile(user_id, tenant_id=self._tenant)


# ── Module-level singleton ──────────────────────────────────────────────────────
_memory_manager: Optional[MemoryManager] = None


def get_memory_manager() -> MemoryManager:
    """Return (or create) the global MemoryManager singleton."""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager


# ═══════════════════════════════════════════════════════════════════════════════
# Long-term memory (Wave B / B4) — mem0-style durable facts, per tenant + user.
#
#   * Feature flag LONG_TERM_MEMORY=on (default off) AND a per-user opt-in
#     (PUT /memory/settings {enabled:true}); both must hold for anything to be
#     stored or recalled.
#   * Extraction: memory_extractor.extract_memories (FAST model, one call/turn).
#   * Dedupe + ADD/UPDATE/NOOP by embedding similarity (MiniLM). If the embedding
#     model cannot load, falls back to token-overlap similarity (logged once);
#     it never fabricates vectors.
#   * Every row is keyed (tenant_id, user_id); every read/delete filters on both.
# ═══════════════════════════════════════════════════════════════════════════════
import json as _json
import os as _os
import threading as _threading
from concurrent.futures import ThreadPoolExecutor as _Pool

NOOP_SIM = 0.92      # near-identical to an existing memory -> NOOP
UPDATE_SIM = 0.75    # same topic, different detail -> UPDATE (replace text)
MAX_MEMORIES_PER_USER = 500

_embedder = None
_embedder_failed = False


def _get_embedder():
    global _embedder, _embedder_failed
    if _embedder is not None or _embedder_failed:
        return _embedder
    try:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", device="cpu")
    except Exception as e:  # noqa: BLE001
        _embedder_failed = True
        logger.warning("[LongTermMemory] embedding model unavailable (%s); using token-overlap similarity", e)
    return _embedder


def _embed(texts: List[str]) -> Optional[List[List[float]]]:
    """Normalised embeddings, or None when the model is unavailable."""
    model = _get_embedder()
    if model is None:
        return None
    try:
        vecs = model.encode(texts, normalize_embeddings=True)
        return [list(map(float, v)) for v in vecs]
    except Exception as e:  # noqa: BLE001
        logger.warning("[LongTermMemory] embed failed: %s", e)
        return None


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _similarity(a_text: str, a_vec, b_text: str, b_vec) -> float:
    if a_vec is not None and b_vec is not None and len(a_vec) == len(b_vec):
        return float(sum(x * y for x, y in zip(a_vec, b_vec)))
    ta, tb = _tokens(a_text), _tokens(b_text)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class LongTermMemoryStore:
    """SQLite-backed long-term memory. Same DB file as the other memory tiers."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS long_term_memories (
                    id             TEXT PRIMARY KEY,
                    tenant_id      TEXT NOT NULL,
                    user_id        TEXT NOT NULL,
                    text           TEXT NOT NULL,
                    kind           TEXT NOT NULL,
                    created_at     TEXT NOT NULL,
                    source_session TEXT,
                    embedding      TEXT
                )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ltm_user ON long_term_memories(tenant_id, user_id)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_settings (
                    tenant_id TEXT NOT NULL,
                    user_id   TEXT NOT NULL,
                    enabled   INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, user_id)
                )""")
            conn.commit()

    # settings -----------------------------------------------------------------
    def user_enabled(self, tenant_id: str, user_id: str) -> bool:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT enabled FROM memory_settings WHERE tenant_id=? AND user_id=?",
                (tenant_id, user_id)).fetchone()
        return bool(row and row[0])

    def set_enabled(self, tenant_id: str, user_id: str, enabled: bool) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO memory_settings (tenant_id,user_id,enabled,updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(tenant_id,user_id) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at",
                (tenant_id, user_id, int(enabled), datetime.now().isoformat()))
            conn.commit()

    # reads --------------------------------------------------------------------
    def _rows(self, tenant_id: str, user_id: str) -> List[sqlite3.Row]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                "SELECT * FROM long_term_memories WHERE tenant_id=? AND user_id=? ORDER BY created_at DESC",
                (tenant_id, user_id)).fetchall()

    def list(self, tenant_id: str, user_id: str) -> List[dict]:
        return [{"id": r["id"], "text": r["text"], "kind": r["kind"],
                 "created_at": r["created_at"], "source_session": r["source_session"]}
                for r in self._rows(tenant_id, user_id)]

    def recall(self, tenant_id: str, user_id: str, query: str, k: int = 5) -> List[dict]:
        rows = self._rows(tenant_id, user_id)
        if not rows or not (query or "").strip():
            return []
        qv = (_embed([query]) or [None])[0]
        scored = []
        for r in rows:
            ev = _json.loads(r["embedding"]) if r["embedding"] else None
            scored.append((_similarity(query, qv, r["text"], ev), r))
        scored.sort(key=lambda t: t[0], reverse=True)
        floor = 0.25 if qv is not None else 0.1
        return [{"id": r["id"], "text": r["text"], "kind": r["kind"]}
                for s, r in scored[:k] if s >= floor]

    # writes -------------------------------------------------------------------
    def add_or_update(self, tenant_id: str, user_id: str, text: str, kind: str,
                      source_session: Optional[str]) -> str:
        """Returns the decision: "ADD" | "UPDATE" | "NOOP"."""
        rows = self._rows(tenant_id, user_id)
        vec = (_embed([text]) or [None])[0]
        best, best_sim = None, 0.0
        for r in rows:
            ev = _json.loads(r["embedding"]) if r["embedding"] else None
            sim = _similarity(text, vec, r["text"], ev)
            if sim > best_sim:
                best, best_sim = r, sim
        emb = _json.dumps(vec) if vec is not None else None
        if best is not None and best_sim >= NOOP_SIM:
            return "NOOP"
        with sqlite3.connect(self._db_path) as conn:
            if best is not None and best_sim >= UPDATE_SIM and best["kind"] == kind:
                conn.execute(
                    "UPDATE long_term_memories SET text=?, embedding=?, created_at=?, source_session=? "
                    "WHERE id=? AND tenant_id=? AND user_id=?",
                    (text, emb, datetime.now().isoformat(), source_session, best["id"], tenant_id, user_id))
                conn.commit()
                return "UPDATE"
            if len(rows) >= MAX_MEMORIES_PER_USER:
                oldest = rows[-1]["id"]
                conn.execute("DELETE FROM long_term_memories WHERE id=? AND tenant_id=? AND user_id=?",
                             (oldest, tenant_id, user_id))
            conn.execute(
                "INSERT INTO long_term_memories (id,tenant_id,user_id,text,kind,created_at,source_session,embedding) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, tenant_id, user_id, text, kind, datetime.now().isoformat(),
                 source_session, emb))
            conn.commit()
            return "ADD"

    def delete(self, tenant_id: str, user_id: str, memory_id: str) -> bool:
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute("DELETE FROM long_term_memories WHERE id=? AND tenant_id=? AND user_id=?",
                               (memory_id, tenant_id, user_id))
            conn.commit()
            return cur.rowcount > 0

    def delete_all(self, tenant_id: str, user_id: str) -> int:
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute("DELETE FROM long_term_memories WHERE tenant_id=? AND user_id=?",
                               (tenant_id, user_id))
            conn.commit()
            return cur.rowcount


_ltm_store: Optional[LongTermMemoryStore] = None
_ltm_lock = _threading.Lock()


def get_ltm_store() -> LongTermMemoryStore:
    global _ltm_store
    with _ltm_lock:
        if _ltm_store is None:
            _ltm_store = LongTermMemoryStore(get_memory_manager()._db_path)
        return _ltm_store


def reset_ltm_store() -> None:
    global _ltm_store
    _ltm_store = None


def long_term_memory_enabled() -> bool:
    """Global feature flag (env LONG_TERM_MEMORY=on). Default off."""
    return _os.getenv("LONG_TERM_MEMORY", "off").strip().lower() in ("on", "1", "true", "yes")


def recall(tenant_id: str, user_id: str, query: str, k: int = 5) -> List[dict]:
    """FROZEN SEAM. Relevant long-term memories [{"id","text","kind"}] for this
    user, or [] when the feature is off / the user has not opted in / on error."""
    if not long_term_memory_enabled():
        return []
    try:
        store = get_ltm_store()
        if not store.user_enabled(tenant_id, user_id):
            return []
        return store.recall(tenant_id, user_id, query, k=k)
    except Exception as e:  # noqa: BLE001
        logger.warning("[LongTermMemory] recall failed: %s", e)
        return []


def remember_turn_sync(tenant_id: str, user_id: str, session_id: str, question: str, answer: str) -> List[str]:
    """Blocking implementation (used by tests and the background worker).
    Returns the list of ADD/UPDATE/NOOP decisions."""
    if not long_term_memory_enabled():
        return []
    store = get_ltm_store()
    if not store.user_enabled(tenant_id, user_id):
        return []
    from VENUAI import memory_extractor
    decisions = []
    for mem in memory_extractor.extract_memories(question, answer):
        decisions.append(store.add_or_update(tenant_id, user_id, mem["text"], mem["kind"], session_id))
    return decisions


_remember_pool = _Pool(max_workers=1, thread_name_prefix="ltm-remember")


def remember_turn(tenant_id: str, user_id: str, session_id: str, question: str, answer: str) -> None:
    """FROZEN SEAM. Fire-and-forget: schedules extraction on a background thread
    so it never delays the response. No-op if LONG_TERM_MEMORY!=on or the user
    has not opted in. Never raises."""
    if not long_term_memory_enabled():
        return None

    def _run():
        try:
            remember_turn_sync(tenant_id, user_id, session_id, question, answer)
        except Exception as e:  # noqa: BLE001
            logger.warning("[LongTermMemory] remember_turn failed: %s", e)

    try:
        _remember_pool.submit(_run)
    except Exception as e:  # noqa: BLE001
        logger.warning("[LongTermMemory] could not schedule remember_turn: %s", e)
    return None
