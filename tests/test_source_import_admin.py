"""Import is never a trusted source submission, including with full automation enabled."""

import uuid

import pytest
from devfeed_core import discovery
from devfeed_core.config import get_settings
from devfeed_core.discovery_crawler import CrawlSession
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.models import Source, SourceDiscoveryJob
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


@pytest.fixture
def import_feed():
    return (
        '<rss version="2.0"><channel><title>Engineering</title>'
        + "".join(
            f"<item><title>Database query tuning {i}</title><link>https://publisher.example/posts/{i}</link>"
            "<description>How to measure database query plans "
            "and improve index performance.</description>"
            "<pubDate>Mon, 14 Sep 2026 10:00:00 GMT</pubDate></item>"
            for i in range(3)
        )
        + "</channel></rss>"
    ).encode()


def test_import_never_approves_and_manual_review_is_explicit(
    admin_client, database, monkeypatch, import_feed
):
    monkeypatch.setattr(get_settings(), "full_automation", False)
    monkeypatch.setattr(CrawlSession, "get", lambda self, url: FetchResult(200, import_feed, url))
    response = admin_client.post(
        "/v1/admin/source-imports",
        json={"format": "urls", "content": "https://publisher.example/"},
    )
    assert response.status_code == 201, response.text
    assert response.json() == {"created": 1, "existing": 0, "total": 1}
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Source)) == 1
        job = session.scalar(select(SourceDiscoveryJob))
        assert job.result["background"] is True
        assert job.result["assess_after"] is False
    discovery.run_one()
    listing = admin_client.get("/v1/admin/source-imports").json()
    candidate_id = listing["items"][0]["id"]
    path = f"/v1/admin/source-imports/{candidate_id}"
    details = admin_client.get(path)
    assert details.status_code == 200, details.text
    assert details.json()["status"] == "ready"
    assert admin_client.post(path + "/review", json={"action": "approve"}).status_code == 422
    assert (
        admin_client.post(
            path + "/review",
            json={"action": "approve", "note": "Reviewed original technical articles"},
        ).status_code
        == 200
    )
    with database() as session:
        source = session.scalar(select(Source))
        assert source.approval_status == "approved"
        assert source.publication_policy == "manual"
        assert source.reviewed_by == "integration-admin"


def test_ai_import_uses_normal_source_review(admin_client, database, monkeypatch, import_feed):
    from devfeed_aggregator import source_tasks
    from devfeed_core.models import SourceEnrichmentJob, SourceReview

    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    monkeypatch.setattr(get_settings(), "full_automation", True)
    monkeypatch.setattr(CrawlSession, "get", lambda self, url: FetchResult(200, import_feed, url))
    response = admin_client.post(
        "/v1/admin/source-imports", json={"format": "urls", "content": "https://publisher.example/"}
    )
    assert response.status_code == 201
    with database() as session:
        source = session.scalar(select(Source))
        identifier = source.id
        assert source.approval_status == "pending" and not source.enabled
        assert source.feed_url is None
    assert (
        admin_client.post(
            f"/v1/admin/sources/{identifier}/review", json={"decision": "approved"}
        ).status_code
        == 409
    )
    discovery.run_one()
    with database() as session:
        assert (
            session.scalar(select(SourceDiscoveryJob).where(SourceDiscoveryJob.stage == "assess"))
            is None
        )
        job_id = session.scalar(select(SourceEnrichmentJob.id))
        assert session.get(Source, identifier).approval_status == "pending"
    monkeypatch.setattr(
        source_tasks,
        "assess_source",
        lambda *args, **kwargs: {
            "approval_supported": True,
            "reason": "Verified engineering content",
        },
    )
    source_tasks.enrich_source(str(job_id))
    with database() as session:
        source = session.get(Source, identifier)
        assert source.approval_status == "approved"
        assert source.reviewed_by == "devfeed:source-relevance"
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 1


def test_remote_collection_and_duplicate_import(admin_client, monkeypatch):
    body = (
        b'<opml><body><outline text="Publisher" '
        b'htmlUrl="https://publisher.example/" /></body></opml>'
    )
    monkeypatch.setattr(CrawlSession, "get", lambda self, url: FetchResult(200, body, url))
    request = {"format": "opml", "url": "https://collection.example/feeds.opml"}
    assert admin_client.post("/v1/admin/source-imports", json=request).json()["created"] == 1
    assert admin_client.post("/v1/admin/source-imports", json=request).json()["existing"] == 1
    request["url"] = "http://127.0.0.1/private"
    assert admin_client.post("/v1/admin/source-imports", json=request).status_code == 422


