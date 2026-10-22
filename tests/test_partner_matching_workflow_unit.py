"""Grounded matching and bounded work orchestration, with supplied repository reads.

Database filtering, lock acquisition and conflict-update atomicity are integration
contracts. These tests exercise decisions made after those reads have completed.
"""

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_core import partner_matching as matching
from devfeed_core.models import (
    Article,
    PartnerArticleMatch,
    PartnerMatchJob,
    PartnerProduct,
    PartnerProductTopic,
    Topic,
    TopicRelation,
)
from devfeed_core.urls import fingerprint
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


def product(**changes):
    return PartnerProduct(
        **{
            "id": uuid.uuid4(),
            "name": "API Checker",
            "product_url": "https://checker.example/",
            "tagline": "Detect breaking OpenAPI changes before deployment.",
            "pricing": "free",
            "revision": 2,
            "updated_at": NOW - timedelta(hours=1),
            "status": "approved",
            "verified_at": NOW - timedelta(hours=1),
            "assessment_revision": 2,
            "assessment": {"state": "qualified"},
            "excluded": False,
            "technologies": ["OpenAPI"],
            "matching_tokens": ["openapi"],
            "evidence": [
                {
                    "url": "https://checker.example/docs",
                    "quote": "Detect breaking OpenAPI changes before deployment.",
                    "capability": "Detect breaking changes before deployment.",
                }
            ],
            "reviews": [],
            **changes,
        }
    )


def article(**changes):
    identifier = changes.get("id", uuid.uuid4())
    url = changes.get("canonical_url", f"https://publisher.example/openapi-{identifier}")
    return Article(
        **{
            "id": identifier,
            "title": "Detect breaking OpenAPI changes",
            "summary": "Detect breaking OpenAPI changes before deployment using a CI check.",
            "content_type": "tutorial",
            "publication_status": "published",
            "review_status": "approved",
            "canonical_url": url,
            "url_hash": fingerprint(url),
            "slug": f"openapi-{identifier}",
            "discovered_at": NOW - timedelta(hours=2),
            "published_to_feed_at": NOW - timedelta(hours=1),
            "editorial_revision": 2,
            "matching_tokens": ["openapi", "detect", "breaking", "changes"],
            **changes,
        }
    )


def topic(name, **changes):
    return Topic(
        **{
            "id": uuid.uuid4(),
            "name": name,
            "slug": name.casefold().replace(" ", "-"),
            "status": "active",
            "kind": "technology",
            "aliases": [],
            "matching_tokens": [name.casefold()],
            "updated_at": NOW - timedelta(days=1),
            **changes,
        }
    )


def result(values):
    return NS(all=lambda: values)


@pytest.mark.parametrize("term", ["OpenAPI", "openapi"])
def test_task_match_uses_specific_verified_technology_and_shared_publisher_tasks(term):
    value = product(technologies=[term, "developer tools"])
    relevant, score, reason = matching.article_match(value, article())
    assert relevant and score >= 83
    assert "break" in reason and "deployment" in reason
    assert matching.grounded_technologies(value) == [term]


@pytest.mark.parametrize("case", ["absent_technology", "unsupported_quote", "one_task", "news"])
def test_marketing_or_one_shared_task_never_establishes_tutorial_relevance(case):
    value, story = product(), article()
    if case == "absent_technology":
        story.title = "Detect breaking protocol changes"
        story.summary = "Detect breaking protocol changes before deployment using a CI check."
    elif case == "unsupported_quote":
        value.evidence.insert(
            0, {"quote": "Detect breaking changes.", "capability": "Detect changes"}
        )
        value.evidence[1]["capability"] = "Unknown platform"
    elif case == "one_task":
        story.summary = (
            "OpenAPI deployment tutorial explains this specification in sufficient detail."
        )
    else:
        story.content_type = "news"
    assert matching.article_match(value, story) == (False, 0, "No grounded practical task match.")


def test_stronger_independent_evidence_wins_and_normalization_ignores_marketing_words():
    value = product()
    value.evidence.insert(0, {**value.evidence[0], "capability": "Detect breaking"})
    relevant, score, reason = matching.article_match(value, article())
    assert relevant and score == 94
    assert "deployment" in reason
    assert matching.words("Tools supports policies monitoring deployed changes") == {
        "policy",
        "monitor",
        "deploy",
        "change",
    }


