"""Exercise real read-model workers and authenticated APIs on disposable local fixtures.

Set DEVFEED_TEST_DATABASE_URL (local PostgreSQL, database ending in _test) and
DEVFEED_TEST_REDIS_URL (local Redis database 15). --seed replaces that database's
contents. Full scale exceeds the measured production population; --scale .001
is a quick functional check. No production records or credentials are copied.
"""

import argparse
import asyncio
import contextvars
import hashlib
import json
import math
import multiprocessing
import os
import platform
import resource
import secrets
import sys
import threading
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
from alembic import command
from alembic.config import Config
from devfeed_core import models
from devfeed_core.analysis import reuse_candidates
from devfeed_core.catalog_cache import reuse_snapshots
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Integer,
    String,
    Uuid,
    event,
    func,
    insert,
    literal,
    literal_column,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
COUNTS = {
    "articles": 150000,
    "article_analysis_jobs": 200000,
    "article_enrichment_jobs": 150000,
    "article_image_jobs": 150000,
    "article_reviews": 300000,
    "article_origins": 151000,
    "ingestion_jobs": 150000,
    "article_contents": 100000,
    "topics": 5000,
    "tags": 60000,
    "inference_calls": 400000,
    "sources": 2000,
    "source_candidates": 1000,
    "partner_assets": 500,
    "partner_tracked_daily_metrics": 1000,
    "article_topics": 500000,
    "article_tags": 1000000,
}
PRODUCTION_BASELINE = {
    "articles": 86248,
    "active_articles": 15492,
    "article_analysis_jobs": 141103,
    "article_enrichment_jobs": 98244,
    "article_image_jobs": 86929,
    "article_reviews": 236064,
    "article_origins": 87615,
    "ingestion_jobs": 90914,
    "article_contents": 83251,
    "topics": 4450,
    "tags": 46162,
    "inference_calls": 328015,
    "sources": 1385,
    "source_candidates": 712,
    "partner_assets": 456,
    "partner_tracked_daily_metrics": 689,
    "article_topics": 341241,
    "article_tags": 808914,
}
REQUEST_TAG = contextvars.ContextVar("read_model_profile_request", default=None)
ISSUER = "https://profile.invalid"
AUTH_ENV = {
    "DEVFEED_ADMIN_BASE_URL": "http://profile.test",
    "DEVFEED_PARTNER_BASE_URL": "http://profile.test",
    "DEVFEED_ADMIN_COOKIE_SECURE": "false",
    "DEVFEED_ADMIN_REQUIRED_ROLE": "superuser",
    "DEVFEED_PARTNER_COOKIE_SECURE": "false",
    "DEVFEED_OIDC_ISSUER_URL": ISSUER,
    "DEVFEED_OIDC_CLIENT_ID": "local-profile",
    "DEVFEED_OIDC_ORGANIZATION_ID": "profile",
    "DEVFEED_OIDC_CLIENT_SECRET": "",
    "DEVFEED_OIDC_TOKEN_ENDPOINT_AUTH_METHOD": "none",
}
ISOLATED_ENV = {
    "DEVFEED_REDIS_SENTINEL_NODES": "[]",
    "DEVFEED_REDIS_SENTINEL_MASTER": "",
    "DEVFEED_REDIS_SENTINEL_USERNAME": "",
    "DEVFEED_REDIS_SENTINEL_PASSWORD": "",
    "DEVFEED_REDIS_SENTINEL_SSL": "false",
}


