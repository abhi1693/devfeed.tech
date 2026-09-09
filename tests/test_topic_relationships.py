"""Real transactions for active-only AI suggestions and supervised graph writes."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from devfeed_aggregator import scheduler, topic_analysis_tasks
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Topic,
    TopicAnalysisJob,
    TopicRelation,
    TopicRelationProposal,
    utcnow,
)
from devfeed_core.topic_relationships import (
    RelationshipAnalysisRequest,
    request_relationship_analysis,
)
from sqlalchemy import func, select

pytestmark = pytest.mark.integration
BASE = "/v1/admin/topic-relationship-proposals"


@pytest.fixture
def catalog(database, monkeypatch):
    monkeypatch.setenv("DEVFEED_AI_ENABLED", "true")
    get_settings.cache_clear()
    with database.begin() as session:
        topics = [
            Topic(name=name, slug=name.lower(), kind="technology", status=status)
            for name, status in [
                ("React", "active"),
                ("JavaScript", "active"),
                ("Vue", "active"),
                ("Proposed", "proposed"),
                ("Rejected", "rejected"),
            ]
        ]
        session.add_all(topics)
        session.flush()
        return [topic.id for topic in topics]


def queue(client, topic, **body):
    response = client.post(f"/v1/admin/topics/{topic}/relationships/analysis", json=body)
    assert response.status_code == 202, response.text
    return uuid.UUID(response.json()["id"])


def suggestion(catalog, **changes):
    return {
        "topic_id": str(catalog[0]),
        "related_topic_id": str(catalog[1]),
        "relation": "uses_language",
        "explanation": "React is a JavaScript library.",
        "evidence_url": "https://react.dev/",
        "evidence_title": "React documentation",
        "evidence_quote": "The library for web and native user interfaces",
        **changes,
    }


def execute(monkeypatch, job, values, during=None):
    calls = []

    def complete(prompt, schema, **kwargs):
        calls.append((prompt, schema, kwargs))
        if during:
            during()
        return {"relationships": values, "reasons": []}

    monkeypatch.setattr(
        topic_analysis_tasks,
        "CodexClient",
        lambda _: SimpleNamespace(complete=complete, web_search_count=1),
    )
    topic_analysis_tasks._analyze(job)
    return calls


def count(database, model):
    with database() as session:
        return session.scalar(select(func.count()).select_from(model))


def review(client, proposal, decision="approved", **changes):
    return client.post(
        f"{BASE}/{proposal['id']}/review",
        json={"decision": decision, "expected_input_hash": proposal["content_hash"], **changes},
    )


def test_combined_relationship_table_paginates_both_directions_and_tracks_reviews(
    admin_client, database, catalog, monkeypatch
):
    job = queue(admin_client, catalog[0])
    execute(
        monkeypatch,
        job,
        [
            suggestion(catalog),
            suggestion(
                catalog,
                topic_id=str(catalog[2]),
                related_topic_id=str(catalog[0]),
                relation="depends_on",
            ),
            suggestion(catalog, related_topic_id=str(catalog[2]), relation="related_to"),
        ],
    )
    proposals = admin_client.get(BASE).json()["items"]
    rejected = next(row for row in proposals if row["relation"] == "related_to")
    assert review(admin_client, rejected, "rejected").status_code == 200
    with database.begin() as session:
        session.add(
            TopicRelation(topic_id=catalog[0], related_topic_id=catalog[2], relation="part_of")
        )

    def listing(**params):
        response = admin_client.get("/v1/admin/topic-relationships", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    page = listing(topic_id=str(catalog[0]), sort="status")
    assert page["total"] == 3
    assert [row["status"] for row in page["items"]] == ["approved", "pending", "pending"]
    assert page["items"][0]["proposal"] is None
    assert all(row["proposal"]["can_approve"] for row in page["items"][1:])
    assert all(row["topic_name"] and row["related_topic_name"] for row in page["items"])
    pages = [
        listing(topic_id=str(catalog[0]), sort="status", limit=1, offset=offset)
        for offset in range(3)
    ]
    assert all(item["total"] == 3 for item in pages)
    assert [item["items"][0] for item in pages] == page["items"]
    assert listing(topic_id=str(catalog[1]))["total"] == 1
    assert listing(topic_id=str(catalog[2]))["total"] == 2
    assert listing(topic_id=str(catalog[0]), q="JAVASCRIPT")["total"] == 1
    assert listing(topic_id=str(catalog[0]), q="depends_on")["total"] == 1
    assert listing(topic_id=str(catalog[0]), status="pending")["total"] == 2
    assert listing(topic_id=str(catalog[3]))["total"] == 0
    assert (
        admin_client.get("/v1/admin/topic-relationships", params={"sort": "unknown"}).status_code
        == 422
    )

    pending = next(row for row in proposals if row["relation"] == "uses_language")
    assert review(admin_client, pending).status_code == 200
    after = listing(topic_id=str(catalog[0]))
    assert after["total"] == 3  # The approved edge replaces its pending suggestion.
    assert sum(row["status"] == "approved" for row in after["items"]) == 2
    remaining = next(row["proposal"] for row in after["items"] if row["status"] == "pending")
    with database.begin() as session:
        session.get(Topic, catalog[2]).description = "Changed after research"
    stale = listing(topic_id=str(catalog[0]), status="pending")["items"][0]["proposal"]
    assert stale["approval_blocker"] and not stale["can_approve"]
    assert review(admin_client, remaining).status_code == 409
    assert review(admin_client, remaining, "rejected").status_code == 200
    assert listing(topic_id=str(catalog[0]))["total"] == 2


def test_active_only_durable_queue_idempotence_and_optional_comparison(
    admin_client, database, catalog
):
    def request(_):
        with database.begin() as session:
            return request_relationship_analysis(
                session, catalog[0], RelationshipAnalysisRequest(), {"subject": "admin"}
            ).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        identifiers = list(pool.map(request, range(2)))
    assert identifiers[0] == identifiers[1]
    assert queue(admin_client, catalog[0]) == identifiers[0]
    route = f"/v1/admin/topics/{catalog[0]}/relationships/analysis"
    assert admin_client.post(route, json={"related_topic_id": str(catalog[1])}).status_code == 409
    assert admin_client.post(route, json={"related_topic_id": str(catalog[0])}).status_code == 409
    for topic in catalog[3:]:
        assert (
            admin_client.post(
                f"/v1/admin/topics/{topic}/relationships/analysis", json={}
            ).status_code
            == 409
        )
        assert admin_client.post(route, json={"related_topic_id": str(topic)}).status_code == 409
    with database() as session:
        job = session.get(TopicAnalysisJob, identifiers[0])
        assert {row[0] for row in job.input_snapshot["catalog"]} == {str(id) for id in catalog[:3]}
        assert job.proposal_id is None and job.topic_id == catalog[0]
    narrowed = queue(admin_client, catalog[2], related_topic_id=str(catalog[1]))
    with database() as session:
        assert len(session.get(TopicAnalysisJob, narrowed).input_snapshot["catalog"]) == 2


def test_research_flows_through_dispatch_jobs_ui_and_review_without_auto_applying(
    admin_client, database, catalog, monkeypatch
):
    identifier = queue(admin_client, catalog[0])
    enqueued = []

    def enqueue(*args, **kwargs):
        enqueued.append((args, kwargs))
        return SimpleNamespace(id="rq-relationship")

    assert (
        scheduler.dispatch_jobs(
            database, SimpleNamespace(enqueue=enqueue), 10, utcnow(), topic_analyses=True
        )
        == 1
    )
    assert str(identifier) in enqueued[0][0]
    calls = execute(monkeypatch, identifier, [suggestion(catalog)])
    assert calls[0][2] == {"allow_web_search": True}
    assert "application handles review and approval" in calls[0][0]
    assert "relationships" in calls[0][1]["properties"]
    assert count(database, Topic) == 5 and count(database, TopicRelation) == 0
    proposal = admin_client.get(BASE).json()["items"][0]
    assert proposal["status"] == "pending" and proposal["can_approve"]
    assert proposal["created_by"]["subject"] == "integration-admin"
    job = admin_client.get(
        "/v1/admin/jobs/ai-analysis", params={"topic_id": str(catalog[0])}
    ).json()
    assert job["total"] == 1 and job["items"][0]["target_name"] == "React"
    assert job["items"][0]["details"]["outcome"] == "enriched"
    assert job["items"][0]["details"]["result"]["proposal_ids"] == [proposal["id"]]
    assert "snapshots" not in json.dumps(job)
    assert admin_client.get(f"/v1/admin/jobs/topic-analysis/{identifier}/logs").status_code == 200
    assert review(admin_client, proposal, expected_input_hash="0" * 64).status_code == 409
    assert review(admin_client, proposal).status_code == 200
    assert review(admin_client, proposal).status_code == 409
    with database() as session:
        edge = session.scalar(select(TopicRelation))
        assert (edge.topic_id, edge.related_topic_id, edge.relation) == (
            catalog[0],
            catalog[1],
            "uses_language",
        )
        assert edge.evidence_url == "https://react.dev/"
    assert (
        admin_client.delete(
            f"{BASE}/{proposal['id']}",
            params={"expected_input_hash": proposal["content_hash"], "expected_status": "pending"},
        ).status_code
        == 409
    )
    assert (
        admin_client.delete(
            f"{BASE}/{proposal['id']}",
            params={"expected_input_hash": proposal["content_hash"], "expected_status": "approved"},
        ).status_code
        == 204
    )
    assert count(database, TopicRelation) == 1


@pytest.mark.parametrize("when", ["before", "during", "review"])
@pytest.mark.parametrize(
    "field,value", [("status", "rejected"), ("description", "Changed by an admin")]
)
def test_target_changes_prevent_proposals_or_approval(
    admin_client, database, catalog, monkeypatch, when, field, value
):
    identifier = queue(admin_client, catalog[0])

    def change():
        with database.begin() as session:
            setattr(session.get(Topic, catalog[1]), field, value)

    if when == "before":
        change()
    calls = execute(
        monkeypatch, identifier, [suggestion(catalog)], during=change if when == "during" else None
    )
    if when == "before":
        supplied = calls[0][0]
        # The removed target may appear in immutable excluded evidence, but not the catalog.
        assert str(catalog[1]) not in supplied
    if when != "review":
        assert count(database, TopicRelationProposal) == 0
    else:
        proposal = admin_client.get(BASE).json()["items"][0]
        change()
        updated = admin_client.get(f"{BASE}/{proposal['id']}").json()
        assert not updated["can_approve"] and updated["approval_blocker"]
        assert review(admin_client, proposal).status_code == 409
        assert review(admin_client, proposal, "rejected").status_code == 200
    assert count(database, TopicRelation) == 0


@pytest.mark.parametrize("change", ["self", "unknown", "inactive", "no_focal", "private_source"])
def test_invalid_model_edges_roll_back_the_whole_batch(
    admin_client, database, catalog, monkeypatch, change
):
    identifier = queue(admin_client, catalog[0])
    invalid = {
        "self": {"related_topic_id": str(catalog[0])},
        "unknown": {"related_topic_id": str(uuid.uuid4())},
        "inactive": {"related_topic_id": str(catalog[3])},
        "no_focal": {"topic_id": str(catalog[2])},
        "private_source": {"evidence_url": "http://127.0.0.1/internal"},
    }[change]
    execute(monkeypatch, identifier, [suggestion(catalog), suggestion(catalog, **invalid)])
    assert count(database, TopicRelationProposal) == 0
    assert count(database, TopicRelation) == 0
    with database() as session:
        job = session.get(TopicAnalysisJob, identifier)
        assert (
            job.status == "queued" and job.attempts == 1 and job.error == "invalid_analysis_result"
        )
        assert job.result == {}


@pytest.mark.parametrize("change", ["focal_inactive", "focal_edited", "lease_lost"])
def test_running_research_cannot_save_after_focal_change_or_lost_lease(
    admin_client, database, catalog, monkeypatch, change
):
    identifier = queue(admin_client, catalog[0])

    def during():
        with database.begin() as session:
            if change == "lease_lost":
                session.get(TopicAnalysisJob, identifier).lease_token = uuid.uuid4()
            else:
                topic = session.get(Topic, catalog[0])
                if change == "focal_inactive":
                    topic.status = "rejected"
                else:
                    topic.slug = "updated"

    execute(monkeypatch, identifier, [suggestion(catalog)], during=during)
    assert count(database, TopicRelationProposal) == 0
    with database() as session:
        if change != "lease_lost":
            assert session.get(TopicAnalysisJob, identifier).outcome == "superseded"


def test_deduplicates_symmetric_edges_and_respects_rejection_until_topics_change(
    admin_client, database, catalog, monkeypatch
):
    value = suggestion(catalog, relation="related_to")
    identifier = queue(admin_client, catalog[0])
    execute(monkeypatch, identifier, [value, value])
    proposal = admin_client.get(BASE).json()["items"][0]
    assert count(database, TopicRelationProposal) == 1
    assert review(admin_client, proposal, "rejected").status_code == 200
    reverse = suggestion(
        catalog, topic_id=str(catalog[1]), related_topic_id=str(catalog[0]), relation="related_to"
    )
    execute(monkeypatch, queue(admin_client, catalog[1]), [reverse])
    assert count(database, TopicRelationProposal) == 1
    with database.begin() as session:
        session.get(Topic, catalog[0]).description = "A changed topic merits new review"
    execute(monkeypatch, queue(admin_client, catalog[0]), [value])
    proposals = admin_client.get(BASE, params={"status": "pending"}).json()["items"]
    assert len(proposals) == 1 and proposals[0]["id"] != proposal["id"]
    assert review(admin_client, proposals[0]).status_code == 200
    execute(monkeypatch, queue(admin_client, catalog[1]), [reverse])
    assert count(database, TopicRelationProposal) == 2 and count(database, TopicRelation) == 1


@pytest.mark.parametrize("relation,reverse", [("uses_language", False), ("related_to", True)])
def test_parallel_reviews_create_one_edge_and_preserve_manual_evidence(
    admin_client, database, catalog, monkeypatch, relation, reverse
):
    execute(monkeypatch, queue(admin_client, catalog[0]), [suggestion(catalog, relation=relation)])
    proposal = admin_client.get(BASE).json()["items"][0]
    with database.begin() as session:
        session.add(
            TopicRelation(
                topic_id=catalog[1] if reverse else catalog[0],
                related_topic_id=catalog[0] if reverse else catalog[1],
                relation=relation,
                evidence_url="https://example.com/reviewed",
            )
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: review(admin_client, proposal), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    with database() as session:
        assert session.scalar(select(TopicRelation)).evidence_url == "https://example.com/reviewed"


def test_filters_topic_cleanup_and_disabled_ai(admin_client, database, catalog, monkeypatch):
    identifier = queue(admin_client, catalog[0])
    execute(monkeypatch, identifier, [suggestion(catalog)])
    for params in [{"q": "javascript"}, {"topic_id": str(catalog[1])}, {"job_id": str(identifier)}]:
        assert admin_client.get(BASE, params=params).json()["total"] == 1
    for params in [{"q": "%_"}, {"topic_id": str(catalog[2])}, {"status": "rejected"}]:
        assert admin_client.get(BASE, params=params).json()["total"] == 0
    for topic in catalog[:2]:
        assert admin_client.delete(f"/v1/admin/topics/{topic}").status_code == 204
    monkeypatch.setenv("DEVFEED_AI_ENABLED", "false")
    get_settings.cache_clear()
    assert (
        admin_client.post(
            f"/v1/admin/topics/{catalog[2]}/relationships/analysis", json={}
        ).status_code
        == 503
    )
    assert count(database, TopicAnalysisJob) == 0