def test_topic_preparation_prefers_exact_aliases_and_only_one_directed_relationship_hop():
    api = topic("API Description", aliases=["OpenAPI"])
    unrelated = topic("API")
    forward, symmetric, reverse, broad = (
        topic(name) for name in ["REST", "HTTP", "SDK Generator", "AI"]
    )
    relations = [
        TopicRelation(topic_id=api.id, related_topic_id=forward.id, relation="depends_on"),
        TopicRelation(topic_id=symmetric.id, related_topic_id=api.id, relation="related_to"),
        TopicRelation(topic_id=reverse.id, related_topic_id=api.id, relation="depends_on"),
    ]
    session = Mock(spec=Session)
    # The repository already applied relation direction/status restrictions. The
    # direct-topic list is an indexed superset which exact alias matching narrows.
    session.scalars.side_effect = [
        result([api, unrelated]),
        result([api, forward, symmetric, broad]),
        result([api.id, forward.id, symmetric.id]),
    ]
    session.execute.return_value.scalars.return_value.all.return_value = relations
    value = product()
    assert matching.prepare_topics(session, value, NOW) is True
    saved = [call.args[0] for call in session.add.call_args_list]
    assert all(isinstance(row, PartnerProductTopic) for row in saved)
    assert {row.topic_id: row.score for row in saved} == {
        api.id: 100,
        forward.id: 65,
        symmetric.id: 65,
    }
    assert all(row.product_revision == value.revision and row.updated_at == NOW for row in saved)


@pytest.mark.parametrize("case", ["no_evidence", "busy_topic"])
def test_topic_preparation_clears_unsupported_matches_but_preserves_busy_snapshot(case):
    session = Mock(spec=Session)
    if case == "no_evidence":
        value = product(evidence=[])
        assert matching.prepare_topics(session, value, NOW) is True
        session.scalars.assert_not_called()
        session.execute.assert_called_once()
    else:
        value = product()
        direct = topic("OpenAPI")
        session.scalars.side_effect = [result([direct]), result([]), result([])]
        session.execute.return_value.scalars.return_value.all.return_value = []
        assert matching.prepare_topics(session, value, NOW) is None
        # Only relationship read occurred; no deletion of the last good matches.
        assert session.execute.call_count == 1
    session.add.assert_not_called()


def current_reads(session, value, story):
    session.execute.side_effect = [
        NS(first=lambda: (value.revision, value.updated_at)),
        NS(first=lambda: story),
        Mock(),
    ]


@pytest.mark.parametrize("change", ["missing_product", "missing_article", "revision", "summary"])
def test_match_publication_refuses_changed_or_temporarily_unavailable_inputs(change):
    session = Mock(spec=Session)
    value, story = product(), article()
    current_story = article(id=story.id)
    if change == "summary":
        current_story.summary += " Human correction."
    current_product = (value.revision + (change == "revision"), value.updated_at)
    session.execute.side_effect = [
        NS(first=lambda: None if change == "missing_product" else current_product),
        NS(first=lambda: None if change == "missing_article" else current_story),
    ]
    assert (
        matching.save_article_match(session, value, story, True, 90, "Grounded task", now=NOW)
        is False
    )
    session.get.assert_not_called()
    assert session.execute.call_count == 2


@pytest.mark.parametrize(
    "origin,previous_origin,changed",
    [
        ("rules", "evaluation", False),
        ("rules", "evaluation", True),
        ("evaluation", "rules", False),
    ],
)
def test_current_evaluation_remains_authoritative_until_inputs_change(
    origin, previous_origin, changed
):
    session = Mock(spec=Session)
    value, story = product(), article()
    current_reads(session, value, story)
    previous = PartnerArticleMatch(
        product_id=value.id,
        article_id=story.id,
        origin=previous_origin,
        product_revision=value.revision - int(changed),
        product_updated_at=value.updated_at,
        article_fingerprint=matching.article_fingerprint(story),
        relevant=False,
        score=0,
        reason="The evaluator found a different practical task.",
        editorial_revision=2,
        updated_at=NOW,
    )
    session.get.return_value = previous
    assert matching.save_article_match(
        session, value, story, True, 90, "Grounded task", origin=origin, now=NOW
    )
    if origin == "rules" and not changed:
        assert session.execute.call_count == 2
        assert previous.relevant is False
    else:
        # Inspect the requested write, not PostgreSQL's conflict arbitration.
        values = session.execute.call_args.args[0].compile().params
        assert (values["origin"], values["product_revision"], values["relevant"]) == (
            origin,
            2,
            True,
        )