def test_import_validation_and_disabled_ai(admin_client):
    for body in [
        {"format": "opml", "content": "<broken"},
        {"format": "urls", "content": "https://publisher.example/\nhttp://localhost/private"},
        {
            "format": "urls",
            "content": "https://publisher.example/",
            "url": "https://collection.example/list",
        },
        {"format": "urls", "content": "https://publisher.example/", "approval_status": "approved"},
    ]:
        assert admin_client.post("/v1/admin/source-imports", json=body).status_code == 422
    assert admin_client.get("/v1/admin/source-imports").json()["total"] == 0
    assert (
        admin_client.post(
            "/v1/admin/source-imports",
            json={"format": "urls", "content": "https://publisher.example/", "review_method": "ai"},
        ).status_code
        == 422
    )


def test_scheduler_dispatches_only_admin_requests(database):
    from devfeed_aggregator.discovery_tasks import dispatch_discovery
    from devfeed_aggregator.queue import get_queue
    from devfeed_core.discovery_import import publisher_hint

    discovery.import_publishers([publisher_hint("https://cli.example/")], "cli")
    discovery.import_publishers(
        [publisher_hint("https://admin.example/")], "admin", background=True
    )
    assert dispatch_discovery(database) == 1
    assert dispatch_discovery(database) == 1
    queue = get_queue()
    try:
        assert queue.count == 1
        job = queue.jobs[0]
        assert job.func_name == "devfeed_aggregator.discovery_tasks.process_candidate"
        target = uuid.UUID(job.args[0])
        with database() as session:
            assert session.get(SourceDiscoveryJob, target).result["background"]
    finally:
        queue.connection.close()


@pytest.mark.parametrize("automatic", [False, True])
def test_import_vetting_uses_system_mode_with_ai_disabled(
    admin_client, database, monkeypatch, automatic
):
    monkeypatch.setattr(get_settings(), "full_automation", automatic)
    monkeypatch.setattr(get_settings(), "ai_enabled", False)
    result = admin_client.post(
        "/v1/admin/source-imports", json={"format": "urls", "content": "https://publisher.example/"}
    )
    assert result.status_code == 201
    with database() as session:
        job = session.scalar(select(SourceDiscoveryJob))
        assert job.result["assess_after"] is automatic
        assert session.scalar(select(func.count()).select_from(Source)) == 1


def test_delete_import_keeps_approved_source(admin_client, database, monkeypatch, import_feed):
    monkeypatch.setattr(get_settings(), "full_automation", False)
    monkeypatch.setattr(CrawlSession, "get", lambda self, url: FetchResult(200, import_feed, url))
    admin_client.post(
        "/v1/admin/source-imports", json={"format": "urls", "content": "https://publisher.example/"}
    )
    discovery.run_one()
    identifier = admin_client.get("/v1/admin/source-imports").json()["items"][0]["id"]
    admin_client.post(
        f"/v1/admin/source-imports/{identifier}/review",
        json={"action": "approve", "note": "Verified original articles"},
    )
    response = admin_client.delete(f"/v1/admin/source-imports/{identifier}")
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 1}
    assert admin_client.get(f"/v1/admin/source-imports/{identifier}").status_code == 404
    from devfeed_core.models import CandidateAssessment, CandidateDiscovery, CandidateFeed

    with database() as session:
        assert session.scalar(select(func.count()).select_from(Source)) == 1
        for model in [CandidateAssessment, CandidateDiscovery, CandidateFeed, SourceDiscoveryJob]:
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_bulk_delete_is_atomic_and_blocks_running_jobs(admin_client, database):
    admin_client.post(
        "/v1/admin/source-imports",
        json={"format": "urls", "content": "https://one.example/\nhttps://two.example/"},
    )
    ids = [row["id"] for row in admin_client.get("/v1/admin/source-imports").json()["items"]]
    with database.begin() as session:
        job = session.scalar(
            select(SourceDiscoveryJob).where(SourceDiscoveryJob.candidate_id == uuid.UUID(ids[1]))
        )
        job.status = "running"
    response = admin_client.post("/v1/admin/source-imports/bulk-delete", json={"ids": ids})
    assert response.status_code == 409
    assert admin_client.get("/v1/admin/source-imports").json()["total"] == 2
    with database.begin() as session:
        job = session.scalar(
            select(SourceDiscoveryJob).where(SourceDiscoveryJob.candidate_id == uuid.UUID(ids[1]))
        )
        job.status = "failed"
    response = admin_client.post("/v1/admin/source-imports/bulk-delete", json={"ids": ids + ids})
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 2}
    assert admin_client.get("/v1/admin/source-imports").json()["total"] == 0
    assert (
        admin_client.post("/v1/admin/source-imports/bulk-delete", json={"ids": []}).status_code
        == 422
    )


