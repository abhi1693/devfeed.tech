"""Source entry points share records, review work, and administration."""

import json

import pytest
from devfeed_core import discovery
from devfeed_core.config import get_settings
from devfeed_core.models import Source, SourceCandidate, SourceDiscoveryJob, SourceEnrichmentJob
from devfeed_core.source_relevance import schedule_pending_reviews
from sqlalchemy import select

pytestmark = pytest.mark.integration


def test_website_creation_enters_the_same_discovery_queue(admin_client, database, monkeypatch):
    response = admin_client.post(
        "/v1/admin/sources",
        json={
            "name": "Example engineering",
            "website_url": "https://engineering.example/",
            "source_type": "publisher",
        },
    )
    assert response.status_code == 201, response.text
    source = response.json()
    assert source["approval_status"] == "pending" and source["feed_url"] is None
    with database() as session:
        candidate = session.scalar(select(SourceCandidate))
        assert str(candidate.source_id) == source["id"]
        assert session.scalar(select(SourceDiscoveryJob)).result["background"]
    repeated = admin_client.post(
        "/v1/admin/source-imports",
        json={
            "format": "urls",
            "content": "https://engineering.example/",
        },
    )
    assert repeated.json() == {"created": 0, "existing": 1, "total": 1}
    assert admin_client.get("/v1/admin/sources?approval_status=pending").json()["total"] == 1


def test_mode_change_queues_review_once_without_approving(database, monkeypatch):
    with database.begin() as session:
        source = Source(
            name="Pending",
            feed_url="https://engineering.example/feed",
            source_type="publisher",
            approval_status="pending",
            enabled=False,
        )
        session.add(source)
        session.flush()
        session.add(SourceEnrichmentJob(source_id=source.id, status="succeeded"))
    assert schedule_pending_reviews(database) == 0
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    monkeypatch.setattr(get_settings(), "full_automation", True)
    assert schedule_pending_reviews(database) == 1
    assert schedule_pending_reviews(database) == 0
    with database() as session:
        assert session.scalar(select(Source)).approval_status == "pending"


def test_worker_identifies_source_discovery(database):
    from devfeed_admin_api.workers import current_jobs
    from devfeed_core.discovery_import import publisher_hint
    from devfeed_core.models import utcnow
    from test_admin_workers import TelemetryStore

    discovery.import_publishers(
        [publisher_hint("https://engineering.example/", name="Engineering")], "test"
    )
    with database() as session:
        job = session.scalar(select(SourceDiscoveryJob))
        identifier = str(job.id)
        source_id = session.scalar(select(Source.id))
    store = TelemetryStore()
    store.hashes["rq:job:delivery"] = {
        "data": json.dumps(
            ["devfeed_aggregator.discovery_tasks.process_candidate", None, [identifier], {}]
        ).encode()
    }
    with database() as session:
        result = current_jobs(store, session, ["delivery"], utcnow())["delivery"]
        assert result.kind == "source-discovery"
        assert result.target_name == "Engineering"
        assert result.source_id == source_id


def test_website_edit_supersedes_inflight_discovery(admin_client, database, monkeypatch):
    response = admin_client.post(
        "/v1/admin/sources",
        json={
            "name": "Engineering",
            "website_url": "https://old.example/blog/",
            "source_type": "publisher",
        },
    )
    identifier = response.json()["id"]

    def changed(*args):
        assert (
            admin_client.patch(
                f"/v1/admin/sources/{identifier}", json={"website_url": "https://new.example/blog/"}
            ).status_code
            == 200
        )
        return {"feeds": [], "retryable": False}

    monkeypatch.setattr(discovery, "discover", changed)
    assert discovery.run_one()["status"] == "superseded"
    with database() as session:
        source = session.scalar(select(Source))
        candidate = session.scalar(select(SourceCandidate))
        assert source.feed_url is None and source.approval_status == "pending"
        assert candidate.identity_url == "https://new.example/blog"
        assert (
            len(
                list(
                    session.scalars(
                        select(SourceDiscoveryJob).where(SourceDiscoveryJob.status == "queued")
                    )
                )
            )
            == 1
        )