def validate_targets(database_url, redis_url, *, environment=None):
    environment = os.environ if environment is None else environment
    if any(environment.get(name) for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")):
        raise ValueError("Profiling refuses inherited libpq host/service routing overrides")
    database = make_url(database_url)
    redis = urlsplit(redis_url)
    if database.get_backend_name() != "postgresql" or database.host not in {
        "127.0.0.1",
        "localhost",
    }:
        raise ValueError("Profiling requires explicitly local PostgreSQL")
    if not (database.database or "").endswith("_test"):
        raise ValueError("Profiling database must end in _test; --seed truncates its contents")
    if database.query:
        raise ValueError("Profiling PostgreSQL URLs must not contain connection query overrides")
    if (
        redis.scheme not in {"redis", "rediss"}
        or redis.hostname not in {"127.0.0.1", "localhost"}
        or redis.path != "/15"
        or redis.query
        or redis.fragment
    ):
        raise ValueError("Profiling requires explicitly local Redis database 15")
    return database


def fixture_counts(scale):
    if not 0 < scale <= 2:
        raise ValueError("Fixture scale must be greater than zero and at most two")
    counts = {name: max(1, math.ceil(total * scale)) for name, total in COUNTS.items()}
    counts["articles"] = max(40, counts["articles"])
    counts["sources"] = max(10, counts["sources"])
    counts["source_candidates"] = max(8, counts["source_candidates"])
    counts["topics"] = max(5, counts["topics"])
    counts["tags"] = max(10, counts["tags"])
    counts["partner_assets"] = max(5, counts["partner_assets"])
    counts["partner_tracked_daily_metrics"] = max(10, counts["partner_tracked_daily_metrics"])
    counts["active_articles"] = counts["articles"] // 5
    return counts


def fixture_id(kind, number):
    digest = hashlib.sha256(f"read-model-profile:{kind}:{number}".encode()).hexdigest()
    return uuid.UUID(digest[:32])


def identifier(kind, number="n"):
    return (
        "substring(encode(sha256(convert_to("
        f"'read-model-profile:{kind}:' || ({number})::text,'UTF8')),'hex'),1,32)::uuid"
    )


def seed_rows(connection, model, count, overrides=None, *, namespace=None, where=None, bytes_=8192):
    """Use generated SQL rows and ordinary triggers, including real defaults/constraints."""
    overrides = overrides or {}
    namespace = namespace or model.__tablename__
    columns, values = [], []
    for column in model.__table__.columns:
        if column.name in overrides:
            value = literal_column(overrides[column.name], type_=column.type)
        elif column.server_default is not None or column.nullable:
            continue
        elif isinstance(column.type, (UUID, Uuid)):
            if not column.primary_key:
                raise ValueError(f"Fixture requires owner for {model.__tablename__}.{column.name}")
            value = literal_column(identifier(namespace), type_=column.type)
        elif isinstance(column.type, DateTime):
            value = literal(datetime.now(UTC), type_=column.type)
        elif isinstance(column.type, Date):
            value = literal(datetime.now(UTC).date(), type_=column.type)
        elif column.default is not None and column.default.is_scalar:
            value = literal(column.default.arg, type_=column.type)
        elif isinstance(column.type, (JSONB, ARRAY)):
            default = (
                column.default.arg(None)
                if column.default is not None
                else {}
                if isinstance(column.type, JSONB)
                else []
            )
            value = literal(default, type_=column.type)
        elif isinstance(column.type, Boolean):
            value = literal(False)
        elif isinstance(column.type, Integer):
            value = literal(0)
        elif isinstance(column.type, String):
            value = literal("profile", type_=column.type)
        else:
            raise ValueError(f"Unhandled fixture column {model.__tablename__}.{column.name}")
        columns.append(column.name)
        values.append(value)
    started = time.perf_counter()
    inserted = 0
    maximum = math.ceil(count * 1.05) if where else count
    # Keep each generated statement bounded while leaving ordinary capture and
    # business triggers enabled. The outer fixture transaction stays atomic.
    for lower in range(1, maximum + 1, 5000):
        series = (
            func.generate_series(lower, min(lower + 4999, maximum))
            .table_valued("n")
            .render_derived()
        )
        statement = select(*values).select_from(series)
        if where:
            statement = statement.where(text(where))
        result = connection.execute(
            insert(model.__table__).from_select(
                columns, statement.limit(count - inserted), include_defaults=False
            ),
            execution_options={"preserve_rowcount": True},
        )
        inserted += result.rowcount
        if maximum > 5000:
            print(
                json.dumps(
                    {
                        "stage": "seed_progress",
                        "table": model.__tablename__,
                        "rows": inserted,
                        "target": count,
                    }
                ),
                file=sys.stderr,
                flush=True,
            )
        if inserted == count:
            break
    assert inserted == count, f"Insufficient generated rows for {model.__tablename__}"
    print(
        json.dumps(
            {
                "stage": "seed",
                "table": model.__tablename__,
                "rows": count,
                "seconds": round(time.perf_counter() - started, 3),
            }
        ),
        file=sys.stderr,
        flush=True,
    )


def seed_fixture(engine, counts, *, history_bytes=8192):
    now = datetime.now(UTC)
    now_sql = "CURRENT_TIMESTAMP"
    articles, sources, assets = counts["articles"], counts["sources"], counts["partner_assets"]
    article_number = f"1 + (n - 1) % {articles}"
    source_number = f"1 + (n - 1) % {sources}"
    body = (
        "array_to_string(ARRAY(SELECT (ARRAY['Python','Kubernetes','PostgreSQL',"
        "'Security','JavaScript','Tracing'])[1+s%6] || ' runtime evidence ' || "
        "md5(n::text || ':' || s::text) FROM generate_series(1,"
        + str(max(1, math.ceil(history_bytes / 66)))
        + ") s), ' ')"
    )
    source_id = identifier("sources", source_number)
    article_id = identifier("articles", article_number)
    started = time.perf_counter()
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout='0'"))
        names = ",".join('"' + table.name + '"' for table in models.Base.metadata.sorted_tables)
        connection.execute(text(f"TRUNCATE {names} CASCADE"))
        seed_rows(
            connection,
            models.Source,
            sources,
            {
                "name": "'Profile publisher ' || n",
                "source_type": "'publisher'",
                "feed_url": "CASE WHEN n % 11 = 0 THEN NULL "
                "ELSE 'https://publisher.example/'||n||'/feed' END",
                "website_url": "'https://publisher.example/'||n",
                "approval_status": "CASE WHEN n % 11 = 0 OR n % 3 = 0 THEN 'pending' "
                "WHEN n % 19 = 0 THEN 'rejected' ELSE 'approved' END",
                "enabled": "n % 7 <> 0",
                "created_at": now_sql + " - (n % 400) * interval '1 day'",
                "updated_at": now_sql + " - (n % 30) * interval '1 day'",
                "last_success_at": now_sql + " - interval '1 hour'",
            },
        )
        seed_rows(
            connection,
            models.Topic,
            counts["topics"],
            {
                "name": "'Python topic '||n",
                "slug": "'profile-topic-'||n",
                "kind": "'language'",
                "status": "CASE WHEN n % 10 = 0 THEN 'rejected' ELSE 'active' END",
                "aliases": "ARRAY['runtime-'||n]",
                "description": "'Python runtime tooling '||n",
            },
        )
        seed_rows(
            connection,
            models.Tag,
            counts["tags"],
            {
                "name": "'Async label '||n",
                "slug": "'profile-tag-'||n",
                "aliases": "ARRAY['async-'||n]",
            },
        )
        seed_rows(
            connection,
            models.Article,
            articles,
            {
                "canonical_url": "'https://publisher.example/articles/'||n",
                "url_hash": "md5('profile-url:'||n)",
                "title": "'Inspect Python coroutine state '||n",
                "summary": "'Python runtime source evidence for article '||n",
                "ai_summary": "'Inspect coroutine execution and locate blocked tasks.'",
                "ai_description": "'Debug blocked Python coroutine execution.'",
                "language": "'en'",
                "review_status": "CASE WHEN n % 17 = 0 AND n % 5 <> 0 THEN 'rejected' "
                "WHEN n % 5 = 0 THEN 'pending' ELSE 'approved' END",
                "publication_status": "CASE WHEN n % 5 = 0 OR n % 17 = 0 "
                "THEN 'unpublished' ELSE 'published' END",
                "classification_provenance": "jsonb_build_object("
                "'developer_relevance','relevant','page_kind','article')",
                "discovered_at": now_sql + " - (n % 120) * interval '1 day'",
                "published_at": now_sql + " - (n % 90) * interval '1 day'",
                "published_to_feed_at": "CASE WHEN n % 5 <> 0 AND n % 17 <> 0 THEN "
                "CURRENT_TIMESTAMP - (n % 30) * interval '1 day' ELSE NULL END",
                "image_url": "CASE WHEN n % 10 = 5 THEN "
                "'https://images.example/'||n||'.png' ELSE NULL END",
            },
        )
        seed_rows(
            connection,
            models.ArticleOrigin,
            counts["article_origins"],
            {
                "article_id": article_id,
                "source_id": source_id,
                "entry_key": "'profile-entry-'||n",
                "original_url": "'https://publisher.example/articles/'||(" + article_number + ")",
            },
        )
        seed_rows(
            connection,
            models.ArticleContent,
            min(counts["article_contents"], articles),
            {
                "article_id": identifier("articles"),
                "url": "'https://publisher.example/articles/'||n",
                "text": body,
                "content_hash": "encode(sha256(convert_to(n::text,'UTF8')),'hex')",
                "method": "'page'",
            },
        )
        seed_rows(
            connection,
            models.ArticleTopic,
            counts["article_topics"],
            {
                "article_id": article_id,
                "topic_id": identifier(
                    "topics", f"1 + ((n-1)%{articles} + ((n-1)/{articles})*31) % {counts['topics']}"
                ),
                "role": f"CASE WHEN n <= {articles} THEN 'primary' "
                "WHEN n%3=0 THEN 'incidental' ELSE 'supporting' END",
                "relevance": "0.8",
                "evidence": "'Python runtime evidence'",
            },
        )
        seed_rows(
            connection,
            models.ArticleTag,
            counts["article_tags"],
            {
                "article_id": article_id,
                "tag_id": identifier(
                    "tags", f"1 + ((n-1)%{articles} + ((n-1)/{articles})*17) % {counts['tags']}"
                ),
            },
        )
        seed_rows(
            connection,
            models.ArticleEnrichmentJob,
            counts["article_enrichment_jobs"],
            {
                "article_id": article_id,
                "status": "CASE WHEN ("
                + article_number
                + ") % 40 = 5 THEN 'running' WHEN ("
                + article_number
                + ") % 40 = 10 THEN 'queued' WHEN ("
                + article_number
                + ") % 40 = 15 THEN 'failed' ELSE 'succeeded' END",
                "attempts": "1",
                "available_at": now_sql + " + interval '30 minutes'",
                "created_at": now_sql + " - (1000000 - n) * interval '1 second'",
                "finished_at": now_sql,
                "outcome": "'enriched'",
                "result": "jsonb_build_object('input_url','https://publisher.example/articles/'||("
                + article_number
                + "),'image_url',NULL)",
            },
            where=f"n <= {articles} AND ({article_number}) % 40 <> 0 "
            f"OR n > {articles} AND ({article_number}) % 5 <> 0",
        )
        seed_rows(
            connection,
            models.ArticleAnalysisJob,
            counts["article_analysis_jobs"],
            {
                "article_id": article_id,
                "status": "CASE WHEN n <= "
                + str(articles)
                + " AND ("
                + article_number
                + ") % 40 = 20 THEN 'queued' WHEN ("
                + article_number
                + ") % 40 = 25 THEN 'failed' ELSE 'succeeded' END",
                "attempts": "1",
                "created_at": now_sql + " - (1000000 - n) * interval '1 second'",
                "finished_at": now_sql,
                "prompt_version": "'historical-profile-prompt'",
                "outcome": "'applied'",
                "input_snapshot": "jsonb_build_object('text'," + body + ")",
                "catalog_snapshot": "jsonb_build_object('topics','[]'::jsonb,"
                "'tags','[]'::jsonb,'historical_evidence'," + body + ")",
                "result": "jsonb_build_object('outcome','ready',"
                "'developer_relevance','relevant','page_kind','article')",
                "usage": "jsonb_build_object('input_tokens',"
                "1000 + n % 9000,'output_tokens',n%2000)",
            },
        )
        seed_rows(
            connection,
            models.ArticleImageJob,
            counts["article_image_jobs"],
            {
                "article_id": article_id,
                "operation": "'discover'",
                "status": "CASE WHEN n % 13 = 0 THEN 'failed' ELSE 'succeeded' END",
                "outcome": "'not_found'",
                "finished_at": now_sql,
                "storage": "jsonb_build_object('input_url','https://publisher.example/articles/'||("
                + article_number
                + "))",
            },
        )
        seed_rows(
            connection,
            models.ArticleReview,
            counts["article_reviews"],
            {
                "article_id": article_id,
                "action": "CASE WHEN ("
                + article_number
                + ") % 17 = 0 THEN 'reject' WHEN n % 2 = 0 THEN 'publish' ELSE 'approve' END",
                "revision": "1",
                "created_at": now_sql + " - (n % 30) * interval '1 day'",
                "automation": "CASE WHEN n % 3 = 0 THEN '{}'::jsonb "
                "ELSE jsonb_build_object('policy','profile','evidence'," + body + ") END",
            },
        )
        seed_rows(
            connection,
            models.IngestionJob,
            counts["ingestion_jobs"],
            {
                "source_id": source_id,
                "status": "CASE WHEN n > "
                + str(counts["ingestion_jobs"] - sources)
                + " AND n % 7 = 0 THEN 'queued' WHEN n % 19 = 0 THEN 'failed' ELSE 'succeeded' END",
                "finished_at": now_sql + " - (n % 90) * interval '1 day'",
                "created_at": now_sql + " - (1000000 - n) * interval '1 second'",
            },
        )
        seed_rows(
            connection,
            models.SourceEnrichmentJob,
            sources,
            {
                "source_id": identifier("sources"),
                "status": "CASE WHEN n % 13 = 0 THEN 'failed' ELSE 'succeeded' END",
                "operation": "'legacy'",
                "finished_at": now_sql,
            },
        )
        seed_rows(
            connection,
            models.SourceCandidate,
            counts["source_candidates"],
            {
                "name": "'Profile candidate '||n",
                "identity_url": "'https://candidate.example/'||n",
                "source_id": "CASE WHEN n % 2 = 0 THEN " + source_id + " ELSE NULL END",
                "status": "CASE WHEN n % 7 = 0 THEN 'rejected' ELSE 'pending' END",
            },
        )
        seed_rows(
            connection,
            models.SourceDiscoveryJob,
            counts["source_candidates"],
            {
                "candidate_id": identifier("source_candidates"),
                "status": "CASE WHEN n = 1 THEN 'running' "
                "WHEN n % 3 = 0 THEN 'failed' ELSE 'queued' END",
            },
        )
        seed_rows(
            connection,
            models.InferenceCall,
            counts["inference_calls"],
            {
                "started_at": now_sql
                + " - (n % 120) * interval '1 day' - (n % 86400) * interval '1 second'",
                "finished_at": now_sql,
                "operation": "(ARRAY['article_analysis','topic_discovery',"
                "'topic_verification','partner_qualification'])[1+n%4]",
                "model": "(ARRAY['model-a','model-b','model-c'])[1+n%3]",
                "reasoning_effort": "(ARRAY['low','medium','high'])[1+n%3]",
                "request_hash": "repeat('a',64)",
                "status": "(ARRAY['returned','failed','running'])[1+n%3]",
                "tokens": "jsonb_build_object('inputTokens',1000+n%9000,'outputTokens',n%2500)",
                "web_searches": "n % 4",
                "duration_ms": "1000+n%20000",
            },
        )
        seed_rows(
            connection, models.PartnerAccount, 1, {"name": "'Profile account'", "tier": "'gold'"}
        )
        seed_rows(
            connection,
            models.PartnerMembership,
            1,
            {
                "account_id": identifier("partner_accounts", "1"),
                "issuer": "'" + ISSUER + "'",
                "subject": "'profile-partner'",
            },
        )
        seed_rows(
            connection,
            models.PartnerProduct,
            assets,
            {
                "name": "'Profile product '||n",
                "product_url": "'https://product.example/'||n",
                "tagline": "'Inspect production task execution'",
                "status": "'approved'",
            },
        )
        seed_rows(
            connection,
            models.PartnerAsset,
            assets,
            {
                "account_id": identifier("partner_accounts", "1"),
                "product_id": identifier("partner_products"),
                "name": "'Profile creative '||n",
                "kind": "CASE WHEN n % 2 = 0 THEN 'product' ELSE 'ad' END",
                "status": "'active'",
            },
        )
        for model in (models.PartnerDailyMetric, models.PartnerTrackedDailyMetric):
            seed_rows(
                connection,
                model,
                counts["partner_tracked_daily_metrics"],
                {
                    "asset_id": identifier("partner_assets", f"1 + (n-1) % {assets}"),
                    "day": f"CURRENT_DATE - ((n-1)/{assets})::int",
                    "impressions": "1000+n%10000",
                    "clicks": "n%100",
                },
            )
        # Initial bootstrap coalesces fixture writes, retaining one durable rebuild.
        connection.execute(text("DELETE FROM read_model_events"))
        connection.execute(text("INSERT INTO read_model_events(kind) VALUES ('policy')"))
    seed_current_articles(sessionmaker(engine, expire_on_commit=False), articles)
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout='0'"))
        connection.execute(text("DELETE FROM read_model_events"))
        connection.execute(text("INSERT INTO read_model_events(kind) VALUES ('policy')"))
        connection.execute(text("ANALYZE"))
    return {
        "seconds": round(time.perf_counter() - started, 3),
        "history_bytes_per_analysis": history_bytes,
        "seeded_at": now.isoformat(),
    }


def seed_current_articles(factory, count):
    """Keep real, current ready/review/image examples alongside large historical runs."""
    from devfeed_core.analysis import (
        PROMPT_VERSION,
        analysis_candidates,
        catalog,
        reuse_candidates,
        snapshot_hash,
        source_snapshot,
    )

    identifiers = [
        fixture_id("articles", number) for number in range(35, min(count, 25600) + 1, 40)
    ]
    with factory.begin() as session, reuse_candidates():
        taxonomy = catalog(session)
        rows = list(
            session.scalars(select(models.Article).where(models.Article.id.in_(identifiers)))
        )
        for index, article in enumerate(rows):
            article.review_status = "approved" if index % 3 == 0 else "pending"
            article.image_url = None
            for origin in article.origins:
                origin.source_id = fixture_id("sources", 1)
            for link in article.topic_links:
                if link.role == "primary":
                    link.role = "supporting"
            primary = session.get(models.ArticleTopic, (article.id, fixture_id("topics", 1)))
            if primary is None:
                primary = models.ArticleTopic(
                    article_id=article.id,
                    topic_id=fixture_id("topics", 1),
                    role="primary",
                    relevance=1,
                    evidence="Python",
                    origin="manual",
                )
                session.add(primary)
            primary.role = "primary"
            snapshot = source_snapshot(article, session.get(models.ArticleContent, article.id))
            candidates = analysis_candidates(taxonomy, snapshot)
            job = session.scalar(
                select(models.ArticleAnalysisJob)
                .where(models.ArticleAnalysisJob.article_id == article.id)
                .order_by(
                    models.ArticleAnalysisJob.created_at.desc(), models.ArticleAnalysisJob.id.desc()
                )
                .limit(1)
            )
            job.status, job.outcome, job.prompt_version = "succeeded", "applied", PROMPT_VERSION
            job.input_snapshot, job.input_hash = snapshot, snapshot_hash(snapshot)
            job.catalog_snapshot, job.catalog_hash = candidates, snapshot_hash(candidates)
            job.result = {
                "outcome": "ready",
                "developer_relevance": "relevant",
                "page_kind": "article",
            }
            article.classification_provenance = {
                "developer_relevance": "relevant",
                "page_kind": "article",
                "analysis_id": str(job.id),
                "input_hash": job.input_hash,
            }
            if index % 3 == 2:
                image = session.scalar(
                    select(models.ArticleImageJob)
                    .where(models.ArticleImageJob.article_id == article.id)
                    .limit(1)
                )
                image.status = "running"


def dataset_counts(factory):
    with factory() as session:
        counts = {
            name: session.scalar(
                select(func.count()).select_from(models.Base.metadata.tables[name])
            )
            for name in COUNTS
        }
        counts["active_articles"] = session.scalar(
            select(func.count())
            .select_from(models.Article)
            .where(
                models.Article.publication_status == "unpublished",
                models.Article.review_status != "rejected",
            )
        )
        counts["projections"] = session.scalar(
            select(func.count()).select_from(models.PipelineProjection)
        )
        return counts


def dataset_resources(factory):
    with factory() as session:
        sizes = {
            name: int(session.scalar(select(func.pg_total_relation_size(name))))
            for name in (
                *COUNTS,
                "pipeline_projections",
                "read_model_events",
                "read_model_states",
                "report_snapshots",
                "report_refreshes",
            )
        }
        postgres = {
            "version": session.scalar(select(func.version())),
            "shared_buffers": session.scalar(select(func.current_setting("shared_buffers"))),
            "max_connections": session.scalar(select(func.current_setting("max_connections"))),
            "database_bytes": session.scalar(
                select(func.pg_database_size(func.current_database()))
            ),
        }
        samples = session.execute(
            text(
                "SELECT avg(octet_length(input_snapshot::text)), "
                "avg(octet_length(catalog_snapshot::text)) FROM "
                "(SELECT input_snapshot,catalog_snapshot "
                "FROM article_analysis_jobs LIMIT 256) sampled"
            )
        ).one()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "postgres": postgres,
        "relation_bytes_including_indexes": sizes,
        "sampled_analysis_input_bytes": round(float(samples[0] or 0), 1),
        "sampled_analysis_catalog_bytes": round(float(samples[1] or 0), 1),
        "process_peak_rss_kib": usage.ru_maxrss,
        "process_cpu_user_seconds": round(usage.ru_utime, 3),
        "process_cpu_system_seconds": round(usage.ru_stime, 3),
    }


