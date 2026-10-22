"""Canonical topic edits and directed graph commands preserve explicit identities.

Catalog rows are already selected by the repository. These units verify the
identity policy and ORM mutations; SQL uniqueness, filtering and locks have their
own integration contracts.
"""

import copy
import uuid
from unittest.mock import Mock

import pytest
from devfeed_core import topic_proposals, topics
from devfeed_core.models import Topic, TopicRelation
from devfeed_core.services import OperationConflict, RecordNotFound
from taxonomy_command_helpers import topic


@pytest.mark.parametrize("collision", ["name", "slug"])
def test_canonical_collision_cannot_be_treated_as_a_second_topic(monkeypatch, collision):
    existing = topic(kind="library")
    body = topics.TopicWrite(
        name="React" if collision == "name" else "Other library",
        slug="react-new" if collision == "name" else "react",
        kind="library",
    )
    session = Mock()
    session.scalars.return_value.all.return_value = [existing]
    monkeypatch.setattr(topics, "lock_topics", Mock())
    with pytest.raises(OperationConflict, match=f"{collision.capitalize()}.*already identifies"):
        topics.save_topic(session, body)
    session.add.assert_not_called()
    session.flush.assert_not_called()
    assert existing.name == "React" and existing.slug == "react"


@pytest.mark.parametrize("initial_status", ["active", "proposed", "rejected"])
def test_shared_alias_is_search_vocabulary_and_does_not_merge_distinct_canonical_entities(
    monkeypatch,
    initial_status,
):
    other = topic("Rust", aliases=["React"])
    body = topics.TopicWrite(name="React", slug="react", kind="library", aliases=["ReactJS"])
    session = Mock()
    session.scalars.return_value.all.return_value = [other]
    monkeypatch.setattr(topics, "lock_topics", Mock())
    saved = topics.save_topic(session, body, initial_status=initial_status)
    session.add.assert_called_once_with(saved)
    session.flush.assert_called_once()
    assert saved.status == initial_status
    assert saved.name == "React" and saved.slug == "react" and saved.aliases == ["ReactJS"]
    assert other.name == "Rust" and other.aliases == ["React"]


@pytest.mark.parametrize("present", [False, True])
def test_existing_topic_edit_requires_its_identity_and_preserves_its_lifecycle(
    monkeypatch, present
):
    current = topic(kind="library", status="rejected")
    body = topics.TopicWrite(
        name=current.name,
        slug=current.slug,
        kind="library",
        description="A JavaScript UI library.",
        aliases=["ReactJS"],
    )
    session = Mock()
    session.scalars.return_value.all.return_value = [current] if present else []
    session.get.return_value = current if present else None
    monkeypatch.setattr(topics, "lock_topics", Mock())
    if present:
        assert topics.save_topic(session, body, current.id) is current
        assert current.status == "rejected" and current.description == body.description
        assert current.aliases == ["ReactJS"]
    else:
        with pytest.raises(RecordNotFound, match="Topic not found"):
            topics.save_topic(session, body, current.id)
        session.flush.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.parametrize("missing", ["focal", "peer", None])
@pytest.mark.parametrize("existing", [False, True])
def test_graph_command_requires_both_entities_and_updates_only_the_directed_edge(missing, existing):
    focal, peer = topic(kind="library"), topic("JavaScript", kind="language")
    body = topics.RelationWrite(
        topic_id=focal.id,
        related_topic_id=peer.id,
        relation="uses_language",
        evidence_url="https://react.dev/learn",
    )
    edge = TopicRelation(
        topic_id=focal.id,
        related_topic_id=peer.id,
        relation="uses_language",
        evidence_url="https://react.dev/",
    )
    before = copy.deepcopy(
        (focal.name, peer.name, edge.topic_id, edge.related_topic_id, edge.relation)
    )
    session = Mock()

    def loaded(model, identifier):
        if model is Topic:
            return (
                None
                if identifier == (focal.id if missing == "focal" else peer.id) and missing
                else (focal if identifier == focal.id else peer)
            )
        assert model is TopicRelation and identifier == (focal.id, peer.id, "uses_language")
        return edge if existing else None

    session.get.side_effect = loaded
    if missing:
        with pytest.raises(RecordNotFound, match="Topic not found"):
            topics.relate_topics(session, body)
        session.add.assert_not_called()
        assert edge.evidence_url == "https://react.dev/"
    else:
        saved = topics.relate_topics(session, body)
        assert saved.evidence_url == body.evidence_url
        assert (saved.topic_id, saved.related_topic_id, saved.relation) == (
            focal.id,
            peer.id,
            "uses_language",
        )
        if existing:
            assert saved is edge
            session.add.assert_not_called()
        else:
            session.add.assert_called_once_with(saved)
    assert (focal.name, peer.name, edge.topic_id, edge.related_topic_id, edge.relation) == before


def test_self_relation_is_rejected_before_any_entity_lookup():
    identifier, session = uuid.uuid4(), Mock()
    body = topics.RelationWrite(
        topic_id=identifier, related_topic_id=identifier, relation="depends_on"
    )
    with pytest.raises(OperationConflict, match="cannot be related to itself"):
        topics.relate_topics(session, body)
    assert session.mock_calls == []


@pytest.mark.parametrize("state", ["missing", "proposed", "rejected"])
def test_metadata_enrichment_does_not_create_or_reactivate_a_missing_or_inactive_topic(state):
    current = topic(status=state) if state != "missing" else None
    session = Mock()
    session.get.return_value = current
    session.scalar.return_value = current
    with pytest.raises(RecordNotFound if state == "missing" else OperationConflict):
        topic_proposals.preview_enrichment(session, uuid.uuid4() if current is None else current.id)
    session.scalars.assert_not_called()
    session.add.assert_not_called()


def test_import_catalog_resolves_canonical_slugs_to_existing_identity_rows():
    first, second = topic(), topic("JavaScript", kind="language")
    session = Mock()
    session.scalars.return_value = [first, second]
    assert topic_proposals.catalogs(session) == {first.slug: first, second.slug: second}
    session.add.assert_not_called()