def test_pending_sources_include_all_unapproved_import_states(admin_client, database):
    from devfeed_core.models import SourceCandidate

    states = ["pending", "ready", "unresolved", "retry_wait", "rejected", "admitted", "linked"]
    with database.begin() as session:
        for status in states:
            session.add(
                SourceCandidate(
                    name=status,
                    identity_url=f"https://{status}.example/",
                    status=status,
                    approval_status="rejected"
                    if status == "rejected"
                    else "approved"
                    if status in {"admitted", "linked"}
                    else "pending",
                )
            )
    response = admin_client.get("/v1/admin/source-imports?pending_only=true").json()
    assert {row["status"] for row in response["items"]} == set(states[:4])
    assert (
        admin_client.get("/v1/admin/source-imports?pending_only=true&q=ready").json()["total"] == 1
    )


def test_duplicate_entries_and_repeat_imports_are_skipped(admin_client, database):
    from devfeed_core.models import SourceCandidate

    body = {
        "format": "urls",
        "content": "\n".join(["https://duplicate.example/"] * 1001 + ["https://new.example/"]),
    }
    first = admin_client.post("/v1/admin/source-imports", json=body)
    assert first.status_code == 201, first.text
    assert first.json() == {"created": 2, "existing": 0, "total": 2}
    second = admin_client.post("/v1/admin/source-imports", json=body)
    assert second.status_code == 201
    assert second.json() == {"created": 0, "existing": 2, "total": 2}
    with database() as session:
        assert session.scalar(select(func.count()).select_from(SourceCandidate)) == 2
        assert session.scalar(select(func.count()).select_from(SourceDiscoveryJob)) == 2


def test_assessment_is_dispatched_ahead_of_discovery_backlog(database, monkeypatch):
    from devfeed_aggregator.discovery_tasks import dispatch_discovery
    from devfeed_core.models import SourceCandidate

    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    with database.begin() as session:
        for i in range(12):
            row = SourceCandidate(name=str(i), identity_url=f"https://publisher{i}.example/")
            session.add(row)
            session.flush()
            discovery.enqueue(session, row.id, background=True)
        discovery.enqueue(session, row.id, "assess", background=True)
    assert dispatch_discovery(database, limit=1) == 1
    with database() as session:
        dispatched = session.scalar(
            select(SourceDiscoveryJob).where(SourceDiscoveryJob.dispatched_at.is_not(None))
        )
        assert dispatched.stage == "assess"
        crawl = session.scalar(
            select(SourceDiscoveryJob).where(SourceDiscoveryJob.stage == "discover")
        )
        crawl_id = crawl.id
    assert discovery.run_one(target_job_id=crawl_id) is None
    with database() as session:
        assert session.get(SourceDiscoveryJob, crawl_id).attempts == 0


def test_sources_list_combines_pending_imports_and_sources(admin_client, database):
    from devfeed_core.discovery_import import publisher_hint
    from devfeed_core.models import SourceCandidate

    with database.begin() as session:
        session.add(
            Source(
                source_type="publisher",
                name="Alpha",
                feed_url="https://alpha.example/feed",
                approval_status="pending",
            )
        )
    discovery.import_publishers(
        [
            publisher_hint("https://beta.example/", name="Beta"),
            publisher_hint("https://gamma.example/", name="Gamma"),
        ],
        "test",
    )
    response = admin_client.get("/v1/admin/sources?approval_status=pending&limit=2&sort=name")
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 3
    assert [row["name"] for row in page["items"]] == ["Alpha", "Beta"]
    beta = page["items"][1]
    assert "candidate_id" not in beta
    assert beta["approval_status"] == "pending" and beta["feed_url"] is None
    path = f"/v1/admin/sources/{beta['id']}"
    assert admin_client.get(path).status_code == 200
    assert admin_client.patch(path, json={"name": "Beta edited"}).status_code == 200
    assert admin_client.delete(path).status_code == 204
    with database() as session:
        assert session.get(Source, uuid.UUID(beta["id"])) is None
        assert session.scalar(select(SourceCandidate).where(SourceCandidate.name == "Beta")) is None
