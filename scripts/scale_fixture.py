"""Seed deterministic business history and publish native ClickHouse test data.

DEVFEED_TEST_DATABASE_URL must select local PostgreSQL ending in _test and
DEVFEED_TEST_REDIS_URL must select local Redis database 15. ClickHouse also
requires explicitly local DEVFEED_TEST_CLICKHOUSE_* credentials and a _test
identifier. --seed replaces those disposable databases. No production records
or credentials are copied. Stable identities retain earlier benchmark parity.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import resource
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from alembic import command
from alembic.config import Config
from devfeed_core import models
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Integer,
    String,
    Uuid,
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
ISSUER = "https://profile.invalid"


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


def analytics_environment(environment=None):
    """Use only explicitly owned local ClickHouse test targets for destructive profiling."""
    environment = os.environ if environment is None else environment
    origin = environment["DEVFEED_TEST_CLICKHOUSE_URL"]
    database = environment["DEVFEED_TEST_CLICKHOUSE_DATABASE"]
    url = urlsplit(origin)
    if (
        url.scheme not in {"http", "https"}
        or url.hostname not in {"localhost", "127.0.0.1", "::1"}
        or url.username is not None
        or url.password is not None
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
        or any(ord(character) < 33 or ord(character) == 127 for character in origin)
    ):
        raise ValueError("Profiling requires an explicitly local ClickHouse service origin")
    _ = url.port
    if (
        not database.endswith("_test")
        or not database.replace("_", "a").isalnum()
        or not (database[0].isalpha() or database[0] == "_")
        or len(database) > 63
        or not database.isascii()
    ):
        raise ValueError("Profiling ClickHouse database must be an identifier ending in _test")
    return {
        "DEVFEED_CLICKHOUSE_URL": origin.rstrip("/"),
        "DEVFEED_CLICKHOUSE_DATABASE": database,
        "DEVFEED_CLICKHOUSE_USERNAME": environment.get(
            "DEVFEED_TEST_CLICKHOUSE_USERNAME", "devfeed"
        ),
        "DEVFEED_CLICKHOUSE_PASSWORD": environment.get("DEVFEED_TEST_CLICKHOUSE_PASSWORD", ""),
    }


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
        value: Any
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
    # Historical job populations can outnumber articles, and their fixture
    # predicate excludes one in five older jobs. Five percent headroom then
    # cannot supply the requested population. Keep generation bounded while
    # allowing enough candidates for those filtered histories.
    maximum = count * 2 if where else count
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
    seed_current_articles(sessionmaker(engine, expire_on_commit=False), articles)
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout='0'"))
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
            if name in models.Base.metadata.tables
        }
        counts["active_articles"] = session.scalar(
            select(func.count())
            .select_from(models.Article)
            .where(
                models.Article.publication_status == "unpublished",
                models.Article.review_status != "rejected",
            )
        )
        return counts


def dataset_resources(factory):
    with factory() as session:
        sizes = {
            name: int(session.scalar(select(func.pg_total_relation_size(name))))
            for name in COUNTS
            if name in models.Base.metadata.tables
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


async def seed_analytics(client, counts):
    """Synthetic accounting goes straight to ClickHouse, never via PostgreSQL."""
    from datetime import timedelta

    from devfeed_core.analytics_direct_engagement import DirectEngagementAnalytics
    from devfeed_core.analytics_inference_store import InferenceStore

    now = datetime.now(UTC)
    store = InferenceStore(client)
    for offset in range(0, counts["inference_calls"], 1000):
        await store.write(
            [
                dict(
                    id=fixture_id("inference_calls", number),
                    started_at=now - timedelta(days=number % 120, seconds=number % 86400),
                    status=("returned", "failed", "running")[number % 3],
                    operation=(
                        "article_analysis",
                        "topic_discovery",
                        "topic_verification",
                        "partner_qualification",
                    )[number % 4],
                    model=("model-a", "model-b", "model-c")[number % 3],
                    reasoning_effort=("low", "medium", "high")[number % 3],
                    tokens=dict(inputTokens=1000 + number % 9000, outputTokens=number % 2500),
                    web_searches=number % 4,
                    duration_ms=1000 + number % 20000,
                )
                for number in range(offset + 1, min(offset + 1000, counts["inference_calls"]) + 1)
            ]
        )
    engagement = DirectEngagementAnalytics(client)
    for baseline in (False, True):
        for offset in range(0, counts["partner_tracked_daily_metrics"], 1000):
            await engagement.partner_totals(
                [
                    dict(
                        asset_id=fixture_id(
                            "partner_assets", 1 + (number - 1) % counts["partner_assets"]
                        ),
                        day=(now - timedelta(days=(number - 1) // counts["partner_assets"])).date(),
                        impressions=1000 + number % 10000,
                        clicks=number % 100,
                        revision=1,
                        updated_at=now,
                    )
                    for number in range(
                        offset + 1, min(offset + 1000, counts["partner_tracked_daily_metrics"]) + 1
                    )
                ],
                baseline=baseline,
            )


def publish_analytics(factory, *, reset=False, counts=None):
    """Import business snapshots and seed only explicitly disposable ClickHouse."""
    from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable
    from devfeed_core.analytics_worker import AnalyticsImportWorker
    from devfeed_core.config import get_settings
    from pydantic import SecretStr

    selected = analytics_environment()
    settings = get_settings().model_copy(
        update={
            "clickhouse_url": selected["DEVFEED_CLICKHOUSE_URL"],
            "clickhouse_database": selected["DEVFEED_CLICKHOUSE_DATABASE"],
            "clickhouse_username": selected["DEVFEED_CLICKHOUSE_USERNAME"],
            "clickhouse_password": SecretStr(selected["DEVFEED_CLICKHOUSE_PASSWORD"]),
        }
    )

    async def publish():
        async with AnalyticsClient(settings) as client:
            await client.initialize()
            if reset:
                tables = await client.query(
                    "SELECT name FROM system.tables WHERE database=currentDatabase() "
                    "AND engine NOT IN ('View','MaterializedView')"
                )
                for row in tables:
                    name = row["name"].replace("`", "``")
                    await client.execute(f"TRUNCATE TABLE `{name}`")
            worker = AnalyticsImportWorker(client, settings)
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                await worker.once()
                try:
                    state = await client.status()
                except AnalyticsUnavailable:
                    continue
                if state["ready"]:
                    if counts is not None:
                        await seed_analytics(client, counts)
                    inference, partner = await asyncio.gather(
                        client.query(
                            "SELECT count() AS count FROM analytics_inference_events FINAL"
                        ),
                        client.query(
                            "SELECT countIf(kind='partner_baseline') AS count "
                            "FROM analytics_partner_events FINAL"
                        ),
                    )
                    return dict(
                        history=state,
                        current_cards_ready=True,
                        counts=dict(
                            inference_calls=inference[0]["count"],
                            partner_tracked_daily_metrics=partner[0]["count"],
                        ),
                    )
            raise RuntimeError("Disposable direct analytics import exceeded 15 minutes")

    return asyncio.run(publish())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--history-bytes", type=int, default=8192)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        url = validate_targets(
            os.environ["DEVFEED_TEST_DATABASE_URL"], os.environ["DEVFEED_TEST_REDIS_URL"]
        )
        counts = fixture_counts(args.scale)
        if not 128 <= args.history_bytes <= 65536:
            raise ValueError("history-bytes must be in 128..65536")
        analytical = analytics_environment()
    except (ValueError, KeyError) as error:
        parser.error(str(error))
    os.environ.update(
        DEVFEED_DATABASE_URL=url.render_as_string(hide_password=False),
        DEVFEED_REDIS_URL=os.environ["DEVFEED_TEST_REDIS_URL"],
        DEVFEED_CACHE_ENABLED="false",
        DEVFEED_AI_CONTENT_NOT_BEFORE="",
        DEVFEED_IMAGE_STORAGE_ENABLED="false",
        DEVFEED_METRICS_ENABLED="false",
        DEVFEED_OTLP_ENDPOINT="",
    )
    os.environ.update(ISOLATED_ENV)
    os.environ.update(analytical)
    from devfeed_core.config import get_settings
    from devfeed_core.db import create_database_engine

    get_settings.cache_clear()
    engine = create_database_engine(
        get_settings().model_copy(update={"database_pool_enabled": False})
    )
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        result = {"complete": False, "requested_scale": args.scale}
        if args.seed:
            result["seed"] = seed_fixture(engine, counts, history_bytes=args.history_bytes)
        result["analytics"] = publish_analytics(
            factory, reset=args.seed, counts=counts if args.seed else None
        )
        result["dataset"] = dataset_counts(factory) | result["analytics"]["counts"]
        result["qualification"] = dataset_qualification(result["dataset"], counts)
        result["resources"] = dataset_resources(factory)
        result["complete"] = True
        encoded = json.dumps(result, indent=2, default=str) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded)
        print(encoded, end="")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
