"""
Graph storage — one `GraphStore` interface, two real backends.

SqliteGraphStore is the default (zero-config, same pattern as every other
store in this codebase). PostgresGraphStore is a real, working
implementation — verified against an actual local PostgreSQL instance
during development (see Documents/AI/knowledge-graph.md for exactly how) —
not a stub, but not the default either, because no deployment
configuration in this project (docker-compose, CI, .env.example) currently
provisions a Postgres service. Activate it by setting DATABASE_URL (or
PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE) and
KNOWLEDGE_GRAPH_BACKEND=postgres.
"""

import json
import logging
import os
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from VENUAI.knowledge_graph.models import Entity, EntityType, Relationship, RelationshipType

logger = logging.getLogger("drdo-rag")

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS kg_entities (
    entity_id       TEXT PRIMARY KEY,
    entity_type     TEXT NOT NULL,
    name            TEXT NOT NULL,
    owner_id        TEXT,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'UNKNOWN',
    properties_json TEXT NOT NULL DEFAULT '{{}}',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kg_entities_name ON kg_entities(name);
CREATE INDEX IF NOT EXISTS idx_kg_entities_type ON kg_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_kg_entities_tenant ON kg_entities(tenant_id);

CREATE TABLE IF NOT EXISTS kg_relationships (
    relationship_id     TEXT PRIMARY KEY,
    source_entity_id    TEXT NOT NULL,
    target_entity_id    TEXT NOT NULL,
    relationship_type   TEXT NOT NULL,
    source_evidence_id  TEXT,
    owner_id            TEXT,
    tenant_id           TEXT NOT NULL DEFAULT 'default',
    classification      TEXT NOT NULL DEFAULT 'UNKNOWN',
    properties_json      TEXT NOT NULL DEFAULT '{{}}',
    valid_from           TEXT,
    valid_until          TEXT,
    observed_at          TEXT,
    source_date          TEXT,
    created_at            TEXT NOT NULL,
    FOREIGN KEY (source_entity_id) REFERENCES kg_entities(entity_id),
    FOREIGN KEY (target_entity_id) REFERENCES kg_entities(entity_id)
);
CREATE INDEX IF NOT EXISTS idx_kg_rel_source ON kg_relationships(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_kg_rel_target ON kg_relationships(target_entity_id);
CREATE INDEX IF NOT EXISTS idx_kg_rel_tenant ON kg_relationships(tenant_id);
"""


def _row_to_entity(row) -> Entity:
    return Entity(
        entity_id=row["entity_id"], entity_type=EntityType(row["entity_type"]), name=row["name"],
        owner_id=row["owner_id"], tenant_id=row["tenant_id"] if "tenant_id" in row.keys() else "default",
        classification=row["classification"],
        properties=json.loads(row["properties_json"] or "{}"), created_at=row["created_at"],
    )


def _row_to_relationship(row) -> Relationship:
    return Relationship(
        relationship_id=row["relationship_id"], source_entity_id=row["source_entity_id"],
        target_entity_id=row["target_entity_id"], relationship_type=RelationshipType(row["relationship_type"]),
        source_evidence_id=row["source_evidence_id"], owner_id=row["owner_id"],
        tenant_id=row["tenant_id"] if "tenant_id" in row.keys() else "default",
        classification=row["classification"], properties=json.loads(row["properties_json"] or "{}"),
        valid_from=row["valid_from"], valid_until=row["valid_until"],
        observed_at=row["observed_at"], source_date=row["source_date"], created_at=row["created_at"],
    )


def _access_clause(
    allowed_owner_ids: Optional[List[str]], tenant_id: str = "default", table_alias: str = "",
) -> tuple:
    """Shared authorization filter — tenant isolation is always enforced
    (Phase 4 — see Documents/Backend/multi-tenancy.md), then the same rule
    already used by documents/datasets within that tenant: None =
    unrestricted (admin); otherwise owner_id IN (...) OR
    classification='PUBLIC'."""
    prefix = f"{table_alias}." if table_alias else ""
    tenant_clause = f"{prefix}tenant_id = ?"
    if allowed_owner_ids is None:
        return tenant_clause, [tenant_id]
    if not allowed_owner_ids:
        return f"{tenant_clause} AND {prefix}classification = 'PUBLIC'", [tenant_id]
    placeholders = ",".join("?" for _ in allowed_owner_ids)
    return (
        f"{tenant_clause} AND ({prefix}owner_id IN ({placeholders}) OR {prefix}classification = 'PUBLIC')",
        [tenant_id] + list(allowed_owner_ids),
    )


class GraphStore(ABC):
    @abstractmethod
    def add_entity(self, entity: Entity) -> None: ...

    @abstractmethod
    def get_entity(
        self, entity_id: str, allowed_owner_ids: Optional[List[str]] = None, tenant_id: str = "default",
    ) -> Optional[Entity]: ...

    @abstractmethod
    def find_entities(
        self, name_query: Optional[str] = None, entity_type: Optional[EntityType] = None,
        allowed_owner_ids: Optional[List[str]] = None, limit: int = 50, tenant_id: str = "default",
    ) -> List[Entity]: ...

    @abstractmethod
    def add_relationship(self, relationship: Relationship) -> None: ...

    @abstractmethod
    def get_relationships_for_entity(
        self, entity_id: str, as_of: Optional[datetime] = None,
        allowed_owner_ids: Optional[List[str]] = None, tenant_id: str = "default",
    ) -> List[Relationship]: ...


class SqliteGraphStore(GraphStore):
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or (Path(__file__).parent.parent / "data" / "knowledge_graph.db")
        self._db_path.parent.mkdir(exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            for statement in _SQLITE_SCHEMA.replace("{{}}", "{}").split(";"):
                if statement.strip():
                    conn.execute(statement)
            # Defensive migration for DB files created before Phase 4
            # multi-tenancy — same pattern as VENUAI.auth.init_db().
            for table in ("kg_entities", "kg_relationships"):
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'")
                except sqlite3.OperationalError:
                    pass
            conn.commit()

    def add_entity(self, entity: Entity) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO kg_entities (entity_id, entity_type, name, owner_id, tenant_id, classification, properties_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(entity_id) DO UPDATE SET properties_json = excluded.properties_json",
                (entity.entity_id, entity.entity_type.value, entity.name, entity.owner_id, entity.tenant_id,
                 entity.classification, json.dumps(entity.properties), entity.created_at or datetime.now().isoformat()),
            )
            conn.commit()

    def get_entity(
        self, entity_id: str, allowed_owner_ids: Optional[List[str]] = None, tenant_id: str = "default",
    ) -> Optional[Entity]:
        clause, params = _access_clause(allowed_owner_ids, tenant_id)
        with self._get_conn() as conn:
            row = conn.execute(
                f"SELECT * FROM kg_entities WHERE entity_id = ? AND {clause}", [entity_id] + params,
            ).fetchone()
        return _row_to_entity(row) if row else None

    def find_entities(
        self, name_query: Optional[str] = None, entity_type: Optional[EntityType] = None,
        allowed_owner_ids: Optional[List[str]] = None, limit: int = 50, tenant_id: str = "default",
    ) -> List[Entity]:
        clause, params = _access_clause(allowed_owner_ids, tenant_id)
        sql = f"SELECT * FROM kg_entities WHERE {clause}"
        if name_query:
            sql += " AND name LIKE ?"
            params.append(f"%{name_query}%")
        if entity_type:
            sql += " AND entity_type = ?"
            params.append(entity_type.value)
        sql += " LIMIT ?"
        params.append(limit)
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_entity(r) for r in rows]

    def add_relationship(self, relationship: Relationship) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO kg_relationships (relationship_id, source_entity_id, target_entity_id, "
                "relationship_type, source_evidence_id, owner_id, tenant_id, classification, properties_json, "
                "valid_from, valid_until, observed_at, source_date, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(relationship_id) DO UPDATE SET properties_json = excluded.properties_json",
                (relationship.relationship_id, relationship.source_entity_id, relationship.target_entity_id,
                 relationship.relationship_type.value, relationship.source_evidence_id, relationship.owner_id,
                 relationship.tenant_id, relationship.classification, json.dumps(relationship.properties),
                 relationship.valid_from, relationship.valid_until, relationship.observed_at,
                 relationship.source_date, relationship.created_at or datetime.now().isoformat()),
            )
            conn.commit()

    def get_relationships_for_entity(
        self, entity_id: str, as_of: Optional[datetime] = None,
        allowed_owner_ids: Optional[List[str]] = None, tenant_id: str = "default",
    ) -> List[Relationship]:
        clause, params = _access_clause(allowed_owner_ids, tenant_id)
        sql = (
            f"SELECT * FROM kg_relationships WHERE {clause} "
            "AND (source_entity_id = ? OR target_entity_id = ?)"
        )
        params = params + [entity_id, entity_id]
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        relationships = [_row_to_relationship(r) for r in rows]
        if as_of is not None:
            relationships = [r for r in relationships if r.is_valid_at(as_of)]
        return relationships


class PostgresGraphStore(GraphStore):
    """
    Real implementation, same schema/semantics as SqliteGraphStore above,
    using psycopg2. Verified end-to-end against an actual local PostgreSQL
    18 instance during development (see
    Documents/AI/knowledge-graph.md) — this is not a stub, but it is not
    this project's default backend, since no docker-compose/CI
    configuration provisions a Postgres service for it yet.

    Connects using DATABASE_URL if set, otherwise PGHOST/PGPORT/PGUSER/
    PGPASSWORD/PGDATABASE (standard libpq env vars, psycopg2's defaults).
    The target database must already exist — this class creates its own
    tables but does not create databases, matching normal Postgres
    operational practice.
    """

    def __init__(self) -> None:
        import psycopg2
        self._psycopg2 = psycopg2
        self._dsn = os.getenv("DATABASE_URL")
        self._init_db()

    def _get_conn(self):
        import psycopg2.extras
        if self._dsn:
            conn = self._psycopg2.connect(self._dsn)
        else:
            conn = self._psycopg2.connect(
                host=os.getenv("PGHOST", "localhost"),
                port=os.getenv("PGPORT", "5432"),
                user=os.getenv("PGUSER", "postgres"),
                password=os.getenv("PGPASSWORD", ""),
                dbname=os.getenv("PGDATABASE", "venuai_graph"),
            )
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn

    def _init_db(self) -> None:
        # Tables + non-tenant indexes first. Split from the tenant_id
        # indexes below because a pre-existing DB from before Phase 4
        # multi-tenancy already has these tables WITHOUT a tenant_id
        # column — "CREATE TABLE IF NOT EXISTS" is then a no-op, and a
        # "CREATE INDEX ... (tenant_id)" in the same batch would fail with
        # UndefinedColumn before the ALTER TABLE migration below ever runs.
        schema = """
        CREATE TABLE IF NOT EXISTS kg_entities (
            entity_id       TEXT PRIMARY KEY,
            entity_type     TEXT NOT NULL,
            name            TEXT NOT NULL,
            owner_id        TEXT,
            classification  TEXT NOT NULL DEFAULT 'UNKNOWN',
            properties_json TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kg_entities_name ON kg_entities(name);
        CREATE INDEX IF NOT EXISTS idx_kg_entities_type ON kg_entities(entity_type);

        CREATE TABLE IF NOT EXISTS kg_relationships (
            relationship_id     TEXT PRIMARY KEY,
            source_entity_id    TEXT NOT NULL REFERENCES kg_entities(entity_id),
            target_entity_id    TEXT NOT NULL REFERENCES kg_entities(entity_id),
            relationship_type   TEXT NOT NULL,
            source_evidence_id  TEXT,
            owner_id             TEXT,
            classification       TEXT NOT NULL DEFAULT 'UNKNOWN',
            properties_json       TEXT NOT NULL DEFAULT '{}',
            valid_from            TEXT,
            valid_until           TEXT,
            observed_at           TEXT,
            source_date           TEXT,
            created_at             TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kg_rel_source ON kg_relationships(source_entity_id);
        CREATE INDEX IF NOT EXISTS idx_kg_rel_target ON kg_relationships(target_entity_id);
        """
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(schema)
                # Defensive migration for DB instances created before Phase 4
                # multi-tenancy — same rationale as SqliteGraphStore._init_db().
                for table in ("kg_entities", "kg_relationships"):
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default'")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_kg_entities_tenant ON kg_entities(tenant_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_kg_rel_tenant ON kg_relationships(tenant_id)")
            conn.commit()
        finally:
            conn.close()

    def add_entity(self, entity: Entity) -> None:
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO kg_entities (entity_id, entity_type, name, owner_id, tenant_id, classification, properties_json, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (entity_id) DO UPDATE SET properties_json = EXCLUDED.properties_json",
                    (entity.entity_id, entity.entity_type.value, entity.name, entity.owner_id, entity.tenant_id,
                     entity.classification, json.dumps(entity.properties), entity.created_at or datetime.now().isoformat()),
                )
            conn.commit()
        finally:
            conn.close()

    def get_entity(
        self, entity_id: str, allowed_owner_ids: Optional[List[str]] = None, tenant_id: str = "default",
    ) -> Optional[Entity]:
        clause, params = self._pg_access_clause(allowed_owner_ids, tenant_id)
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM kg_entities WHERE entity_id = %s AND {clause}", [entity_id] + params)
                row = cur.fetchone()
        finally:
            conn.close()
        return self._dict_to_entity(row) if row else None

    def find_entities(
        self, name_query: Optional[str] = None, entity_type: Optional[EntityType] = None,
        allowed_owner_ids: Optional[List[str]] = None, limit: int = 50, tenant_id: str = "default",
    ) -> List[Entity]:
        clause, params = self._pg_access_clause(allowed_owner_ids, tenant_id)
        sql = f"SELECT * FROM kg_entities WHERE {clause}"
        if name_query:
            sql += " AND name ILIKE %s"
            params.append(f"%{name_query}%")
        if entity_type:
            sql += " AND entity_type = %s"
            params.append(entity_type.value)
        sql += " LIMIT %s"
        params.append(limit)
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        finally:
            conn.close()
        return [self._dict_to_entity(r) for r in rows]

    def add_relationship(self, relationship: Relationship) -> None:
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO kg_relationships (relationship_id, source_entity_id, target_entity_id, "
                    "relationship_type, source_evidence_id, owner_id, tenant_id, classification, properties_json, "
                    "valid_from, valid_until, observed_at, source_date, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (relationship_id) DO UPDATE SET properties_json = EXCLUDED.properties_json",
                    (relationship.relationship_id, relationship.source_entity_id, relationship.target_entity_id,
                     relationship.relationship_type.value, relationship.source_evidence_id, relationship.owner_id,
                     relationship.tenant_id, relationship.classification, json.dumps(relationship.properties),
                     relationship.valid_from, relationship.valid_until, relationship.observed_at,
                     relationship.source_date, relationship.created_at or datetime.now().isoformat()),
                )
            conn.commit()
        finally:
            conn.close()

    def get_relationships_for_entity(
        self, entity_id: str, as_of: Optional[datetime] = None,
        allowed_owner_ids: Optional[List[str]] = None, tenant_id: str = "default",
    ) -> List[Relationship]:
        clause, params = self._pg_access_clause(allowed_owner_ids, tenant_id)
        sql = (
            f"SELECT * FROM kg_relationships WHERE {clause} "
            "AND (source_entity_id = %s OR target_entity_id = %s)"
        )
        params = params + [entity_id, entity_id]
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        finally:
            conn.close()
        relationships = [self._dict_to_relationship(r) for r in rows]
        if as_of is not None:
            relationships = [r for r in relationships if r.is_valid_at(as_of)]
        return relationships

    @staticmethod
    def _pg_access_clause(allowed_owner_ids: Optional[List[str]], tenant_id: str = "default") -> tuple:
        tenant_clause = "tenant_id = %s"
        if allowed_owner_ids is None:
            return tenant_clause, [tenant_id]
        if not allowed_owner_ids:
            return f"{tenant_clause} AND classification = 'PUBLIC'", [tenant_id]
        placeholders = ",".join("%s" for _ in allowed_owner_ids)
        return (
            f"{tenant_clause} AND (owner_id IN ({placeholders}) OR classification = 'PUBLIC')",
            [tenant_id] + list(allowed_owner_ids),
        )

    @staticmethod
    def _dict_to_entity(row: dict) -> Entity:
        return Entity(
            entity_id=row["entity_id"], entity_type=EntityType(row["entity_type"]), name=row["name"],
            owner_id=row["owner_id"], tenant_id=row.get("tenant_id", "default"), classification=row["classification"],
            properties=json.loads(row["properties_json"] or "{}"), created_at=row["created_at"],
        )

    @staticmethod
    def _dict_to_relationship(row: dict) -> Relationship:
        return Relationship(
            relationship_id=row["relationship_id"], source_entity_id=row["source_entity_id"],
            target_entity_id=row["target_entity_id"], relationship_type=RelationshipType(row["relationship_type"]),
            source_evidence_id=row["source_evidence_id"], owner_id=row["owner_id"],
            tenant_id=row.get("tenant_id", "default"),
            classification=row["classification"], properties=json.loads(row["properties_json"] or "{}"),
            valid_from=row["valid_from"], valid_until=row["valid_until"],
            observed_at=row["observed_at"], source_date=row["source_date"], created_at=row["created_at"],
        )


_store: Optional[GraphStore] = None


def get_graph_store() -> GraphStore:
    """
    Backend selection: KNOWLEDGE_GRAPH_BACKEND=postgres opts into
    PostgresGraphStore (real, tested — see class docstring); anything else
    (including unset) uses SqliteGraphStore, the zero-config default
    consistent with every other store in this codebase.
    """
    global _store
    if _store is None:
        if os.getenv("KNOWLEDGE_GRAPH_BACKEND", "sqlite").lower() == "postgres":
            _store = PostgresGraphStore()
        else:
            _store = SqliteGraphStore()
    return _store


def reset_graph_store_singleton() -> None:
    """Test-only hook — same rationale as VENUAI.ai_gateway.gateway.reset_gateway_singleton."""
    global _store
    _store = None