def dataset_qualification(dataset, requested_counts):
    below_production = {
        name: {"actual": dataset.get(name, 0), "baseline": baseline}
        for name, baseline in PRODUCTION_BASELINE.items()
        if dataset.get(name, 0) < baseline
    }
    below_requested = {
        name: {"actual": dataset.get(name, 0), "requested": count}
        for name, count in requested_counts.items()
        if dataset.get(name, 0) < count
    }
    return {
        "label": "production-scale synthetic" if not below_production else "functional smoke",
        "meets_recorded_production_population": not below_production,
        "meets_requested_population": not below_requested,
        "below_production": below_production,
        "below_requested": below_requested,
    }


def apps(factory):
    from devfeed_admin_api import auth as admin_auth
    from devfeed_admin_api import config as admin_config
    from devfeed_admin_api import oidc as admin_oidc
    from devfeed_admin_api.dependencies import get_session as admin_session
    from devfeed_admin_api.main import create_app as admin_app
    from devfeed_admin_api.reporting import reporting_sessions
    from devfeed_partner_api import auth as partner_auth
    from devfeed_partner_api import config as partner_config
    from devfeed_partner_api import oidc as partner_oidc
    from devfeed_partner_api.dependencies import get_session as partner_session
    from devfeed_partner_api.main import create_app as partner_app

    def sessions():
        with factory() as session:
            yield session

    admin, partner = admin_app(), partner_app()
    for app, auth, configuration, oidc, subject, roles in (
        (admin, admin_auth, admin_config, admin_oidc, "profile-admin", ["superuser"]),
        (partner, partner_auth, partner_config, partner_oidc, "profile-partner", ["partner"]),
    ):
        settings = configuration.get_settings()
        token = secrets.token_urlsafe(32)
        record = {
            "subject": subject,
            "issuer": ISSUER,
            "organization_id": "profile",
            "roles": roles,
            "expires_at": int(time.time()) + 3600,
            "csrf_token": secrets.token_urlsafe(32),
            "policy": oidc.policy_key(settings),
            "identity_registration_version": 1,
            "provider_session": {
                "issuer": ISSUER,
                "client_id": settings.oidc_client_id,
                "subject": subject,
                "issued_at": int(time.time()),
            },
        }
        session_key = auth.key("session", token)
        auth.get_redis().set(session_key, json.dumps(record), ex=3600)
        app.state.profile_cookies = {oidc.cookie_name(settings, "session"): token}
        app.state.profile_session = (auth.get_redis(), session_key)
    admin.dependency_overrides[admin_session] = sessions
    admin.dependency_overrides[reporting_sessions] = lambda: factory
    partner.dependency_overrides[partner_session] = sessions
    return admin, partner


