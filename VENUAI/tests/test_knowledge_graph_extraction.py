"""
Real tests for VENUAI/knowledge_graph/extraction.py — both the regex-based
deterministic extractor and the LLM-based one (with a fake Groq client, no
network). The central thing under test is "never invent a relationship":
the deterministic extractor only ever produces MENTIONS edges for text
that genuinely contains the lab code/weapon name, and the LLM extractor
discards anything that doesn't validate against the real enums.
"""

from langchain_core.documents import Document

from VENUAI.evidence import Evidence
from VENUAI.knowledge_graph.extraction import extract_deterministic, extract_llm
from VENUAI.knowledge_graph.models import EntityType, RelationshipType


def _evidence(content: str) -> Evidence:
    return Evidence.from_document(
        Document(page_content=content, metadata={"doc_id": "d1", "filename": "report.pdf", "page": 0})
    )


# ── Deterministic extraction ─────────────────────────────────────────────────

def test_deterministic_extracts_lab_code_mention():
    ev = _evidence("LRDE develops advanced radar systems for DRDO.")
    result = extract_deterministic(ev, owner_id="u1", classification="PUBLIC")

    lab_entities = [e for e in result.entities if e.entity_type == EntityType.LABORATORY]
    assert any(e.name == "LRDE" for e in lab_entities)
    mentions = [r for r in result.relationships if r.relationship_type == RelationshipType.MENTIONS]
    assert len(mentions) >= 1
    assert all(r.source_evidence_id == ev.evidence_id for r in mentions)


def test_deterministic_extracts_weapon_name_mention():
    ev = _evidence("The Agni missile system has been tested successfully.")
    result = extract_deterministic(ev, owner_id="u1")

    tech_entities = [e for e in result.entities if e.entity_type == EntityType.TECHNOLOGY]
    assert any(e.name == "Agni" for e in tech_entities)


def test_deterministic_produces_no_entities_for_irrelevant_text():
    ev = _evidence("Bananas are a good source of potassium and fiber.")
    result = extract_deterministic(ev, owner_id="u1")

    # Only the source Document entity — no lab/weapon mentions invented from nothing.
    non_document_entities = [e for e in result.entities if e.entity_type != EntityType.DOCUMENT]
    assert non_document_entities == []
    assert result.relationships == []


def test_deterministic_relationships_always_carry_evidence_provenance():
    ev = _evidence("ARDE and HEMRL collaborate on propellant research.")
    result = extract_deterministic(ev, owner_id="u1")
    assert len(result.relationships) >= 1
    assert all(r.source_evidence_id == ev.evidence_id for r in result.relationships)


# ── LLM-based extraction ─────────────────────────────────────────────────────

class _FakeChoice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]
        self.usage = None


class _FakeGroqClient:
    def __init__(self, response_text):
        self.response_text = response_text

        class _Completions:
            def create(inner_self, model, messages, max_tokens, temperature):
                return _FakeResponse(response_text)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def test_llm_extraction_parses_valid_json_response(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    response = '{"entities": [{"name": "Uttam Radar", "type": "technology"}, {"name": "LRDE", "type": "laboratory"}], ' \
               '"relationships": [{"source": "Uttam Radar", "type": "developed_by", "target": "LRDE"}]}'
    client = _FakeGroqClient(response)
    ev = _evidence("Uttam Radar was developed by LRDE.")

    result = extract_llm(ev, client, owner_id="u1", classification="PUBLIC")

    assert result.is_fallback is False
    assert len(result.entities) == 2
    assert len(result.relationships) == 1
    assert result.relationships[0].relationship_type == RelationshipType.DEVELOPED_BY
    assert result.relationships[0].source_evidence_id == ev.evidence_id


def test_llm_extraction_drops_invalid_entity_type():
    client = _FakeGroqClient('{"entities": [{"name": "X", "type": "not_a_real_type"}], "relationships": []}')
    ev = _evidence("irrelevant")
    result = extract_llm(ev, client, owner_id="u1")
    assert result.entities == []  # hallucinated type dropped, not coerced


def test_llm_extraction_drops_relationship_referencing_unknown_entity():
    client = _FakeGroqClient(
        '{"entities": [{"name": "A", "type": "technology"}], '
        '"relationships": [{"source": "A", "type": "uses", "target": "B_never_listed"}]}'
    )
    ev = _evidence("irrelevant")
    result = extract_llm(ev, client, owner_id="u1")
    assert len(result.entities) == 1
    assert result.relationships == []  # target entity was never actually extracted


def test_llm_extraction_falls_back_to_empty_on_malformed_json():
    client = _FakeGroqClient("this is not JSON at all")
    ev = _evidence("irrelevant")
    result = extract_llm(ev, client, owner_id="u1")
    assert result.is_fallback is True
    assert result.entities == []
    assert result.relationships == []


def test_llm_extraction_falls_back_on_model_call_failure():
    class _BrokenClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("simulated failure")

    ev = _evidence("irrelevant")
    result = extract_llm(ev, _BrokenClient(), owner_id="u1")
    assert result.is_fallback is True
    assert result.entities == []


def test_llm_extraction_handles_markdown_fenced_json():
    client = _FakeGroqClient('```json\n{"entities": [], "relationships": []}\n```')
    ev = _evidence("irrelevant")
    result = extract_llm(ev, client, owner_id="u1")
    assert result.is_fallback is False
    assert result.entities == []
