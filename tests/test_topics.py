import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from devfeed_core import topics
from devfeed_core.models import Topic
from devfeed_core.services import OperationConflict
from pydantic import ValidationError


def test_topic_kind_is_dynamic_and_prose_is_not_implicitly_generated():
    body = topics.TopicWrite(name="Example", slug="example", kind="infrastructure-practice")
    assert body.kind == "infrastructure-practice"
    assert body.description is None and body.facts == []
    with pytest.raises(ValidationError):
        topics.TopicWrite(name="Example", slug="example", kind="x" * 51)
    with pytest.raises(ValidationError):
        topics.TopicWrite(name="Example", slug="example", kind="tool", ai_description="Guessed")


def test_topic_facts_require_citable_public_source_and_timezone():
    values = dict(
        name="first-release", value="Known date", source_url="https://example.com/history"
    )
    with pytest.raises(ValidationError):
        topics.TopicFact(**values, retrieved_at=datetime(2026, 1, 1))
    fact = topics.TopicFact(**values, retrieved_at="2026-01-01T00:00:00Z")
    assert fact.retrieved_at.tzinfo is not None
    with pytest.raises(ValidationError):
        topics.TopicFact(
            **{**values, "source_url": "http://127.0.0.1"}, retrieved_at=fact.retrieved_at
        )


def test_overlapping_aliases_require_explicit_resolution():
    existing = Topic(id=uuid.uuid4(), name="JavaScript", slug="javascript", aliases=["JS"])
    db = SimpleNamespace(
        execute=lambda *_: None, scalars=lambda _: SimpleNamespace(all=lambda: [existing])
    )
    with pytest.raises(OperationConflict, match="overlaps"):
        topics.save_topic(db, topics.TopicWrite(name="JS", slug="js", kind="language"))


def test_relation_cannot_link_a_topic_to_itself():
    identifier = uuid.uuid4()
    with pytest.raises(OperationConflict, match="itself"):
        topics.relate_topics(
            None,
            topics.RelationWrite(
                topic_id=identifier, related_topic_id=identifier, relation="related_to"
            ),
        )
