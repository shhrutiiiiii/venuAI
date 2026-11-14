"""
DRDO VenuAI — Analytics
=========================
Logs every query with full metadata to SQLite and aggregates
stats for the admin dashboard.

Usage:
    analytics = QueryAnalytics()
    analytics.log_query(session_id, user_id, question, ...)
    data = analytics.get_dashboard_data()
"""

import json
import re
import sqlite3
import logging
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("drdo-rag")

_DB_PATH = str(Path("VENUAI/venuai_memory.db").resolve())


class QueryAnalytics:
    """
    Logs every query with performance metadata to SQLite and provides
    aggregated statistics for the analytics dashboard.
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Create the analytics table if it does not exist."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id          TEXT,
                    user_id             TEXT,
                    question            TEXT,
                    answer_len          INTEGER DEFAULT 0,
                    latency_ms          REAL DEFAULT 0,
                    grounding_score     REAL DEFAULT 0,
                    hallucination_score REAL DEFAULT 0,
                    agent_used          TEXT DEFAULT 'rag',
                    tools_called        TEXT DEFAULT '[]',
                    error               TEXT,
                    timestamp           TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_ts ON analytics(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_user ON analytics(user_id)")
            conn.commit()

    def log_query(
        self,
        session_id: str,
        user_id: str,
        question: str,
        answer_len: int,
        latency_ms: float,
        grounding_score: float,
        hallucination_score: float,
        agent_used: str = "rag",
        tools_called: Optional[List[str]] = None,
        error: Optional[str] = None,
    ) -> None:
        """
        Insert a query analytics record into the SQLite `analytics` table.

        Args:
            session_id          : Session UUID.
            user_id             : AuthUser.id.
            question            : The user's question text.
            answer_len          : Length of the AI answer in characters.
            latency_ms          : End-to-end response latency in milliseconds.
            grounding_score     : Grounding score from VerifierAgent (0–1).
            hallucination_score : Hallucination score from HallucinationDetector (0–1).
            agent_used          : Which pipeline was used ('rag', 'agent', 'multi_agent').
            tools_called        : List of tool names called during agentic execution.
            error               : Error message if the query failed, else None.
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    """INSERT INTO analytics
                       (session_id, user_id, question, answer_len, latency_ms,
                        grounding_score, hallucination_score, agent_used,
                        tools_called, error, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        user_id or "anonymous",
                        question,
                        answer_len,
                        round(latency_ms, 1),
                        round(grounding_score, 3),
                        round(hallucination_score, 3),
                        agent_used,
                        json.dumps(tools_called or []),
                        error,
                        datetime.now().isoformat(),
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"[Analytics] log_query error: {e}")

    def get_dashboard_data(self) -> dict:
        """
        Return aggregated analytics statistics for the admin dashboard.

        Returns a dict with:
          - total_queries_today / week / all_time
          - avg_grounding_score (last 30 days, daily buckets)
          - avg_hallucination_rate (last 30 days, daily buckets)
          - top_topics (keyword extraction from questions)
          - queries_by_domain
          - avg_latency_ms
          - error_rate
          - recent_queries (last 20)
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                return self._aggregate(conn)
        except Exception as e:
            logger.error(f"[Analytics] get_dashboard_data error: {e}")
            return {}

    def _aggregate(self, conn: sqlite3.Connection) -> dict:
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        week_start = (now - timedelta(days=7)).isoformat()
        month_start = (now - timedelta(days=30)).isoformat()

        def scalar(sql: str, params=()) -> float:
            row = conn.execute(sql, params).fetchone()
            return row[0] if row and row[0] is not None else 0

        total_all = int(scalar("SELECT COUNT(*) FROM analytics"))
        total_today = int(scalar("SELECT COUNT(*) FROM analytics WHERE timestamp >= ?", (today_start,)))
        total_week = int(scalar("SELECT COUNT(*) FROM analytics WHERE timestamp >= ?", (week_start,)))

        avg_latency = scalar("SELECT AVG(latency_ms) FROM analytics WHERE error IS NULL")
        error_count = scalar("SELECT COUNT(*) FROM analytics WHERE error IS NOT NULL")
        error_rate = round(error_count / total_all * 100, 1) if total_all else 0.0

        # Daily grounding/hallucination trends (last 30 days)
        trend_rows = conn.execute(
            """SELECT DATE(timestamp) as day,
                      AVG(grounding_score) as avg_grounding,
                      AVG(hallucination_score) as avg_hallucination,
                      COUNT(*) as count
               FROM analytics
               WHERE timestamp >= ?
               GROUP BY day
               ORDER BY day""",
            (month_start,),
        ).fetchall()

        grounding_trend = [
            {"date": r[0], "value": round(r[1], 3), "count": r[3]} for r in trend_rows
        ]
        hallucination_trend = [
            {"date": r[0], "value": round(r[2], 3), "count": r[3]} for r in trend_rows
        ]

        # Domain breakdown (infer from agent_used + question keywords)
        domain_rows = conn.execute(
            "SELECT agent_used, COUNT(*) FROM analytics GROUP BY agent_used"
        ).fetchall()
        queries_by_domain = {r[0]: r[1] for r in domain_rows}

        # Top topics via keyword extraction
        all_questions = [
            r[0] for r in conn.execute("SELECT question FROM analytics LIMIT 500").fetchall()
        ]
        top_topics = self._extract_top_topics(all_questions)

        # Recent queries
        recent = conn.execute(
            """SELECT question, latency_ms, grounding_score, hallucination_score,
                      agent_used, timestamp, error
               FROM analytics ORDER BY timestamp DESC LIMIT 20"""
        ).fetchall()
        recent_queries = [
            {
                "question": r[0][:80],
                "latency_ms": r[1],
                "grounding_score": r[2],
                "hallucination_score": r[3],
                "agent_used": r[4],
                "timestamp": r[5],
                "error": r[6],
            }
            for r in recent
        ]

        ops = self._ops_block()
        return {
            "ops": ops,
            "online_eval": self._online_eval_block(),
            "total_queries": {
                "today": total_today,
                "week": total_week,
                "all_time": total_all,
            },
            "avg_latency_ms": round(avg_latency, 1),
            "error_rate_pct": error_rate,
            "grounding_trend": grounding_trend,
            "hallucination_trend": hallucination_trend,
            "queries_by_domain": queries_by_domain,
            "top_topics": top_topics,
            "recent_queries": recent_queries,
        }

    @staticmethod
    def _ops_block() -> dict:
        """Ledger-backed ops metrics (tokens, p50/p95, cache hit, escalation, cost). Never raises."""
        try:
            from VENUAI import trace_store
            m = trace_store.ops_metrics(7)
            cost, priced = 0.0, False
            try:
                from VENUAI.ai_gateway import model_registry as _mr
                for model, u in m["by_model"].items():
                    spec = getattr(_mr, "get_model", lambda _m: None)(model)
                    pin = getattr(spec, "price_in_per_mtok", None)
                    pout = getattr(spec, "price_out_per_mtok", None)
                    if pin is not None and pout is not None:
                        priced = True
                        cost += u["prompt_tokens"] / 1e6 * pin + u["completion_tokens"] / 1e6 * pout
            except Exception:
                pass
            m["cost_usd"] = round(cost, 4) if priced else None   # null = no pricing in registry
            return m
        except Exception as e:
            logger.debug("ops block failed: %s", e)
            return {}

    @staticmethod
    def _online_eval_block() -> dict:
        try:
            from VENUAI import online_evals
            return online_evals.status()
        except Exception:
            return {}

    @staticmethod
    def _extract_top_topics(questions: List[str]) -> List[dict]:
        """
        Extract top keywords from a list of questions using simple TF-based
        term frequency (no external library needed).
        """
        _STOPWORDS = {
            "what", "is", "the", "of", "in", "a", "an", "and", "or",
            "for", "to", "about", "tell", "me", "how", "does", "are",
            "which", "where", "can", "you", "explain", "give", "list",
            "provide", "describe", "drdo", "their", "its", "all",
        }
        counter: Counter = Counter()
        for q in questions:
            tokens = re.findall(r"\b[a-zA-Z]{3,}\b", q.lower())
            for t in tokens:
                if t not in _STOPWORDS:
                    counter[t] += 1

        return [{"topic": t, "count": c} for t, c in counter.most_common(10)]


# ── Module-level singleton ──────────────────────────────────────────────────────
_analytics: Optional[QueryAnalytics] = None


def get_analytics() -> QueryAnalytics:
    """Return (or create) the global QueryAnalytics singleton."""
    global _analytics
    if _analytics is None:
        _analytics = QueryAnalytics()
    return _analytics
