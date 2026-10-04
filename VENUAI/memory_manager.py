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

class MemoryManager:
    """
    3-tier persistent memory for VenuAI.

    Tier 1 — Working Memory  (in-process dict, fast, ephemeral)
    Tier 2 — Episodic Memory (SQLite, persists across restarts)
    Tier 3 — Semantic Memory (ChromaDB 'user_memory' collection, vector-searchable)
    """

    def __init__(self, db_path: str = "VENUAI/venuai_memory.db") -> None:
        """
        Initialize the SQLite database and create tables if they don't exist.
        The ChromaDB collection is created lazily on first use to avoid
        loading the embedding model at import time.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._db_path = str(Path(db_path).resolve())
        self._working: dict[str, list[dict]] = defaultdict(list)  # session_id → turns
        self._chroma_collection = None  # lazy-loaded
        self._embeddings = None         # lazy-loaded
        self._init_db()
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
                    timestamp   TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id  TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    summary     TEXT NOT NULL,
                    start_time  TEXT NOT NULL,
                    end_time    TEXT NOT NULL,
                    turn_count  INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_user ON turns(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id)")
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
                    timestamp       TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_research_user ON research_findings(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_research_id ON research_findings(research_id)")
            conn.commit()

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
            client = PersistentClient(path="VENUAI/chroma_db")
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

    def save_turn(
        self,
        session_id: str,
        user_id: str,
        user_msg: str,
        ai_msg: str,
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

        # Tier 1 — working memory
        self._working[session_id].append({
            "role": "user",
            "content": user_msg,
            "timestamp": ts,
        })
        self._working[session_id].append({
            "role": "assistant",
            "content": ai_msg,
            "timestamp": ts,
        })
        # Keep only the last 20 turns in RAM
        if len(self._working[session_id]) > 40:
            self._working[session_id] = self._working[session_id][-40:]

        # Tier 2 — episodic / SQLite
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO turns (session_id, user_id, user_msg, ai_msg, timestamp) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (session_id, user_id, user_msg, ai_msg, ts),
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
        all_turns = self._working.get(session_id, [])
        working = all_turns[-12:]  # 6 pairs = 12 entries

        # Tier 2 — episodic: fetch past session summaries for this user
        episodic = self._get_episodic_context(user_id)

        # Tier 3 — semantic: vector search over past facts
        semantic = self._get_semantic_context(user_id, query)

        return {
            "working": working,
            "episodic": episodic,
            "semantic": semantic,
        }

    def summarize_session(self, session_id: str) -> None:
        """
        Summarise all turns of a session into a 3–5 sentence paragraph using
        extractive summarisation (no LLM call). Store the summary in SQLite
        `sessions` table.

        Call this at the end of each user session.

        Args:
            session_id: The session UUID to summarise.
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT user_id, user_msg, ai_msg, timestamp "
                    "FROM turns WHERE session_id = ? ORDER BY id",
                    (session_id,),
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
                    "(session_id, user_id, summary, start_time, end_time, turn_count) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, user_id, summary, start_time, end_time, turn_count),
                )
                conn.commit()

            logger.info(f"[MemoryManager] Session {session_id[:8]}… summarised ({turn_count} turns).")
        except Exception as e:
            logger.error(f"[MemoryManager] summarize_session error: {e}")

    def get_user_profile(self, user_id: str) -> dict:
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
                    "SELECT user_msg, ai_msg FROM turns WHERE user_id = ?",
                    (user_id,),
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
    ) -> None:
        """Persist one DeepResearchAgent result, keyed by research_id so a
        long-running research task's findings can be retrieved later."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO research_findings "
                    "(research_id, user_id, question, answer, evidence_coverage, critique_passed, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (research_id, user_id, question, answer, evidence_coverage,
                     int(critique_passed), datetime.now().isoformat()),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"[MemoryManager] save_research_finding failed: {e}")

    def get_research_findings(self, research_id: str, user_id: str) -> List[dict]:
        """
        Retrieve findings for one research_id — scoped to user_id so one
        user cannot read another user's research findings by guessing an id.
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM research_findings WHERE research_id = ? AND user_id = ? "
                    "ORDER BY id ASC",
                    (research_id, user_id),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[MemoryManager] get_research_findings failed: {e}")
            return []

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_episodic_context(self, user_id: str) -> str:
        """Fetch the 3 most recent session summaries for this user from SQLite."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT summary FROM sessions WHERE user_id = ? "
                    "ORDER BY end_time DESC LIMIT 3",
                    (user_id,),
                ).fetchall()
            if rows:
                return " | ".join(r[0] for r in rows)
        except Exception as e:
            logger.debug(f"[MemoryManager] _get_episodic_context: {e}")
        return ""

    def _get_semantic_context(self, user_id: str, query: str) -> List[str]:
        """Vector-search ChromaDB user_memory for facts relevant to the query."""
        try:
            collection = self._get_chroma_collection()
            if not collection:
                return []
            results = collection.query(
                query_texts=[query],
                n_results=3,
                where={"user_id": user_id},
            )
            docs = results.get("documents", [[]])[0]
            return [d for d in docs if d]
        except Exception as e:
            logger.debug(f"[MemoryManager] _get_semantic_context: {e}")
        return []


# ── Module-level singleton ──────────────────────────────────────────────────────
_memory_manager: Optional[MemoryManager] = None


def get_memory_manager() -> MemoryManager:
    """Return (or create) the global MemoryManager singleton."""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager
