"""
Knowledge graph data model — spec sections 22/23.

Every Entity/Relationship carries provenance (`source_evidence_id`,
pointing back to a real `VENUAI.evidence.Evidence.evidence_id`) and
authorization fields (`owner_id`, `classification`) — the same rules
already enforced for documents/datasets/multimodal regions. "Never invent
graph relationships" (spec section 22) is enforced structurally: every
extraction path in `VENUAI/knowledge_graph/extraction.py` requires real
source text to point a relationship's provenance at.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class EntityType(str, Enum):
    DOCUMENT = "document"
    ORGANIZATION = "organization"
    RESEARCHER = "researcher"
    TECHNOLOGY = "technology"
    SYSTEM = "system"
    PROJECT = "project"
    LABORATORY = "laboratory"
    DATASET = "dataset"
    MODEL = "model"
    PAPER = "paper"
    EVENT = "event"
    LOCATION = "location"
    CONCEPT = "concept"


class RelationshipType(str, Enum):
    AUTHORED_BY = "authored_by"
    MENTIONS = "mentions"
    USES = "uses"
    DEPENDS_ON = "depends_on"
    PART_OF = "part_of"
    DEVELOPED_BY = "developed_by"
    RELATED_TO = "related_to"
    COMPARED_WITH = "compared_with"
    PUBLISHED_BY = "published_by"
    DERIVED_FROM = "derived_from"


@dataclass(frozen=True)
class Entity:
    entity_id: str
    entity_type: EntityType
    name: str
    owner_id: Optional[str] = None
    tenant_id: str = "default"
    classification: str = "UNKNOWN"
    properties: dict = field(default_factory=dict)
    created_at: str = ""

    @staticmethod
    def make_id(entity_type: EntityType, name: str, tenant_id: str = "default") -> str:
        """Deterministic id from (tenant, type, normalized name) — the same
        real entity mentioned twice within one tenant (e.g. "LRDE" seen in
        two documents) resolves to one node instead of being duplicated on
        every extraction. tenant_id is part of the hash (Phase 4
        multi-tenancy — see Documents/Backend/multi-tenancy.md) specifically
        so that two different tenants both mentioning "LRDE" produce two
        distinct, non-colliding nodes rather than one node whose
        owner_id/tenant_id gets silently overwritten by whichever tenant
        extracted it second."""
        normalized = name.strip().lower()
        return hashlib.sha256(f"{tenant_id}:{entity_type.value}:{normalized}".encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Relationship:
    relationship_id: str
    source_entity_id: str
    target_entity_id: str
    relationship_type: RelationshipType
    source_evidence_id: Optional[str] = None  # VENUAI.evidence.Evidence.evidence_id — real provenance, not invented
    owner_id: Optional[str] = None
    tenant_id: str = "default"
    classification: str = "UNKNOWN"
    properties: dict = field(default_factory=dict)
    # Temporal fields — spec section 23. All optional: a relationship with
    # no temporal info is treated as "currently valid, no known end," not
    # defaulted to any specific date (never fabricate a date that wasn't
    # actually observed in the source).
    valid_from: Optional[str] = None     # ISO date — when the relationship started being true
    valid_until: Optional[str] = None    # ISO date — when it stopped being true (None = still valid)
    observed_at: Optional[str] = None    # ISO datetime — when this extraction happened
    source_date: Optional[str] = None    # ISO date — the source document/evidence's own date, if known
    created_at: str = ""

    @staticmethod
    def make_id(source_id: str, rel_type: RelationshipType, target_id: str, source_evidence_id: Optional[str]) -> str:
        basis = f"{source_id}:{rel_type.value}:{target_id}:{source_evidence_id or ''}"
        return hashlib.sha256(basis.encode()).hexdigest()[:16]

    def is_valid_at(self, as_of: datetime) -> bool:
        """Spec section 23: 'What was the status in 2024?' — answerable by
        checking as_of against valid_from/valid_until."""
        if self.valid_from:
            try:
                if as_of < datetime.fromisoformat(self.valid_from):
                    return False
            except ValueError:
                pass
        if self.valid_until:
            try:
                if as_of > datetime.fromisoformat(self.valid_until):
                    return False
            except ValueError:
                pass
        return True
