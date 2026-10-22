import json
import uuid

import pytest
from devfeed_cli import articles
from devfeed_cli.main import run
from devfeed_core.models import Article, ArticleEnrichmentJob, ArticleOrigin, Source
from devfeed_core.urls import fingerprint
from redis.exceptions import ConnectionError as RedisConnectionError

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
def enrichment_articles(database):
    result = []
    with database.begin() as session:
        for index in range(2):
            source = Source(
                name=f"Engineering {index}",
                feed_url=f"https://example.com/engineering-{index}.xml",
                source_type="publisher",
                approval_status="approved",
            )
            session.add(source)
            session.flush()
            url = f"https://example.com/engineering/{index}"
            article = Article(
                canonical_url=url,
                url_hash=fingerprint(url),
                title=f"Python application testing {index}",
                summary="Python application testing uses isolated fixtures and assertions.",
                metadata_source_type="publisher",
            )
            session.add(article)
            session.flush()
            session.add(
                ArticleOrigin(
                    article_id=article.id,
                    source_id=source.id,
                    entry_key=str(article.id),
                    original_url=url,
                )
            )
            result.append((article.id, source.id))
    return result


def test_enrichment_commands_coalesce_work_and_preserve_failed_retry_history(
    database, enrichment_articles, capsys
):
    article_id = str(enrichment_articles[0][0])
    first = invoke(capsys, "articles", "enrich", article_id)
    assert first["article_id"] == article_id
    assert first["publisher_metadata_present"] is False
    assert first["job"]["status"] == "queued"
    identifier = first["job"]["id"]
    assert invoke(capsys, "articles", "enrich", article_id)["job"]["id"] == identifier
    assert invoke(capsys, "articles", "show", identifier) == first["job"]
    assert "Only failed jobs" in invoke(capsys, "articles", "retry", identifier, code=2)
    with database.begin() as session:
        failed = session.get(ArticleEnrichmentJob, uuid.UUID(identifier))
        failed.status, failed.error = "failed", "publisher_timeout"
    replacement = invoke(capsys, "articles", "retry", identifier)
    assert replacement["job"]["id"] != identifier
    assert replacement["job"]["article_id"] == article_id
    assert replacement["job"]["status"] == "queued"
    old = invoke(capsys, "articles", "show", identifier)
    assert old["status"] == "failed" and old["error"] == "publisher_timeout"
    missing = str(uuid.uuid4())
    for action in ("show", "retry"):
        assert "Article enrichment job not found" in invoke(
            capsys, "articles", action, missing, code=2
        )


def test_enrichment_job_listing_filters_before_pagination(database, enrichment_articles, capsys):
    first, second = [str(row[0]) for row in enrichment_articles]
    failed = invoke(capsys, "articles", "enrich", first)["job"]
    invoke(capsys, "articles", "enrich", second)
    with database.begin() as session:
        session.get(ArticleEnrichmentJob, uuid.UUID(failed["id"])).status = "failed"
    retry = invoke(capsys, "articles", "retry", failed["id"])["job"]
    rows = invoke(capsys, "articles", "jobs", "--article-id", first, "--status", "queued")
    assert [row["id"] for row in rows] == [retry["id"]]
    first_page = invoke(capsys, "articles", "jobs", "--article-id", first, "--limit", "1")
    second_page = invoke(
        capsys, "articles", "jobs", "--article-id", first, "--limit", "1", "--offset", "1"
    )
    assert [row["id"] for row in first_page] == [retry["id"]]
    assert [row["id"] for row in second_page] == [failed["id"]]
    assert len(invoke(capsys, "articles", "jobs")) == 3


def test_enrichment_backfill_scopes_by_source_and_skips_previously_attempted_articles(
    database, enrichment_articles, capsys
):
    article_id, source_id = enrichment_articles[0]
    result = invoke(capsys, "articles", "backfill", "--source-id", str(source_id))
    assert result["queued"] == 1
    assert result["jobs"][0]["article_id"] == str(article_id)
    assert invoke(capsys, "articles", "backfill", "--source-id", str(source_id)) == {
        "queued": 0,
        "jobs": [],
    }
    remaining = invoke(capsys, "articles", "backfill")
    assert remaining["queued"] == 1
    assert remaining["jobs"][0]["article_id"] == str(enrichment_articles[1][0])


@pytest.mark.parametrize("action", ["enrich", "retry", "backfill"])
def test_immediate_enrichment_dispatch_keeps_committed_job_when_redis_fails(
    database, enrichment_articles, monkeypatch, capsys, action
):
    article_id, source_id = enrichment_articles[0]
    identifier = str(article_id)
    if action == "retry":
        old = invoke(capsys, "articles", "enrich", identifier)["job"]
        identifier = old["id"]
        with database.begin() as session:
            session.get(ArticleEnrichmentJob, uuid.UUID(identifier)).status = "failed"
    dispatched = []

    def fail_dispatch(job_id, *, kind):
        assert kind == "article-enrichment"
        with database() as session:
            committed = session.get(ArticleEnrichmentJob, job_id)
            assert committed.article_id == article_id and committed.status == "queued"
        dispatched.append(job_id)
        raise RedisConnectionError("Redis unavailable")

    monkeypatch.setattr(articles, "dispatch_now", fail_dispatch)
    arguments = (
        ["backfill", "--source-id", str(source_id), "--dispatch"]
        if action == "backfill"
        else [action, identifier, "--force"]
    )
    assert "Redis is unavailable" in invoke(capsys, "articles", *arguments, code=1)
    assert len(dispatched) == 1
    queued = invoke(
        capsys, "articles", "jobs", "--article-id", str(article_id), "--status", "queued"
    )
    assert [row["id"] for row in queued] == [str(dispatched[0])]