@pytest.mark.parametrize("kind", ["product", "article"])
def test_bounded_match_page_keeps_cursor_before_busy_input(monkeypatch, kind):
    value, story = product(), article()
    source, candidates = (
        (value, [article(), story]) if kind == "product" else (story, [product(), value])
    )
    candidates.sort(key=lambda row: row.id)
    job = PartnerMatchJob(
        kind=kind, entity_id=source.id, cursor=uuid.UUID(int=1), generation=1, updated_at=NOW
    )
    session = Mock(spec=Session)
    session.scalar.return_value = source
    session.scalars.side_effect = [result([row.id for row in candidates]), result(candidates)]
    publish = Mock(side_effect=[True, False])
    monkeypatch.setattr(matching, "save_article_match", publish)
    assert matching._prepare_job(session, job, article_limit=2, now=NOW) == (1, False)
    assert job.cursor == candidates[0].id
    assert publish.call_count == 2
    assert publish.call_args.args[3] is True


@pytest.mark.parametrize(
    "case", ["deleted", "busy", "taxonomy_busy", "topics_busy", "topics_complete", "empty"]
)
def test_preparation_retries_busy_inputs_and_completes_only_safe_terminal_work(monkeypatch, case):
    value = product()
    job = PartnerMatchJob(
        kind="topics" if case.startswith("topics") else "product",
        entity_id=value.id,
        cursor=None,
        generation=1,
    )
    session = Mock(spec=Session)
    session.scalar.side_effect = (
        [None, None if case == "deleted" else value.id]
        if case in {"deleted", "busy"}
        else [value, case != "taxonomy_busy"]
    )
    session.scalars.return_value = result([])
    prepared = Mock(return_value=None if case == "topics_busy" else True)
    monkeypatch.setattr(matching, "prepare_topics", prepared)
    assert matching._prepare_job(session, job, article_limit=2, now=NOW) == (
        0,
        case in {"deleted", "topics_complete", "empty"},
    )
    assert job.cursor is None


@pytest.mark.parametrize("limits", [(0, 10), (10, 0), (-1, 1)])
def test_invalid_batch_limits_fail_before_opening_transaction(limits):
    factory = Mock()
    with pytest.raises(ValueError, match="positive"):
        matching.refresh_partner_matches(factory, product_limit=limits[0], article_limit=limits[1])
    factory.begin.assert_not_called()


def test_match_drain_releases_preparation_transaction_before_enrollment(monkeypatch):
    jobs = [
        PartnerMatchJob(
            kind=kind, entity_id=uuid.uuid4(), generation=1, updated_at=NOW - timedelta(minutes=2)
        )
        for kind in ["product", "product", "article"]
    ]
    session = Mock(spec=Session)
    session.scalar.return_value = True
    session.scalars.return_value = result(jobs)
    phases = []

    @contextmanager
    def begin():
        phases.append("begin")
        yield session
        phases.append("end")

    factory = NS(begin=begin)
    monkeypatch.setattr(
        matching, "_prepare_job", Mock(side_effect=[(2, True), (1, False), (0, True)])
    )
    monkeypatch.setattr(matching, "utcnow", lambda: NOW)

    def enroll(actual_session, *, now, product_ids):
        assert phases == ["begin", "end", "begin"]
        assert actual_session is session and now == NOW
        assert product_ids == [jobs[0].entity_id, jobs[1].entity_id]

    enrollment = Mock(side_effect=enroll)
    monkeypatch.setattr(matching, "ensure_automatic_assets", enrollment)
    assert matching.refresh_partner_matches(factory, now=NOW) == 3
    assert [call.args[0] for call in session.delete.call_args_list] == [jobs[0], jobs[2]]
    assert jobs[1].updated_at == NOW
    enrollment.assert_called_once()


def test_another_active_match_drain_leaves_queue_untouched():
    session = Mock(spec=Session)
    session.scalar.return_value = False
    factory = Mock()
    factory.begin.return_value.__enter__ = Mock(return_value=session)
    factory.begin.return_value.__exit__ = Mock(return_value=False)
    assert matching.refresh_partner_matches(factory, now=NOW) == 0
    session.scalars.assert_not_called()
    session.delete.assert_not_called()
