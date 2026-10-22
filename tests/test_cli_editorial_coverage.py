import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_aggregator.queue import get_queue
from devfeed_cli import editorial
from devfeed_cli.main import run
from devfeed_core.config import get_settings
from devfeed_core.models import Article, ArticleAnalysisJob, ArticleOrigin, Source, utcnow
from devfeed_core.urls import fingerprint
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select

pytestmark = pytest.mark.integration


def invoke(capsys, *arguments, code=0):
    result = run(list(arguments))
    output = capsys.readouterr()
    assert result == code, output.err
    if code:
        assert output.out == ""
        return output.err
    return json.loads(output.out)


@pytest.fixture
def editorial_articles(database):
    identifiers = [uuid.UUID(int=index) for index in range(1, 5)]
    with database.begin() as session:
        source = Source(
            name="Engineering",
            feed_url="https://example.com/engineering.xml",
            source_type="publisher",
            approval_status="approved",
        )
        session.add(source)
        session.flush()
        for index, identifier in enumerate(identifiers):
            url = f"https://example.com/engineering/{identifier}"
            session.add(
                Article(
                    id=identifier,
                    canonical_url=url,
                    url_hash=fingerprint(url),
                    title=f"Python application testing {index}",
                    summary="Python application testing uses isolated fixtures and assertions.",
                    metadata_source_type="publisher",
                    review_status="approved" if index == 3 else "pending",
                    publication_status="published" if index == 3 else "unpublished",
                    classification_provenance={"page_kind": "article"},
                )
            )
            session.flush()
            session.add(
                ArticleOrigin(
                    article_id=identifier,
                    source_id=source.id,
                    entry_key=str(identifier),
                    original_url=url,
                )
            )
    return identifiers


def test_article_listing_applies_filters_and_cursor_before_pagination(
    database, editorial_articles, capsys
):
    first, second, third, published = map(str, editorial_articles)
    page = invoke(
        capsys,
        "articles",
        "list",
        "--after",
        first,
        "--review-status",
        "pending",
        "--publication-status",
        "unpublished",
        "--limit",
        "1",
    )
    assert [item["id"] for item in page["items"]] == [second]
    assert page["next_after"] == second
    page = invoke(capsys, "articles", "list", "--after", second, "--review-status", "pending")
    assert [item["id"] for item in page["items"]] == [third]
    assert page["next_after"] is None
    assert len(invoke(capsys, "articles", "list")["items"]) == 4
    assert invoke(capsys, "articles", "list", "--after", published)["items"] == []
    published_page = invoke(capsys, "articles", "list", "--publication-status", "published")
    assert [item["id"] for item in published_page["items"]] == [published]
    article = invoke(capsys, "articles", "inspect", first)
    assert article["review_status"] == "pending"
    assert "not_approved" in article["publication_blockers"]
    assert "Article not found" in invoke(capsys, "articles", "inspect", str(uuid.uuid4()), code=2)


def test_classification_and_decisions_preserve_revision_and_audit_contract(
    database, editorial_articles, tmp_path, capsys
):
    identifier = str(editorial_articles[0])
    body = {
        "developer_relevance": "relevant",
        "page_kind": "article",
        "language": "en",
        "content_type": "tutorial",
        "content_format": "article",
        "topics": [],
        "tags": [],
        "actor": "operator",
        "note": "Checked publisher evidence",
        "expected_revision": 0,
    }
    path = tmp_path / "classification.json"
    path.write_text(json.dumps(body))
    classified = invoke(capsys, "articles", "classify", identifier, "--file", str(path))
    assert classified["editorial_revision"] == 1
    assert classified["classification_provenance"]["origin"] == "manual"
    assert classified["language"] == "en" and classified["content_type"] == "tutorial"
    assert "Article changed" in invoke(
        capsys, "articles", "classify", identifier, "--file", str(path), code=2
    )
    preview = invoke(capsys, "articles", "approve", identifier, "--revision", "1", "--dry-run")
    assert preview["dry_run"] is True
    assert preview["article"]["review_status"] == "pending"
    approved = invoke(
        capsys,
        "articles",
        "approve",
        identifier,
        "--revision",
        "1",
        "--by",
        "operator",
        "--note",
        "Reviewed classification",
    )
    assert approved["article"]["review_status"] == "approved"
    assert approved["article"]["publication_status"] == "unpublished"
    assert approved["article"]["editorial_revision"] == 2
    history = invoke(capsys, "articles", "review-history", identifier)
    assert [(row["action"], row["revision"]) for row in history] == [
        ("approve", 2),
        ("classify", 1),
    ]
    assert history[0]["actor"] == "operator"
    assert history[0]["note"] == "Reviewed classification"
    assert len(invoke(capsys, "articles", "review-history", identifier, "--limit", "1")) == 1


