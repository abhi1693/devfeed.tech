"""Actual analysis-worker PostgreSQL work, including dependency failures and catalog churn."""

import hashlib
import json
import os
import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from devfeed_aggregator import analysis_tasks
from devfeed_aggregator.codex_client import AnalysisError
from devfeed_core import analysis, catalog_cache
from devfeed_core.cache import CacheUnavailable
from devfeed_core.config import get_settings
from devfeed_core.models import Article, ArticleAnalysisJob, ArticleOrigin, Source, Tag, Topic
from redis import Redis
from sqlalchemy import insert, text

from tests.support.article_step_helpers import article_step_client
from tests.support.postgres_work import postgres_work

pytestmark = pytest.mark.integration


def unavailable(operation):
    raise CacheUnavailable("Injected cache transport outage")


def seed(factory, tags, topics):
    with factory.begin() as session:
        session.execute(text("TRUNCATE articles,sources,topics,tags CASCADE"))
        taxonomy = [
            Topic(
                id=uuid.uuid5(uuid.NAMESPACE_URL, f"pg-topic:{number}"),
                name="Angular" if number == 0 else f"Fixture topic {number}",
                slug=f"pg-topic-{number}",
                kind="technology",
                status="active",
                description="Application routing for developers.",
            )
            for number in range(topics)
        ]
        session.add_all(taxonomy)
        session.execute(
            insert(Tag),
            [
                {
                    "id": uuid.uuid5(uuid.NAMESPACE_URL, f"pg-tag:{number}"),
                    "name": f"Fixture tag {number}",
                    "slug": f"pg-tag-{number}",
                }
                for number in range(tags)
            ],
        )
        source = Source(
            name="Publisher",
            feed_url="https://fixture.example/feed",
            source_type="publisher",
            approval_status="approved",
            enabled=True,
            publication_policy="manual",
        )
        url = "https://fixture.example/angular-routing"
        article = Article(
            canonical_url=url,
            url_hash=hashlib.sha256(url.encode()).hexdigest(),
            title="Angular routing",
            summary="Angular routing helps developers build navigation and organize applications.",
        )
        session.add_all([source, article])
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=article.id, source_id=source.id, entry_key="pg-work", original_url=url
            )
        )
        session.flush()
        job = analysis.request_analysis(session, article.id)
        return article.id, job.id, taxonomy[0].id


@pytest.mark.parametrize(
    "mode", ["disabled", "cold", "warm", "outage", "churn", "disabled_churn", "early_failure"]
)
def test_job_catalog_reuse_does_not_transfer_work_to_postgres(database, monkeypatch, mode):
    tags = int(os.environ.get("DEVFEED_ANALYSIS_PG_TAGS", "256"))
    topics = int(os.environ.get("DEVFEED_ANALYSIS_PG_TOPICS", "1"))
    assert 1 <= tags <= 100_000 and 1 <= topics <= 10_000
    settings, engine = get_settings(), database.kw["bind"]
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(analysis_tasks, "session_factory", lambda: database)
    reports, outcomes = [], []
    for retained in (False, True):
        monkeypatch.setattr(settings, "cache_enabled", False)
        monkeypatch.setattr(settings, "analysis_catalog_reuse_enabled", retained)
        identifier, job_id, topic_id = seed(database, tags, topics)
        monkeypatch.setattr(settings, "cache_enabled", mode not in {"disabled", "disabled_churn"})
        with Redis.from_url(settings.redis_url) as transport:
            transport.flushdb()
        if mode == "warm":
            with database() as session:
                analysis.catalog(session)
        calls = []

        def complete(*args, calls=calls, topic_id=topic_id):
            assert measured["active_connections"] == 0
            calls.append(True)
            if mode == "early_failure":
                raise AnalysisError("codex_timeout")
            if mode in {"churn", "disabled_churn"} and len(calls) == 1:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE topics SET description='Changed routing definition' "
                            "WHERE id=:id"
                        ),
                        {"id": topic_id},
                    )
            return {
                "outcome": "ready",
                "developer_relevance": "relevant",
                "language": "en",
                "content_type": "tutorial",
                "content_format": "article",
                "ai_summary": "Angular routing for developers.",
                "ai_description": "A guide to Angular routing for application developers.",
                "ai_title": None,
                "title_evidence": None,
                "page_kind": "article",
                "topics": [
                    {
                        "topic_id": str(topic_id),
                        "role": "primary",
                        "relevance": 0.9,
                        "evidence": "Angular",
                    }
                ],
                "tags": [],
                "reasons": [],
            }

        monkeypatch.setattr(
            analysis_tasks,
            "CodexClient",
            lambda _, complete=complete: article_step_client(complete=complete),
        )
        fault = SimpleNamespace(namespace="fixture", _run=unavailable)
        with (
            patch.object(catalog_cache, "get_cache", lambda fault=fault: fault)
            if mode == "outage"
            else nullcontext(),
            postgres_work(engine) as measured,
        ):
            analysis_tasks.analyze_article(str(job_id))
        assert measured["active_connections"] == 0 and measured["peak_connections"] == 1
        with database() as session:
            job, article = session.get(ArticleAnalysisJob, job_id), session.get(Article, identifier)
            outcomes.append(
                (
                    job.status,
                    job.outcome,
                    article.ai_summary,
                    article.publication_status,
                    len(calls),
                )
            )
        reports.append(dict(measured))
    assert outcomes[0] == outcomes[1]
    assert reports[1]["sql_calls"] <= reports[0]["sql_calls"]
    assert reports[1]["transactions"] <= reports[0]["transactions"]
    if "server" in reports[0]:
        assert reports[1]["server"]["calls"] <= reports[0]["server"]["calls"]
        assert reports[1]["server"]["rows"] <= reports[0]["server"]["rows"]
    print(
        "ANALYSIS_PG_WORK="
        + json.dumps(
            {"mode": mode, "tags": tags, "topics": topics, "runs": reports}, sort_keys=True
        )
    )