def workload():
    return [
        ("article-grouped", "admin", "/v1/admin/article-pipeline?limit=20&group_by=source"),
        ("article-cards", "admin", "/v1/admin/article-pipeline?limit=20"),
        ("article-column", "admin", "/v1/admin/article-pipeline/enrichment?limit=20"),
        ("article-search", "admin", "/v1/admin/article-pipeline?limit=20&q=Python"),
        ("article-search-empty", "admin", "/v1/admin/article-pipeline?limit=20&q=Kubernetes"),
        (
            "article-source",
            "admin",
            "/v1/admin/article-pipeline?limit=20&source_id=" + str(fixture_id("sources", 2)),
        ),
        ("article-attention", "admin", "/v1/admin/article-pipeline?limit=20&needs_attention=true"),
        (
            "article-deep-column",
            "admin",
            "/v1/admin/article-pipeline/enrichment?limit=20&cursor=1000",
        ),
        ("source-board", "admin", "/v1/admin/source-pipeline?limit=20"),
        ("source-search", "admin", "/v1/admin/source-pipeline?limit=20&q=publisher%201"),
        ("source-type", "admin", "/v1/admin/source-pipeline?limit=20&source_type=publisher"),
        ("source-attention", "admin", "/v1/admin/source-pipeline?limit=20&needs_attention=true"),
        ("overview-main", "admin", "/v1/admin/overview?days=30"),
        *[
            ("overview-" + group, "admin", "/v1/admin/overview/panels/" + group + "?days=30")
            for group in (
                "attention",
                "workload",
                "blockers",
                "publications",
                "adoption",
                "publication-time",
                "feed-health",
                "topic-coverage",
                "sources",
                "popular-articles",
                "publication-automation",
                "job-tokens",
                "throughput",
                "decision-efficiency",
                "inference-tokens",
            )
        ],
        (
            "partner-dashboard",
            "partner",
            f"/v1/partner/accounts/{fixture_id('partner_accounts', 1)}/dashboard?limit=25&days=30",
        ),
    ]