@pytest.mark.parametrize("action", ["analyze", "analysis-backfill"])
def test_analysis_requires_explicit_ai_enablement(database, capsys, action):
    arguments = ["articles", action]
    if action == "analyze":
        arguments.append(str(uuid.uuid4()))
    assert "Configure" in invoke(capsys, *arguments, code=2)
    with database() as session:
        assert session.scalar(select(ArticleAnalysisJob.id)) is None


def test_analysis_retry_preserves_failed_history_and_omits_private_snapshots(
    database, editorial_articles, capsys
):
    get_settings().ai_enabled = True
    article_id = editorial_articles[0]
    with database.begin() as session:
        old = ArticleAnalysisJob(
            article_id=article_id,
            status="failed",
            error="model_unavailable",
            input_snapshot={"text": "private source evidence"},
            catalog_snapshot={"private": "catalog evidence"},
        )
        unrelated = ArticleAnalysisJob(article_id=editorial_articles[1])
        session.add_all([old, unrelated])
        session.flush()
        old_id = old.id
    replacement = invoke(capsys, "articles", "analysis-retry", str(old_id))
    assert replacement["id"] != str(old_id)
    assert replacement["article_id"] == str(article_id)
    assert replacement["status"] == "queued"
    assert "input_snapshot" not in replacement and "catalog_snapshot" not in replacement
    same = invoke(capsys, "articles", "analyze", str(article_id))
    assert same["id"] == replacement["id"]
    rows = invoke(capsys, "articles", "analyses", "--article-id", str(article_id))
    assert {row["id"] for row in rows} == {str(old_id), replacement["id"]}
    assert next(row for row in rows if row["id"] == str(old_id))["error"] == "model_unavailable"
    assert all("input_snapshot" not in row and "catalog_snapshot" not in row for row in rows)
    assert len(invoke(capsys, "articles", "analyses", "--limit", "1")) == 1
    assert "Only failed" in invoke(capsys, "articles", "analysis-retry", replacement["id"], code=2)
    assert "Analysis job not found" in invoke(
        capsys, "articles", "analysis-retry", str(uuid.uuid4()), code=2
    )


def test_analysis_backfill_cursor_and_immediate_dispatch_publish_saved_jobs(
    database, editorial_articles, capsys
):
    get_settings().ai_enabled = True
    first, second, third, _ = map(str, editorial_articles)
    queued = invoke(capsys, "articles", "analysis-backfill", "--limit", "1")
    assert queued["scanned"] == queued["queued"] == 1
    assert queued["next_after"] == first
    assert queued["jobs"][0]["article_id"] == first
    assert queued["jobs"][0]["dispatched_at"] is None
    dispatched = invoke(
        capsys,
        "articles",
        "analysis-backfill",
        "--after",
        first,
        "--limit",
        "1",
        "--force",
        "--dispatch",
    )
    assert dispatched["next_after"] == second
    assert dispatched["jobs"][0]["article_id"] == second
    assert dispatched["jobs"][0]["dispatched_at"] is not None
    forced = invoke(capsys, "articles", "analyze", third, "--force")
    assert forced["article_id"] == third and forced["dispatched_at"] is not None
    assert invoke(capsys, "articles", "analysis-backfill", "--force") == {
        "scanned": 0,
        "queued": 0,
        "next_after": None,
        "jobs": [],
    }
    queue = get_queue("article-analysis")
    try:
        assert {job.args[0] for job in queue.jobs} == {
            dispatched["jobs"][0]["id"],
            forced["id"],
        }
        assert all(
            job.func_name == "devfeed_aggregator.analysis_tasks.analyze_article"
            for job in queue.jobs
        )
    finally:
        queue.connection.close()


def test_analysis_dispatch_commits_due_override_and_closes_redis_on_failure(
    database, editorial_articles, monkeypatch, capsys
):
    future = utcnow() + timedelta(hours=1)
    with database.begin() as session:
        job = ArticleAnalysisJob(
            article_id=editorial_articles[0],
            available_at=future,
            dispatched_at=utcnow(),
            attempts=2,
        )
        session.add(job)
        session.flush()
        identifier = job.id
    closed = []
    monkeypatch.setattr(
        editorial,
        "get_queue",
        lambda _: SimpleNamespace(connection=SimpleNamespace(close=lambda: closed.append(True))),
    )

    def fail_dispatch(*args, **kwargs):
        with database() as session:
            committed = session.get(ArticleAnalysisJob, identifier)
            assert committed.available_at < future
            assert committed.dispatched_at is None and committed.attempts == 2
        raise RedisConnectionError("Redis unavailable")

    monkeypatch.setattr(editorial, "dispatch_jobs", fail_dispatch)
    assert "Redis is unavailable" in invoke(
        capsys, "articles", "analysis-dispatch", str(identifier), code=1
    )
    assert closed == [True]
    with database.begin() as session:
        session.get(ArticleAnalysisJob, identifier).status = "running"
    assert "Only queued" in invoke(capsys, "articles", "analysis-dispatch", str(identifier), code=2)
    assert "Analysis job not found" in invoke(
        capsys, "articles", "analysis-dispatch", str(uuid.uuid4()), code=2
    )