@pytest.mark.parametrize("decision", ["approved", "rejected"])
@pytest.mark.parametrize("actor", ["admin:reviewer", "devfeed:source-relevance"])
def test_import_list_uses_linked_source_decision(admin_client, database, decision, actor):
    from devfeed_core.discovery_import import publisher_hint
    from devfeed_core.schemas import SourceDecision
    from devfeed_core.services import review_source

    identifier = discovery.import_publishers(
        [publisher_hint("https://publisher.example/")], "https://collection.example/feed.opml"
    )["candidates"][0]
    with database.begin() as session:
        candidate = session.scalar(select(SourceCandidate))
        source = session.get(Source, candidate.source_id)
        source.feed_url = "https://publisher.example/rss"
        review_source(
            session, source.id, SourceDecision(decision=decision, actor=actor, note="Reviewed")
        )
        assert candidate.approval_status == "pending"
    listing = admin_client.get("/v1/admin/source-imports").json()
    assert listing["total"] == 1
    assert listing["items"][0]["approval_status"] == decision
    detail = admin_client.get(f"/v1/admin/source-imports/{identifier}").json()
    assert detail["approval_status"] == decision
    assert admin_client.get("/v1/admin/source-imports?pending_only=true").json()["total"] == 0


def test_website_correction_invalidates_old_opml_hints(admin_client, database, monkeypatch):
    from devfeed_core.discovery_import import publisher_hint
    from devfeed_core.models import CandidateDiscovery

    origin = "https://collection.example/publishers.opml"
    identifier = discovery.import_publishers(
        [publisher_hint("https://old.example/", feed="https://old.example/atom.xml")], origin
    )["candidates"][0]

    def old_discovery(homepage, hints):
        assert hints == ["https://old.example/atom.xml"]
        response = admin_client.patch(
            f"/v1/admin/sources/{identifier}", json={"website_url": "https://correct.example/"}
        )
        assert response.status_code == 200, response.text
        return {"feeds": [], "retryable": False}

    monkeypatch.setattr(discovery, "discover", old_discovery)
    assert discovery.run_one()["status"] == "superseded"

    def corrected_discovery(homepage, hints):
        assert homepage == "https://correct.example/"
        assert hints == []
        return {"feeds": [], "retryable": False}

    monkeypatch.setattr(discovery, "discover", corrected_discovery)
    assert discovery.run_one()["status"] == "succeeded"
    with database() as session:
        provenance = session.scalar(select(CandidateDiscovery))
        assert provenance.origin == origin
        assert provenance.feed_hint is None
        assert session.scalar(select(Source)).feed_url is None


@pytest.mark.parametrize(
    "version,status,job_status,expected",
    [
        ("source-relevance-v3", "pending", "succeeded", 1),
        ("source-relevance-v4", "pending", "succeeded", 1),
        ("source-relevance-v5", "pending", "succeeded", 0),
        ("source-relevance-v3", "approved", "succeeded", 0),
        ("source-relevance-v3", "rejected", "succeeded", 0),
        ("source-relevance-v3", "pending", "queued", 0),
        ("source-relevance-v3", "pending", "failed", 0),
    ],
)
def test_policy_change_requeues_only_eligible_old_pending_reviews(
    database, monkeypatch, version, status, job_status, expected
):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    monkeypatch.setattr(get_settings(), "full_automation", True)
    with database.begin() as session:
        source = Source(
            name="Mixed editorial source",
            feed_url="https://engineering.example/feed",
            source_type="publisher",
            approval_status=status,
            relevance_assessment={"version": version, "relevance": "relevant"},
        )
        session.add(source)
        session.flush()
        session.add(SourceEnrichmentJob(source_id=source.id, status=job_status))
    assert schedule_pending_reviews(database) == expected
    assert schedule_pending_reviews(database) == 0
