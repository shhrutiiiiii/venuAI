"""Real tests for VENUAI/knowledge_graph/models.py."""

from datetime import datetime

from VENUAI.knowledge_graph.models import Entity, EntityType, Relationship, RelationshipType


def test_entity_make_id_is_deterministic_for_same_name():
    id1 = Entity.make_id(EntityType.LABORATORY, "LRDE")
    id2 = Entity.make_id(EntityType.LABORATORY, "LRDE")
    assert id1 == id2


def test_entity_make_id_normalizes_case_and_whitespace():
    id1 = Entity.make_id(EntityType.LABORATORY, "LRDE")
    id2 = Entity.make_id(EntityType.LABORATORY, "  lrde  ")
    assert id1 == id2


def test_entity_make_id_differs_by_type():
    id1 = Entity.make_id(EntityType.LABORATORY, "X")
    id2 = Entity.make_id(EntityType.TECHNOLOGY, "X")
    assert id1 != id2


def test_relationship_is_valid_at_within_range():
    rel = Relationship(
        relationship_id="r1", source_entity_id="a", target_entity_id="b",
        relationship_type=RelationshipType.RELATED_TO,
        valid_from="2022-01-01", valid_until="2024-12-31",
    )
    assert rel.is_valid_at(datetime(2023, 6, 1)) is True
    assert rel.is_valid_at(datetime(2021, 1, 1)) is False
    assert rel.is_valid_at(datetime(2025, 1, 1)) is False


def test_relationship_with_no_temporal_fields_is_always_valid():
    rel = Relationship(
        relationship_id="r1", source_entity_id="a", target_entity_id="b",
        relationship_type=RelationshipType.RELATED_TO,
    )
    assert rel.is_valid_at(datetime(2000, 1, 1)) is True
    assert rel.is_valid_at(datetime(2099, 1, 1)) is True


def test_relationship_with_only_valid_from_is_open_ended():
    rel = Relationship(
        relationship_id="r1", source_entity_id="a", target_entity_id="b",
        relationship_type=RelationshipType.RELATED_TO, valid_from="2022-01-01",
    )
    assert rel.is_valid_at(datetime(2021, 1, 1)) is False
    assert rel.is_valid_at(datetime(2099, 1, 1)) is True
