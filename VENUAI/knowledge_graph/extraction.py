"""
Entity/relationship extraction — spec section 22's "never invent graph
relationships" is the central constraint here. Two real extractors:

  - extract_deterministic(): regex-based, reusing the SAME lab-code/
    weapon-name patterns already used by VENUAI.memory_manager for
    semantic-memory fact extraction (imported, not duplicated). Produces
    only MENTIONS relationships — a lab code or weapon name actually
    appearing in the evidence text is the entire claim being made, so
    there is nothing to fabricate.

  - extract_llm(): a real LLM call via AIGateway asking for entities and
    typed relationships as strict JSON. Every item is validated against
    the real EntityType/RelationshipType enums and discarded if it doesn't
    match — a hallucinated relationship type is dropped, not coerced into
    the nearest-looking real one. On any parse/call failure, returns an
    empty result (same fallback discipline as VENUAI.research_planner),
    never a fabricated extraction.

Every Entity/Relationship this module produces carries
`source_evidence_id` pointing at the real `VENUAI.evidence.Evidence` the
text came from.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from VENUAI.evidence import Evidence
from VENUAI.knowledge_graph.models import Entity, EntityType, Relationship, RelationshipType
from VENUAI.memory_manager import _LAB_CODES, _WEAPON_NAMES

logger = logging.getLogger("drdo-rag")


@dataclass(frozen=True)
class ExtractionResult:
    entities: List[Entity] = field(default_factory=list)
    relationships: List[Relationship] = field(default_factory=list)
    is_fallback: bool = False  # True if the LLM path failed/was unusable (extract_llm only)


def extract_deterministic(
    evidence: Evidence, owner_id: Optional[str] = None, classification: str = "UNKNOWN",
    tenant_id: str = "default",
) -> ExtractionResult:
    now = datetime.now().isoformat()
    text = evidence.content

    doc_entity = Entity(
        entity_id=Entity.make_id(EntityType.DOCUMENT, evidence.document_name, tenant_id),
        entity_type=EntityType.DOCUMENT, name=evidence.document_name,
        owner_id=owner_id, tenant_id=tenant_id, classification=classification, created_at=now,
    )

    entities = [doc_entity]
    relationships = []

    lab_hits = set(_LAB_CODES.findall(text))
    for lab_code in lab_hits:
        lab_entity = Entity(
            entity_id=Entity.make_id(EntityType.LABORATORY, lab_code, tenant_id),
            entity_type=EntityType.LABORATORY, name=lab_code,
            owner_id=owner_id, tenant_id=tenant_id, classification=classification, created_at=now,
        )
        entities.append(lab_entity)
        relationships.append(Relationship(
            relationship_id=Relationship.make_id(doc_entity.entity_id, RelationshipType.MENTIONS, lab_entity.entity_id, evidence.evidence_id),
            source_entity_id=doc_entity.entity_id, target_entity_id=lab_entity.entity_id,
            relationship_type=RelationshipType.MENTIONS, source_evidence_id=evidence.evidence_id,
            owner_id=owner_id, tenant_id=tenant_id, classification=classification, observed_at=now, created_at=now,
        ))

    weapon_hits = set(_WEAPON_NAMES.findall(text))
    for weapon_name in weapon_hits:
        tech_entity = Entity(
            entity_id=Entity.make_id(EntityType.TECHNOLOGY, weapon_name, tenant_id),
            entity_type=EntityType.TECHNOLOGY, name=weapon_name,
            owner_id=owner_id, tenant_id=tenant_id, classification=classification, created_at=now,
        )
        entities.append(tech_entity)
        relationships.append(Relationship(
            relationship_id=Relationship.make_id(doc_entity.entity_id, RelationshipType.MENTIONS, tech_entity.entity_id, evidence.evidence_id),
            source_entity_id=doc_entity.entity_id, target_entity_id=tech_entity.entity_id,
            relationship_type=RelationshipType.MENTIONS, source_evidence_id=evidence.evidence_id,
            owner_id=owner_id, tenant_id=tenant_id, classification=classification, observed_at=now, created_at=now,
        ))

    return ExtractionResult(entities=entities, relationships=relationships, is_fallback=False)


_EXTRACTION_PROMPT = """Extract entities and relationships EXPLICITLY STATED in the text below. \
Do not infer anything not directly supported by the text.

Valid entity types: document, organization, researcher, technology, system, project, laboratory, dataset, model, paper, event, location, concept
Valid relationship types: authored_by, mentions, uses, depends_on, part_of, developed_by, related_to, compared_with, published_by, derived_from

Respond with ONLY valid JSON in this exact shape, nothing else:
{{"entities": [{{"name": "...", "type": "..."}}], "relationships": [{{"source": "...", "type": "...", "target": "..."}}]}}

If nothing is clearly extractable, respond with {{"entities": [], "relationships": []}}.

Text:
{text}

JSON:"""


def extract_llm(
    evidence: Evidence, groq_client, owner_id: Optional[str] = None, classification: str = "UNKNOWN",
    tenant_id: str = "default",
) -> ExtractionResult:
    from VENUAI.ai_gateway.gateway import get_gateway
    from VENUAI.ai_gateway.model_router import TaskType

    now = datetime.now().isoformat()
    try:
        gateway = get_gateway(groq_client)
        result = gateway.complete(
            task_type=TaskType.GENERATION,
            messages=[{"role": "user", "content": _EXTRACTION_PROMPT.format(text=evidence.content[:2000])}],
            max_tokens=500,
            temperature=0.0,
        )
        raw = result.content.strip()
        # Models sometimes wrap JSON in a markdown fence despite instructions — strip it defensively.
        if raw.startswith("```"):
            raw = raw.strip("`").removeprefix("json").strip()
        parsed = json.loads(raw)

        entities_by_name = {}
        entities: List[Entity] = []
        for item in parsed.get("entities", []):
            try:
                etype = EntityType(item["type"])
            except (KeyError, ValueError):
                continue  # hallucinated/invalid type — dropped, never coerced
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            entity = Entity(
                entity_id=Entity.make_id(etype, name, tenant_id), entity_type=etype, name=name,
                owner_id=owner_id, tenant_id=tenant_id, classification=classification, created_at=now,
            )
            entities.append(entity)
            entities_by_name[name.lower()] = entity

        relationships: List[Relationship] = []
        for item in parsed.get("relationships", []):
            try:
                rtype = RelationshipType(item["type"])
            except (KeyError, ValueError):
                continue
            source = entities_by_name.get(str(item.get("source", "")).strip().lower())
            target = entities_by_name.get(str(item.get("target", "")).strip().lower())
            if not source or not target:
                continue  # relationship referencing an entity the model didn't actually list — dropped
            relationships.append(Relationship(
                relationship_id=Relationship.make_id(source.entity_id, rtype, target.entity_id, evidence.evidence_id),
                source_entity_id=source.entity_id, target_entity_id=target.entity_id,
                relationship_type=rtype, source_evidence_id=evidence.evidence_id,
                owner_id=owner_id, tenant_id=tenant_id, classification=classification, observed_at=now, created_at=now,
            ))

        return ExtractionResult(entities=entities, relationships=relationships, is_fallback=False)

    except Exception as e:
        logger.warning(f"[knowledge_graph.extract_llm] Falling back to empty extraction: {e}")
        return ExtractionResult(entities=[], relationships=[], is_fallback=True)
