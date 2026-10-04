"""
Real tests for VENUAI/knowledge_graph/store.py — SqliteGraphStore is
exercised unconditionally (zero-config); PostgresGraphStore is exercised
against a REAL PostgreSQL instance when one is reachable (verified during
development against a local server) and cleanly skipped otherwise — never
mocked, since the entire point of this backend is that it's a genuine
working implementation, not a stub.
"""

import os
from datetime import datetime

import pytest

from VENUAI.knowledge_graph.models import Entity, EntityType, Relationship, RelationshipType
from VENUAI.knowledge_graph.store import SqliteGraphStore


def _postgres_available() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=os.getenv("PGHOST", "localhost"), port=os.getenv("PGPORT", "5432"),
            user=os.getenv("PGUSER", "postgres"), password=os.getenv("PGPASSWORD", "postgres"),
            dbname=os.getenv("PGDATABASE", "venuai_graph_test"), connect_timeout=2,
        )
        conn.close()
        return True
    except Exception:
        return False


def _sqlite_store(tmp_path):
    return SqliteGraphStore(db_path=tmp_path / "kg_test.db")


def _postgres_store():
    os.environ.setdefault("PGDATABASE", "venuai_graph_test")
    os.environ.setdefault("PGPASSWORD", "postgres")
    from VENUAI.knowledge_graph.store import PostgresGraphStore
    store = PostgresGraphStore()
    # Unlike SqliteGraphStore (fresh temp file per test via tmp_path), this
    # Postgres store is a real, persistent, SHARED database across test
    # runs — entity ids are deterministic (Entity.make_id hashes type+name),
    # so two tests using the same default entity name collide on the same
    # row. Truncate before each test for real isolation, the same guarantee
    # tmp_path gives the sqlite side for free.
    conn = store._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE kg_relationships, kg_entities")
        conn.commit()
    finally:
        conn.close()
    return store


# Run every test against both backends where Postgres is reachable, and
# SQLite-only otherwise, rather than writing the suite twice.
def _stores(tmp_path):
    result = [("sqlite", _sqlite_store(tmp_path))]
    if _postgres_available():
        result.append(("postgres", _postgres_store()))
    return result


def _lab(name="LRDE", owner="u1", classification="UNKNOWN"):
    return Entity(entity_id=Entity.make_id(EntityType.LABORATORY, name), entity_type=EntityType.LABORATORY,
                  name=name, owner_id=owner, classification=classification)


def _tech(name="Uttam AESA Radar", owner="u1", classification="UNKNOWN"):
    return Entity(entity_id=Entity.make_id(EntityType.TECHNOLOGY, name), entity_type=EntityType.TECHNOLOGY,
                  name=name, owner_id=owner, classification=classification)


@pytest.mark.parametrize("backend_fixture", ["sqlite", "postgres"])
def test_add_and_get_entity(tmp_path, backend_fixture):
    if backend_fixture == "postgres" and not _postgres_available():
        pytest.skip("No reachable PostgreSQL instance")
    store = _sqlite_store(tmp_path) if backend_fixture == "sqlite" else _postgres_store()

    lab = _lab()
    store.add_entity(lab)
    got = store.get_entity(lab.entity_id)
    assert got is not None
    assert got.name == "LRDE"
    assert got.entity_type == EntityType.LABORATORY


@pytest.mark.parametrize("backend_fixture", ["sqlite", "postgres"])
def test_add_relationship_and_retrieve_for_entity(tmp_path, backend_fixture):
    if backend_fixture == "postgres" and not _postgres_available():
        pytest.skip("No reachable PostgreSQL instance")
    store = _sqlite_store(tmp_path) if backend_fixture == "sqlite" else _postgres_store()

    lab, tech = _lab(), _tech()
    store.add_entity(lab)
    store.add_entity(tech)
    rel = Relationship(
        relationship_id=Relationship.make_id(tech.entity_id, RelationshipType.DEVELOPED_BY, lab.entity_id, "ev1"),
        source_entity_id=tech.entity_id, target_entity_id=lab.entity_id,
        relationship_type=RelationshipType.DEVELOPED_BY, source_evidence_id="ev1", owner_id="u1",
    )
    store.add_relationship(rel)

    rels = store.get_relationships_for_entity(lab.entity_id)
    assert len(rels) == 1
    assert rels[0].relationship_type == RelationshipType.DEVELOPED_BY
    assert rels[0].source_evidence_id == "ev1"