def percentile(values, fraction):
    return round(sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)], 3)


def query_ceiling(route):
    if route.startswith("source-"):
        return 16
    if route in {"article-search", "article-search-empty", "article-source", "article-attention"}:
        return 15
    if route == "article-deep-column":
        return 8
    return {
        "article-grouped": 8,
        "article-cards": 15,
        "article-column": 8,
        "source-board": 16,
        "partner-dashboard": 8,
    }.get(route, 6)


def profile_gates(phases, *, p95_budget_ms):
    if not math.isfinite(p95_budget_ms) or p95_budget_ms <= 0:
        raise ValueError("p95 budget must be finite and greater than zero")
    failures = []
    for phase, routes in phases.items():
        for route, result in routes.items():
            reasons = []
            if {int(code) for code in result["status_counts"]} != {200}:
                reasons.append("HTTP errors")
            if result["p95_ms"] > p95_budget_ms:
                reasons.append("p95 latency")
            if result["queries_max"] > query_ceiling(route):
                reasons.append("SQL query count")
            if reasons:
                failures.append({"phase": phase, "route": route, "reasons": reasons})
    return {
        "passed": not failures,
        "p95_budget_ms": p95_budget_ms,
        "sql_query_ceilings": {route: query_ceiling(route) for route, _, _ in workload()},
        "failures": failures,
    }


