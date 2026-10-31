"""Synthetic active card observations that exercise catalog freshness and ranking."""

import uuid
from datetime import UTC, datetime, timedelta

from devfeed_core import analysis
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleOrigin,
    Source,
    Tag,
    Topic,
)
from devfeed_core.urls import fingerprint
from sqlalchemy import insert

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def identifier(kind, number):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"devfeed-card-catalog:{kind}:{number}")


def seed(factory, *, tags, topics, articles=12, sources=8):
    """Keep real migrated revision triggers enabled throughout fixture insertion."""
    with factory.begin() as session:
        session.execute(
            insert(Topic),
            [
                dict(
                    id=identifier("topic", number),
                    name=f"FixtureTechnology{number:04d}",
                    slug=f"fixture-technology-{number}",
                    status="active",
                    kind="technology",
                    aliases=[f"FixtureAlias{number:04d}"],
                    keywords=[f"fixture-keyword-{number}"],
                    description=f"Development tools and application routing platform {number}.",
                )
                for number in range(topics)
            ],
        )
        session.execute(
            insert(Tag),
            [
                dict(
                    id=identifier("tag", number),
                    name=f"FixtureTag{number:05d}",
                    slug=f"fixture-tag-{number}",
                    aliases=[f"FixtureTagAlias{number:05d}"],
                )
                for number in range(tags)
            ],
        )
        publishers = [
            Source(
                id=identifier("source", number),
                name=f"Fixture publisher {number}",
                feed_url=f"https://fixture-{number}.example/feed?token=private-fixture-value",
                source_type="publisher",
                approval_status="approved",
                enabled=True,
                publication_policy="manual",
            )
            for number in range(sources)
        ]
        session.add_all(publishers)
        session.flush()
        taxonomy = analysis.catalog(session)
        with analysis.reuse_candidates():
            for number in range(articles):
                url = f"https://fixture.example/article/{number}"
                article = Article(
                    id=identifier("article", number),
                    canonical_url=url,
                    url_hash=fingerprint(url),
                    title=f"FixtureTechnology0000 routing tutorial {number}",
                    summary=(
                        f"FixtureTechnology0000 organizes application routing "
                        f"and navigation {number}."
                    ),
                    ai_summary="A guide to routing in applications.",
                    ai_description="Build application routing and navigation.",
                    language="en",
                    content_type="tutorial",
                    discovered_at=NOW - timedelta(hours=1),
                    feed_at=NOW - timedelta(hours=1),
                )
                session.add(article)
                session.flush()
                session.add(
                    ArticleOrigin(
                        article_id=article.id,
                        source_id=publishers[number % sources].id,
                        entry_key=str(article.id),
                        original_url=url,
                    )
                )
                source = analysis.source_snapshot(article, None)
                candidates = analysis.analysis_candidates(taxonomy, source)
                job = ArticleAnalysisJob(
                    id=identifier("analysis", number),
                    article_id=article.id,
                    status="succeeded",
                    outcome="applied",
                    input_hash=analysis.snapshot_hash(source),
                    catalog_hash=analysis.snapshot_hash(candidates),
                    catalog_snapshot=candidates,
                    prompt_version=analysis.PROMPT_VERSION,
                    result={"outcome": "ready", "topics": [], "tags": []},
                    finished_at=NOW - timedelta(minutes=10),
                )
                article.classification_provenance = {
                    "analysis_id": str(job.id),
                    "input_hash": job.input_hash,
                }
                session.add_all(
                    [
                        job,
                        ArticleEnrichmentJob(
                            article_id=article.id,
                            status="succeeded",
                            outcome="metadata_only",
                            result={"input_url": article.canonical_url},
                            finished_at=NOW - timedelta(minutes=15),
                        ),
                    ]
                )
    return (
        [str(identifier("article", number)) for number in range(articles)],
        [str(item.id) for item in publishers],
    )
