"""B2: HippoRAG-style PPR retrieval on a synthetic 2-hop corpus (no models, no network)."""
from pathlib import Path

from VENUAI import kg_retrieval
from VENUAI.knowledge_graph.models import Entity, EntityType, Relationship, RelationshipType
from VENUAI.knowledge_graph.store import SqliteGraphStore


class FakeVS:
    def __init__(self, chunks):
        self.chunks = chunks  # doc_id -> [(text, filename)]

    def get(self, where=None, include=None):
        d = where["doc_id"]
        rows = self.chunks.get(d, [])
        return {"documents": [t for t, _ in rows],
                "metadatas": [{"doc_id": d, "filename": f, "page": 0} for _, f in rows]}


def _add(store, tenant, doc_id, fname, names):
    doc = Entity(Entity.make_id(EntityType.DOCUMENT, fname, tenant), EntityType.DOCUMENT, fname, tenant_id=tenant)
    store.add_entity(doc)
    for n in names:
        e = Entity(Entity.make_id(EntityType.TECHNOLOGY, n, tenant), EntityType.TECHNOLOGY, n, tenant_id=tenant)
        store.add_entity(e)
        store.add_relationship(Relationship(
            Relationship.make_id(doc.entity_id, RelationshipType.MENTIONS, e.entity_id, f"upload-{doc_id}"),
            doc.entity_id, e.entity_id, RelationshipType.MENTIONS, source_evidence_id=f"upload-{doc_id}", tenant_id=tenant))


def _setup(tmp_path: Path):
    st = SqliteGraphStore(tmp_path / "kg.db")
    _add(st, "t1", "d1", "one.pdf", ["Zorvak", "Quillon"])
    _add(st, "t1", "d2", "two.pdf", ["Quillon", "Brantel"])
    _add(st, "t1", "d3", "three.pdf", ["Unrelated"])
    _add(st, "t2", "x1", "other.pdf", ["Zorvak", "Secretnode"])
    vs = FakeVS({
        "d1": [("Zorvak is integrated with the Quillon sensor suite.", "one.pdf")],
        "d2": [("Quillon feeds the Brantel guidance computer, which fixes the final range.", "two.pdf")],
        "d3": [("Something about Unrelated things.", "three.pdf")],
        "x1": [("Zorvak secret other tenant text.", "other.pdf")],
    })
    return st, vs


def test_two_hop_reaches_second_document(tmp_path):
    st, vs = _setup(tmp_path)
    docs = kg_retrieval.retrieve_graph("How is Zorvak related to the final range?", "t1", None, k=6, vs=vs, store=st)
    ids = [d.metadata["doc_id"] for d in docs]
    assert "d1" in ids and "d2" in ids          # d2 does not mention Zorvak: reached via Quillon
    assert "d3" not in ids
    assert all(d.metadata["retriever"] == "kg_ppr" for d in docs)
    d2 = next(d for d in docs if d.metadata["doc_id"] == "d2")
    assert d2.metadata["kg_path"][0] == "Zorvak" and "Quillon" in d2.metadata["kg_path"]


def test_tenant_and_acl_isolation(tmp_path):
    st, vs = _setup(tmp_path)
    docs = kg_retrieval.retrieve_graph("Zorvak", "t1", None, k=8, vs=vs, store=st)
    assert "x1" not in [d.metadata["doc_id"] for d in docs]
    only_d1 = kg_retrieval.retrieve_graph("Zorvak", "t1", ["d1"], k=8, vs=vs, store=st)
    assert {d.metadata["doc_id"] for d in only_d1} == {"d1"}
    assert kg_retrieval.retrieve_graph("Zorvak", "t1", [], k=8, vs=vs, store=st) == []


def test_no_entity_hit_returns_empty_and_failure_is_swallowed(tmp_path):
    st, vs = _setup(tmp_path)
    assert kg_retrieval.retrieve_graph("nothing known here", "t1", None, vs=vs, store=st) == []
    class Boom:
        def list_entities(self, **k): raise RuntimeError("x")
    assert kg_retrieval.retrieve_graph("Zorvak", "t1", None, vs=vs, store=Boom()) == []


def test_ppr_sums_to_one_and_prefers_seed_neighbourhood():
    p = kg_retrieval.personalized_pagerank(4, [(0, 1, 1.0), (1, 2, 1.0)], {0: 1.0})
    assert abs(p.sum() - 1) < 1e-6 and p[1] > p[2] > p[3] == 0 or p[3] < p[2]