@reuse_snapshots()
@reuse_candidates()
def background_worker(kind, stop, ready, results):
    """Independent processes/engines model publishers competing with HTTP readers."""
    from devfeed_admin_api.overview_projection import refresh_overview_projections
    from devfeed_admin_api.pipeline_projection import project_batch
    from devfeed_core.config import get_settings
    from devfeed_core.db import create_database_engine

    get_settings.cache_clear()
    engine = create_database_engine(get_settings())
    factory = sessionmaker(engine, expire_on_commit=False)
    counts = Counter()
    token = str(uuid.uuid4())
    try:
        ready.set()
        while not stop.is_set():
            if kind == "pipeline":
                counts["acknowledged_events"] += project_batch(factory, batch=40, token=token)
            elif kind == "reports":
                # Exercise the production demand scheduler, including its warm set.
                counts["published"] += refresh_overview_projections(factory, limit=2)
            else:
                # Ten edits per transaction, at most 400: ordinary triggers capture
                # actual entity changes while readers and both publishers run.
                if counts["transactions"] >= 40:
                    stop.wait(0.1)
                    continue
                with factory.begin() as session:
                    changed = session.execute(
                        text(
                            "UPDATE articles SET title = 'Concurrent profile update ' "
                            "|| :round || ':' || id::text WHERE id IN "
                            "(SELECT id FROM articles WHERE publication_status = 'unpublished' "
                            "AND review_status != 'rejected' ORDER BY id LIMIT 10)"
                        ),
                        {"round": counts["transactions"]},
                    )
                    counts["updated_rows"] += changed.rowcount
                counts["transactions"] += 1
            counts["iterations"] += 1
            stop.wait(0.25 if kind == "updates" else 0.1)
    except Exception as error:
        counts["errors"] += 1
        results.put({"kind": kind, "error": str(error), **counts})
        raise
    else:
        results.put(
            {
                "kind": kind,
                **counts,
                "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            }
        )
    finally:
        engine.dispose()


def competing_publishers(factory, engine, *, repeats, concurrency):
    context = multiprocessing.get_context("spawn")
    stop, results = context.Event(), context.Queue()
    processes = []
    try:
        for kind in ("pipeline", "reports", "updates"):
            ready = context.Event()
            process = context.Process(target=background_worker, args=(kind, stop, ready, results))
            process.start()
            processes.append(process)
            if not ready.wait(15):
                raise RuntimeError(f"Background {kind} process did not start")
        routes = asyncio.run(sample_apis(factory, engine, repeats=repeats, concurrency=concurrency))
    finally:
        stop.set()
        for process in processes:
            process.join(45)
            if process.is_alive():
                process.terminate()
                process.join(5)
    workers = [results.get(timeout=2) for _ in processes]
    assert all(process.exitcode == 0 for process in processes), workers
    assert next(item for item in workers if item["kind"] == "updates")["updated_rows"] > 0
    assert next(item for item in workers if item["kind"] == "pipeline")["acknowledged_events"] > 0
    assert next(item for item in workers if item["kind"] == "reports")["published"] > 0
    return {"routes": routes, "workers": workers}


async def sample_apis(factory, engine, *, repeats=5, concurrency=8):
    application = dict(zip(("admin", "partner"), apps(factory), strict=True))
    counters = Counter()
    guard = threading.Lock()

    def count_query(*_):
        if tag := REQUEST_TAG.get():
            with guard:
                counters[tag] += 1

    event.listen(engine, "before_cursor_execute", count_query)
    clients = {
        name: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://profile.test",
            cookies=app.state.profile_cookies,
        )
        for name, app in application.items()
    }
    semaphore = asyncio.Semaphore(concurrency)
    records = []

    async def request(number, target):
        label, service, url = target
        async with semaphore:
            tag = f"{label}:{number}"
            token = REQUEST_TAG.set(tag)
            started = time.perf_counter()
            try:
                response = await clients[service].get(url)
                records.append(
                    {
                        "route": label,
                        "ms": (time.perf_counter() - started) * 1000,
                        "status": response.status_code,
                        "queries": counters[tag],
                        "bytes": len(response.content),
                    }
                )
            finally:
                REQUEST_TAG.reset(token)

    try:
        await asyncio.gather(
            *(request(number, target) for number in range(repeats) for target in workload())
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_query)
        for client in clients.values():
            await client.aclose()
        for app in application.values():
            redis, session_key = app.state.profile_session
            redis.delete(session_key)
    summary = {}
    for label in sorted({row["route"] for row in records}):
        rows = [row for row in records if row["route"] == label]
        values = [row["ms"] for row in rows]
        summary[label] = {
            "samples": len(rows),
            "p50_ms": percentile(values, 0.5),
            "p95_ms": percentile(values, 0.95),
            "p99_ms": percentile(values, 0.99),
            "status_counts": dict(Counter(row["status"] for row in rows)),
            "queries_min": min(row["queries"] for row in rows),
            "queries_max": max(row["queries"] for row in rows),
            "response_bytes_max": max(row["bytes"] for row in rows),
        }
    return summary