@pytest.mark.parametrize("backend_fixture", ["sqlite", "postgres"])
def test_find_entities_by_name_query(tmp_path, backend_fixture):
    if backend_fixture == "postgres" and not _postgres_available():
        pytest.skip("No reachable PostgreSQL instance")
    store = _sqlite_store(tmp_path) if backend_fixture == "sqlite" else _postgres_store()

    store.add_entity(_lab("LRDE"))
    store.add_entity(_lab("DRDL"))
    found = store.find_entities(name_query="LRDE")
    assert len(found) == 1
    assert found[0].name == "LRDE"


@pytest.mark.parametrize("backend_fixture", ["sqlite", "postgres"])
def test_authorization_filters_other_users_entities(tmp_path, backend_fixture):
    if backend_fixture == "postgres" and not _postgres_available():
        pytest.skip("No reachable PostgreSQL instance")
    store = _sqlite_store(tmp_path) if backend_fixture == "sqlite" else _postgres_store()

    private_lab = _lab("PrivateLab", owner="user-a", classification="UNKNOWN")
    store.add_entity(private_lab)

    # user-b's allowed_owner_ids doesn't include user-a, and the entity isn't PUBLIC.
    got = store.get_entity(private_lab.entity_id, allowed_owner_ids=["user-b"])
    assert got is None

    # The owner can see it.
    got_owner = store.get_entity(private_lab.entity_id, allowed_owner_ids=["user-a"])
    assert got_owner is not None


@pytest.mark.parametrize("backend_fixture", ["sqlite", "postgres"])
def test_public_entity_visible_to_everyone(tmp_path, backend_fixture):
    if backend_fixture == "postgres" and not _postgres_available():
        pytest.skip("No reachable PostgreSQL instance")
    store = _sqlite_store(tmp_path) if backend_fixture == "sqlite" else _postgres_store()

    public_lab = _lab("PublicLab", owner="user-a", classification="PUBLIC")
    store.add_entity(public_lab)

    got = store.get_entity(public_lab.entity_id, allowed_owner_ids=["user-b"])
    assert got is not None


@pytest.mark.parametrize("backend_fixture", ["sqlite", "postgres"])
def test_admin_sees_everything_with_none_allowed_owner_ids(tmp_path, backend_fixture):
    if backend_fixture == "postgres" and not _postgres_available():
        pytest.skip("No reachable PostgreSQL instance")
    store = _sqlite_store(tmp_path) if backend_fixture == "sqlite" else _postgres_store()

    private_lab = _lab("AdminVisibleLab", owner="user-a", classification="UNKNOWN")
    store.add_entity(private_lab)

    got = store.get_entity(private_lab.entity_id, allowed_owner_ids=None)
    assert got is not None


@pytest.mark.parametrize("backend_fixture", ["sqlite", "postgres"])
def test_temporal_query_filters_expired_relationships(tmp_path, backend_fixture):
    if backend_fixture == "postgres" and not _postgres_available():
        pytest.skip("No reachable PostgreSQL instance")
    store = _sqlite_store(tmp_path) if backend_fixture == "sqlite" else _postgres_store()

    lab, tech = _lab(), _tech()
    store.add_entity(lab)
    store.add_entity(tech)
    rel = Relationship(
        relationship_id=Relationship.make_id(tech.entity_id, RelationshipType.USES, lab.entity_id, "ev2"),
        source_entity_id=tech.entity_id, target_entity_id=lab.entity_id,
        relationship_type=RelationshipType.USES, source_evidence_id="ev2", owner_id="u1",
        valid_from="2020-01-01", valid_until="2022-12-31",
    )
    store.add_relationship(rel)

    current = store.get_relationships_for_entity(lab.entity_id, as_of=datetime(2026, 1, 1))
    assert len(current) == 0  # expired by "now"

    historical = store.get_relationships_for_entity(lab.entity_id, as_of=datetime(2021, 6, 1))
    assert len(historical) == 1
