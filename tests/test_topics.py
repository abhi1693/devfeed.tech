import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from devfeed_core import topics
from devfeed_core.models import Topic
from devfeed_core.services import OperationConflict
from pydantic import ValidationError


@pytest.mark.parametrize(
    "description, expected",
    [
        (
            "# Python\n\nA **language** with [documentation](https://python.org).",
            "Python A language with documentation.",
        ),
        ("A [language][docs].\n\n[docs]: https://python.org", "A language."),
        (
            "Use `foo_bar` with C++, C#, and .NET; 2 * 3 < 7.",
            "Use foo_bar with C++, C#, and .NET; 2 * 3 < 7.",
        ),
        ("> A topic\n\n- First\n- Second\n\n~~Old~~ new", "A topic First Second Old new"),
        (
            "<p>A <strong>language</strong> &amp; tools.</p><script>bad()</script>",
            "A language & tools.",
        ),
        ("```python\nprint('hello')\n```", "print('hello')"),
        ("![logo](https://example.com/logo.png)", None),
        (None, None),
    ],
)
def test_topic_descriptions_are_plain_on_writes_and_legacy_reads(description, expected):
    values = dict(name="Python", slug="python", kind="language", description=description)
    body = topics.TopicWrite(**values)
    assert body.description == expected
    stored = topics.TopicOut.model_validate(
        {
            **values,
            "id": uuid.uuid4(),
            "aliases": [],
            "keywords": [],
            "ai_description": description,
            "website_url": None,
            "logo_url": None,
            "facts": [],
        }
    )
    assert stored.description == stored.ai_description == expected


def test_topic_description_length_validation_is_not_bypassed_by_markup():
    with pytest.raises(ValidationError):
        topics.TopicWrite(name="Python", slug="python", kind="language", description="**" * 2001)


def test_github_import_draft_normalizes_description_without_changing_evidence():
    from devfeed_core.github_topics import topic_document
    from devfeed_core.topic_proposals import TopicDraft

    document = b"---\ntopic: python\ndisplay_name: Python\n---\nA **language** with [docs](https://python.org)."
    draft = TopicDraft.model_validate(topic_document(document, "python", "a" * 40, "language"))
    assert draft.description == "A language with docs."


@pytest.mark.integration
def test_existing_topic_reads_are_plain_and_admin_writes_store_plain_text(
    database, client, admin_client
):
    raw = "A **language** with [documentation](https://python.org)."
    with database.begin() as session:
        topic = Topic(
            name="Python", slug="python", kind="language", status="active", description=raw
        )
        session.add(topic)
        session.flush()
        identifier = topic.id
    for response in (
        client.get("/v1/topics/python"),
        admin_client.get(f"/v1/admin/topics/{identifier}"),
    ):
        assert response.status_code == 200
        assert response.json()["description"] == "A language with documentation."
    with database() as session:
        assert session.get(Topic, identifier).description == raw
    response = admin_client.put(
        f"/v1/admin/topics/{identifier}",
        json={
            "name": "Python",
            "slug": "python",
            "kind": "language",
            "status": "active",
            "description": "A **language** with `typing`.",
        },
    )
    assert response.status_code == 200, response.text
    with database() as session:
        assert session.get(Topic, identifier).description == "A language with typing."


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


@pytest.mark.parametrize(
    "values",
    [dict(name="JavaScript", slug="another"), dict(name="Another", slug="javascript")],
)
def test_canonical_names_and_slugs_still_require_explicit_resolution(values):
    existing = Topic(id=uuid.uuid4(), name="JavaScript", slug="javascript", aliases=["JS"])
    db = SimpleNamespace(
        execute=lambda *_: None, scalars=lambda _: SimpleNamespace(all=lambda: [existing])
    )
    with pytest.raises(OperationConflict, match='already identifies topic "JavaScript"'):
        topics.save_topic(db, topics.TopicWrite(**values, kind="language"))


def test_relation_cannot_link_a_topic_to_itself():
    identifier = uuid.uuid4()
    with pytest.raises(OperationConflict, match="itself"):
        topics.relate_topics(
            None,
            topics.RelationWrite(
                topic_id=identifier, related_topic_id=identifier, relation="related_to"
            ),
        )