def parity(factory):
    from devfeed_admin_api import article_pipeline, source_pipeline
    from devfeed_core.read_models import pipeline_policy_version

    policy = pipeline_policy_version()
    checked = Counter()
    with factory() as session:
        stages = session.execute(
            select(models.PipelineProjection.kind, models.PipelineProjection.stage)
            .where(models.PipelineProjection.policy_version == policy)
            .distinct()
        ).all()
        selected = []
        for kind, stage in stages:
            selected.extend(
                session.scalars(
                    select(models.PipelineProjection)
                    .where(
                        models.PipelineProjection.policy_version == policy,
                        models.PipelineProjection.kind == kind,
                        models.PipelineProjection.stage == stage,
                    )
                    .order_by(models.PipelineProjection.entity_id)
                    .limit(2)
                )
            )
        expected = {(row.kind, row.entity_id): row.payload for row in selected}
    ids = [identifier for (kind, identifier) in expected if kind == "article"]
    statement = article_pipeline._statement
    # Limit the legacy reference's input population, preserving its independent
    # fact loading, classification, retry derivation, and card construction.
    with (
        factory() as session,
        patch.object(
            article_pipeline,
            "_statement",
            lambda *args, **kwargs: statement(*args, **kwargs).where(models.Article.id.in_(ids)),
        ),
    ):
        articles = article_pipeline._board(
            session, q=None, source_id=None, needs_attention=False, limit=50
        )
    with factory() as session:
        sources = source_pipeline._columns(
            session,
            source_pipeline.SourcePipelineQuery(q="", limit=5000),
            datetime.now(UTC),
        )
    actual = [
        ("article", card.id, card) for column in articles.columns for card in column.items
    ] + [(card.kind, card.id, card) for column in sources for card in column.items]
    for kind, identifier, card in actual:
        if (kind, identifier) in expected:
            previous = expected[(kind, identifier)]
            payload = card.model_dump(mode="json")
            for field in (
                "stage",
                "state",
                "blockers",
                "allowed_actions",
                "retry_targets",
                "source_id",
            ):
                assert payload.get(field) == previous.get(field), (
                    f"Read-model oracle mismatch: {kind}/{field}"
                )
            checked[kind + ":" + card.stage] += 1
    assert sum(checked.values()) == len(expected), "Legacy oracle did not cover saved samples"
    return {"checked": dict(checked), "matches_legacy_lifecycle": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--history-bytes", type=int, default=8192)
    parser.add_argument("--repeats", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--second-concurrency", type=int, default=16)
    parser.add_argument("--p95-budget-ms", type=float, default=1000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        url = validate_targets(
            os.environ["DEVFEED_TEST_DATABASE_URL"], os.environ["DEVFEED_TEST_REDIS_URL"]
        )
        counts = fixture_counts(args.scale)
        if (
            not 1 <= args.repeats <= 100
            or not 1 <= args.concurrency <= 40
            or not 1 <= args.second_concurrency <= 40
            or not 128 <= args.history_bytes <= 65536
            or args.p95_budget_ms <= 0
            or not math.isfinite(args.p95_budget_ms)
        ):
            raise ValueError(
                "Supported bounds: repeats1..100, concurrency1..40, history-bytes128..65536, "
                "p95-budget-ms greater than zero"
            )
    except (ValueError, KeyError) as error:
        parser.error(str(error))
    os.environ.update(
        DEVFEED_DATABASE_URL=url.render_as_string(hide_password=False),
        DEVFEED_READ_MODEL_DATABASE_URL=url.render_as_string(hide_password=False),
        DEVFEED_REDIS_URL=os.environ["DEVFEED_TEST_REDIS_URL"],
        DEVFEED_CACHE_ENABLED="false",
        DEVFEED_AI_CONTENT_NOT_BEFORE="",
        DEVFEED_IMAGE_STORAGE_ENABLED="false",
        DEVFEED_METRICS_ENABLED="false",
        DEVFEED_OTLP_ENDPOINT="",
    )
    os.environ.update(AUTH_ENV)
    os.environ.update(ISOLATED_ENV)
    from devfeed_admin_api.overview_projection import refresh_overview_projections
    from devfeed_admin_api.pipeline_projection import drain_pipeline
    from devfeed_core.config import get_settings
    from devfeed_core.db import create_database_engine

    get_settings.cache_clear()
    from devfeed_admin_api.config import get_settings as admin_settings
    from devfeed_partner_api.config import get_settings as partner_settings

    admin_settings.cache_clear()
    partner_settings.cache_clear()
    engine = create_database_engine(get_settings())
    factory = sessionmaker(engine, expire_on_commit=False)
    output = {
        "complete": False,
        "started_at": datetime.now(UTC).isoformat(),
        "scope": "Synthetic local PostgreSQL and in-process authenticated ASGI APIs; "
        "excludes production infrastructure, proxy, network, and browser",
        "authentication": "Real local Redis sessions, expiry/policy/provider-logout "
        "and role checks, "
        "live partner memberships; synthetic identities, OIDC login/token exchange excluded",
        "hardware": {
            "python": platform.python_version(),
            "system": platform.system(),
            "architecture": platform.machine(),
            "cpu_count": os.cpu_count(),
            "memory_total_bytes": os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"),
        },
        "production_baseline_2026_10_21": PRODUCTION_BASELINE,
        "workload": {
            "repeats_per_route": args.repeats,
            "concurrency": args.concurrency,
            "second_concurrency": args.second_concurrency,
            "fixture_scale_requested": args.scale,
            "latency_scope": "Admitted fixed-concurrency request time; includes ASGI/threadpool/"
            "database waits, excludes load-generator semaphore queue",
            "database_cache": "PostgreSQL buffers warmed by seeding/backfill/oracle; Redis "
            "application response cache disabled",
            "normal_workers": "Paused after publishing; competition phase runs separate "
            "pipeline/report/update processes with independent database engines",
            "tail_samples_qualified": args.repeats >= 30,
        },
    }

    def checkpoint(phase):
        output["last_completed_phase"] = phase
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.output.with_suffix(args.output.suffix + ".tmp")
            temporary.write_text(json.dumps(output, indent=2) + "\n")
            temporary.replace(args.output)
        print(json.dumps({"stage": phase}), file=sys.stderr, flush=True)

    checkpoint("starting")
    try:
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        if args.seed:
            output["seed"] = seed_fixture(engine, counts, history_bytes=args.history_bytes)
        started = time.perf_counter()
        output["projector"] = {
            "acknowledged_events": drain_pipeline(factory),
            "seconds": round(time.perf_counter() - started, 3),
        }
        checkpoint("projector")
        output["dataset"] = dataset_counts(factory)
        output["dataset_qualification"] = dataset_qualification(output["dataset"], counts)
        output["parity"] = parity(factory)
        checkpoint("dataset_and_oracle")
        output["first_requests_before_report_publish"] = asyncio.run(
            sample_apis(factory, engine, repeats=1, concurrency=args.concurrency)
        )
        started = time.perf_counter()
        output["overview_worker"] = {
            "published": refresh_overview_projections(factory, limit=30, seed=False),
            "seconds": round(time.perf_counter() - started, 3),
        }
        checkpoint("reports")
        output["concurrent_cache_disabled"] = asyncio.run(
            sample_apis(factory, engine, repeats=args.repeats, concurrency=args.concurrency)
        )
        checkpoint("concurrency_" + str(args.concurrency))
        output["concurrent_cache_disabled_second"] = asyncio.run(
            sample_apis(factory, engine, repeats=args.repeats, concurrency=args.second_concurrency)
        )
        checkpoint("concurrency_" + str(args.second_concurrency))
        output["competing_publishers"] = competing_publishers(
            factory, engine, repeats=args.repeats, concurrency=args.second_concurrency
        )
        checkpoint("competing_publishers")
        with factory.begin() as session:
            row = session.scalar(
                select(models.Article)
                .where(
                    models.Article.publication_status == "unpublished",
                    models.Article.review_status != "rejected",
                )
                .limit(1)
            )
            for _ in range(100):
                session.add(models.ReadModelEvent(kind="article", entity_id=row.id))
        with factory() as session:
            before = session.scalar(select(func.count()).select_from(models.ReadModelEvent))
        output["worker_paused"] = asyncio.run(
            sample_apis(factory, engine, repeats=1, concurrency=args.concurrency)
        )
        with factory() as session:
            after = session.scalar(select(func.count()).select_from(models.ReadModelEvent))
        assert before == after, "HTTP reads performed projection work"
        started = time.perf_counter()
        output["duplicate_backlog"] = {
            "before": before,
            "after_gets": after,
            "acknowledged": drain_pipeline(factory),
            "seconds": round(time.perf_counter() - started, 3),
        }
        output["parity_after_backlog"] = parity(factory)
        output["resources"] = dataset_resources(factory)
        output["errors"] = {
            strategy + ":" + label: row["status_counts"]
            for strategy in ("concurrent_cache_disabled", "concurrent_cache_disabled_second")
            for label, row in output[strategy].items()
            if set(row["status_counts"]) != {200}
        }
        output["gates"] = profile_gates(
            {
                "concurrent_cache_disabled": output["concurrent_cache_disabled"],
                "concurrent_cache_disabled_second": output["concurrent_cache_disabled_second"],
                "competing_publishers": output["competing_publishers"]["routes"],
            },
            p95_budget_ms=args.p95_budget_ms,
        )
        if not output["dataset_qualification"]["meets_requested_population"]:
            output["gates"]["passed"] = False
            output["gates"]["failures"].append({"reason": "Dataset below requested scale"})
        output["complete"] = True
        checkpoint("complete")
        encoded = json.dumps(output, indent=2)
        print(encoded)
        return int(not output["gates"]["passed"])
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
