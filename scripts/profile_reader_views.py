"""Measure saved reader views against a deterministic, disposable PostgreSQL fixture.

Never uses the checkout's .env. --seed requires an empty local *_test database;
there is deliberately no reset/truncate option. Run pytest against another database.
Use --app-root to measure another checkout with this same harness and fixture.
"""

import argparse
import asyncio
import contextvars
import hashlib
import importlib
import importlib.util
import json
import math
import os
import platform
import resource
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_SNAPSHOT_AT = "2026-10-22T09:34:41+00:00"
ORIGIN = "https://reader-profile.invalid"
ISSUER = "https://identity.reader-profile.invalid"
PROFILE_ORGANIZATION = "reader-profile"
RECOMMENDED_FEED_PATH = "/v1/user/feed?limit=24"
COUNTS = {
    "articles": 500000,
    "sources": 7500,
    "topics": 25000,
    "tags": 250000,
    "article_origins": 550000,
    "article_topics": 2000000,
    "article_tags": 5000000,
    "user_accounts": 1000,
    "article_likes": 250000,
}
PRODUCTION = {
    "articles": 90217,
    "published_approved_articles": 65869,
    "sources": 1403,
    "topics": 4450,
    "tags": 47796,
    "article_origins": 91868,
    "article_topics": 344971,
    "article_tags": 849616,
    "user_accounts": 33,
    "user_topics": 480,
    "user_sources": 42,
    "user_tags": 0,
    "article_likes": 53,
}
SCENARIOS = {"empty": 1, "ordinary": 2, "prolific": 3, "restricted": 4, "sparse": 5}
FEED_VIEW_LABEL = "feed-view"
REQUEST: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "reader_profile_request", default=None
)


def fixture_counts(scale):
    if not math.isfinite(scale) or not 0 < scale <= 2:
        raise ValueError("Scale must be finite, greater than zero and at most two")
    counts = {name: max(1, math.ceil(total * scale)) for name, total in COUNTS.items()}
    for name, minimum in {
        "articles": 3000,
        "sources": 250,
        "topics": 250,
        "tags": 250,
        "user_accounts": 25,
    }.items():
        counts[name] = max(minimum, counts[name])
    counts["article_origins"] = max(counts["articles"], counts["article_origins"])
    counts["article_topics"] = max(counts["articles"], counts["article_topics"])
    counts["article_tags"] = max(counts["articles"], counts["article_tags"])
    return counts


def qualification(actual):
    below = {
        name: {"actual": actual.get(name, 0), "production": count}
        for name, count in PRODUCTION.items()
        if actual.get(name, 0) <= count
    }
    return {
        "above_production": not below,
        "below_or_equal": below,
        "label": "above-production synthetic" if not below else "functional smoke",
    }


def source_identity(root):
    paths = ("apps/api/src", "apps/user-api/src", "packages/core/src", "packages/http/src")
    diff = subprocess.check_output(["git", "diff", "--binary", "HEAD", "--", *paths], cwd=root)
    files = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *paths],
        cwd=root,
    )
    digest = hashlib.sha256()
    for relative in sorted(set(files.decode().split("\0")) - {""}):
        path = root / relative
        if path.is_file():
            digest.update(relative.encode() + b"\0" + path.read_bytes() + b"\0")
    return {
        "paths": paths,
        "source_sha256": digest.hexdigest(),
        "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "status": subprocess.check_output(
            ["git", "status", "--short", "--", *paths], cwd=root, text=True
        ),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "seed_helper_sha256": hashlib.sha256(
            (ROOT / "scripts/profile_read_models.py").read_bytes()
        ).hexdigest(),
    }


@contextmanager
def revision_imports(root):
    """Editable installs use aliases, so src directories alone cannot select a checkout."""
    with tempfile.TemporaryDirectory(prefix="devfeed-reader-imports-") as directory:
        aliases = {}
        for parent in ("apps", "packages"):
            for project in sorted((root / parent).glob("*/pyproject.toml")):
                metadata = tomllib.loads(project.read_text())
                mappings = metadata.get("tool", {}).get("setuptools", {}).get("package-dir", {})
                for package, source in mappings.items():
                    if package.startswith("devfeed_"):
                        destination = project.parent / source
                        (Path(directory) / package).symlink_to(
                            destination, target_is_directory=True
                        )
                        aliases[package] = destination.resolve()
        if (
            not {"devfeed_core", "devfeed_user_api", "devfeed_api", "devfeed_http"}
            <= aliases.keys()
        ):
            raise ValueError("App root does not contain the required DevFeed package mappings")
        if any(name in sys.modules for name in aliases):
            raise ValueError("Revision selection must run in a fresh Python process")
        sys.path.insert(0, directory)
        try:
            provenance = {}
            for name in aliases:
                module = importlib.import_module(name)
                assert module.__file__ is not None
                path = Path(module.__file__).resolve()
                if not path.is_relative_to(aliases[name]):
                    raise ValueError(f"Imported {name} from the wrong revision")
                provenance[name] = str(path)
            yield provenance
        finally:
            sys.path.remove(directory)


def helper_module():
    spec = importlib.util.spec_from_file_location(
        "reader_profile_helpers", ROOT / "scripts/profile_read_models.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def isolated_environment(database, redis):
    for name in list(os.environ):
        if name.startswith("DEVFEED_"):
            del os.environ[name]
    os.environ.update(
        DEVFEED_DATABASE_URL=database,
        DEVFEED_REDIS_URL=redis,
        DEVFEED_CACHE_ENABLED="false",
        DEVFEED_AI_ENABLED="false",
        DEVFEED_FULL_AUTOMATION="false",
        DEVFEED_METRICS_ENABLED="false",
        DEVFEED_OTLP_ENDPOINT="",
        DEVFEED_IMAGE_STORAGE_ENABLED="false",
        DEVFEED_DATABASE_POOL_ENABLED="true",
        DEVFEED_DATABASE_POOL_SIZE="16",
        DEVFEED_DATABASE_MAX_OVERFLOW="0",
        DEVFEED_API_MAX_CONCURRENT_REQUESTS="16",
        DEVFEED_LOG_LEVEL="WARNING",
        DEVFEED_USER_BASE_URL=ORIGIN,
        DEVFEED_USER_OIDC_ISSUER_URL=ISSUER,
        DEVFEED_USER_OIDC_CLIENT_ID=PROFILE_ORGANIZATION,
        DEVFEED_USER_OIDC_ORGANIZATION_ID=PROFILE_ORGANIZATION,
        DEVFEED_USER_OIDC_TOKEN_ENDPOINT_AUTH_METHOD="none",
        OTEL_SDK_DISABLED="true",
    )


def settings_payload(helper):
    columns = [
        {
            "id": str(helper.fixture_id("column", n)),
            "feed": {"kind": "topic", "id": str(helper.fixture_id("topics", n))},
        }
        for n in range(1, 13)
    ]
    return {
        "languages": ["en"],
        "view": "cards",
        "columns": columns,
        "columns_initialized": True,
        "my_feed_columns": columns,
        "my_feed_columns_initialized": True,
        "feed_views": {f"topic:{helper.fixture_id('topics', n)}": "compact" for n in range(1, 256)}
        | {"my_feed": "columns"},
    }


def seed_fixture(engine, counts, helper):
    from devfeed_core import models
    from sqlalchemy import select, text

    tables = {name: models.Base.metadata.tables[name] for name in COUNTS}
    tables |= {
        name: models.Base.metadata.tables[name]
        for name in ("user_topics", "user_sources", "user_tags")
    }
    articles, users = counts["articles"], counts["user_accounts"]
    article_n = f"1+(n-1)%{articles}"
    approved_sources = min(counts["sources"] * 9 // 10, counts["sources"] - 100)
    identifier, seed = helper.identifier, helper.seed_rows
    started = time.perf_counter()
    fixture_clock = datetime.now(UTC).replace(microsecond=0)
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout='0'"))
        for name, fixture_table in tables.items():
            if connection.scalar(select(fixture_table).limit(1)) is not None:
                raise ValueError(f"Seeding requires empty fixture tables; {name} is populated")
        # Preserve business projections (slugs/facts), FKs and constraints; suppress
        # only background queue notifications that are outside these reader APIs.
        disabled = (
            connection.execute(
                text("""
            SELECT c.relname AS table_name,t.tgname AS trigger_name
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace ns ON ns.oid=c.relnamespace
            WHERE ns.nspname=current_schema() AND NOT t.tgisinternal AND t.tgenabled='O'
              AND (t.tgname LIKE 'recommendation_%' OR t.tgname LIKE 'search_%'
                   OR t.tgname='read_model_changed'
                   OR t.tgname='partner_matching_source_changed')
            ORDER BY c.relname,t.tgname
        """)
            )
            .mappings()
            .all()
        )
        quote = connection.dialect.identifier_preparer.quote
        for trigger in disabled:
            connection.execute(
                text(
                    f"ALTER TABLE {quote(trigger.table_name)} DISABLE TRIGGER "
                    f"{quote(trigger.trigger_name)}"
                )
            )
        now = f"'{fixture_clock.isoformat()}'::timestamptz"
        seed(
            connection,
            models.Source,
            counts["sources"],
            {
                "name": "'Reader publisher '||n",
                "slug": "'reader-source-'||n",
                "source_type": "'publisher'",
                "feed_url": "'https://publisher.invalid/'||n||'/rss'",
                "approval_status": f"CASE WHEN n>{approved_sources} "
                "THEN 'pending' ELSE 'approved' END",
            },
        )
        seed(
            connection,
            models.Topic,
            counts["topics"],
            {
                "name": "'Reader topic '||n",
                "slug": "'reader-topic-'||n",
                "kind": "'technology'",
                "status": "CASE WHEN n%19=0 THEN 'rejected' ELSE 'active' END",
            },
        )
        seed(
            connection,
            models.Tag,
            counts["tags"],
            {
                "name": "'Reader tag '||n",
                "slug": "'reader-tag-'||n",
            },
        )
        seed(
            connection,
            models.Article,
            articles,
            {
                "title": "'Database engineering reader article '||n",
                "canonical_url": "'https://publisher.invalid/article/'||n",
                "url_hash": "md5('reader-profile:'||n)",
                "summary": "repeat('Synthetic database engineering evidence. ',16)",
                "language": "CASE WHEN n%10=0 THEN 'fr' ELSE 'en' END",
                "content_type": "CASE WHEN n%7=0 THEN 'news' ELSE 'article' END",
                "review_status": "CASE WHEN n%5=0 THEN 'pending' ELSE 'approved' END",
                "publication_status": "CASE WHEN n%5=0 THEN 'unpublished' ELSE 'published' END",
                "feed_at": now + "-n*interval '1 minute'",
                "discovered_at": now + "-n*interval '1 minute'",
                "published_to_feed_at": f"CASE WHEN n%5<>0 THEN {now}-n*interval '1 minute' END",
            },
        )
        source_n = (
            f"CASE WHEN n<={articles} AND n%2=0 THEN 1+(n%100) "
            f"ELSE 101+(({article_n})+(n-1)/{articles})%({counts['sources']}-101) END"
        )
        seed(
            connection,
            models.ArticleOrigin,
            counts["article_origins"],
            {
                "article_id": identifier("articles", article_n),
                "source_id": identifier("sources", source_n),
                "entry_key": "'reader-entry-'||n",
                "original_url": "'https://publisher.invalid/article/'||n",
            },
        )
        for model, table, field, kind, stride in (
            (models.ArticleTopic, "article_topics", "topic_id", "topics", 31),
            (models.ArticleTag, "article_tags", "tag_id", "tags", 17),
        ):
            usable = min(counts[kind] * 9 // 10, counts[kind] - 100)
            linked_n = (
                f"CASE WHEN n<={articles} AND n%2=0 THEN 1+(n%100) "
                f"ELSE 101+(({article_n})+((n-1)/{articles})*{stride})%({usable}-101) END"
            )
            overrides = {
                "article_id": identifier("articles", article_n),
                field: identifier(kind, linked_n),
            }
            if kind == "topics":
                overrides |= {
                    "role": f"CASE WHEN n<={articles} THEN 'primary' "
                    "WHEN n%3=0 THEN 'incidental' ELSE 'supporting' END",
                    "relevance": "0.8",
                    "evidence": "'Synthetic reader evidence'",
                }
            seed(connection, model, counts[table], overrides)
        payload = json.dumps(settings_payload(helper)).replace("'", "''")
        seed(
            connection,
            models.UserAccount,
            users,
            {
                "issuer": f"'{ISSUER}'",
                "subject": "'reader-profile-'||n",
                "organization_id": f"'{PROFILE_ORGANIZATION}'",
                "feed_settings": 'CASE WHEN n=4 THEN \'{"languages":["de"]}\'::jsonb '
                f"WHEN n=3 THEN '{payload}'::jsonb ELSE '{{}}'::jsonb END",
            },
        )
        for follow_model, _table, field, kind in (
            (models.UserTopic, "user_topics", "topic_id", "topics"),
            (models.UserSource, "user_sources", "source_id", "sources"),
            (models.UserTag, "user_tags", "tag_id", "tags"),
        ):
            # Five follows for the ordinary reader, 100 for every other active reader.
            # Reader 5 follows tail IDs with no topic/tag links or approved sources.
            candidate = (
                f"CASE WHEN 2+(n-1)/100=5 THEN {counts[kind]}-((n-1)%100) ELSE 1+(n-1)%100 END"
            )
            seed(
                connection,
                follow_model,
                (users - 1) * 100 - 95,
                {
                    "user_id": identifier("user_accounts", "2+(n-1)/100"),
                    field: identifier(kind, candidate),
                },
                where="2+(n-1)/100<>2 OR (n-1)%100<5",
            )
        seed(
            connection,
            models.ArticleLike,
            counts["article_likes"],
            {
                "user_id": identifier(
                    "user_accounts",
                    f"CASE WHEN 2+(n-1)%{users - 2}>=5 THEN 3+(n-1)%{users - 2} "
                    f"ELSE 2+(n-1)%{users - 2} END",
                ),
                "article_id": identifier(
                    "articles", f"1+((n-1)/{users - 2}+((n-1)%{users - 2})*31)%{articles}"
                ),
                "created_at": now + "-n*interval '1 second'",
            },
        )
        for trigger in disabled:
            connection.execute(
                text(
                    f"ALTER TABLE {quote(trigger.table_name)} ENABLE TRIGGER "
                    f"{quote(trigger.trigger_name)}"
                )
            )
    with engine.begin() as connection:
        connection.execute(text("ANALYZE"))
    return {
        "seconds": round(time.perf_counter() - started, 3),
        "fixture_clock_utc": fixture_clock.isoformat(),
        "disabled_queue_triggers_during_seed": [dict(row) for row in disabled],
        "constraints": "FKs/checks/indexes and business projection triggers stayed active",
        "derived_state": "Feed/settings/starters read base rows; caches disabled. Search, "
        "recommendation snapshots and admin projections are outside workload.",
    }


def dataset(engine, counts):
    from sqlalchemy import text

    with engine.connect() as connection:
        actual = {
            name: connection.scalar(text(f'SELECT count(*) FROM "{name}"'))
            for name in (*COUNTS, "user_topics", "user_sources", "user_tags")
        }
        actual["published_approved_articles"] = connection.scalar(
            text(
                "SELECT count(*) FROM articles WHERE publication_status='published' "
                "AND review_status='approved'"
            )
        )
        fixture_clock = connection.scalar(
            text("SELECT max(feed_at)+interval '1 minute' FROM articles")
        )
        resources = dict(
            connection.execute(
                text("""
            SELECT version() AS version,current_setting('shared_buffers') AS shared_buffers,
                   current_setting('work_mem') AS work_mem,
                   current_setting('max_connections') AS max_connections,
                   pg_database_size(current_database()) AS database_bytes
        """)
            )
            .mappings()
            .one()
        )
        resources["relation_bytes"] = {
            name: connection.scalar(text("SELECT pg_total_relation_size(:name)"), {"name": name})
            for name in actual
            if name != "published_approved_articles"
        }
        profiles = (
            connection.execute(
                text("""
            SELECT subject,
              (SELECT count(*) FROM user_topics f WHERE f.user_id=u.id) AS topics,
              (SELECT count(*) FROM user_sources f WHERE f.user_id=u.id) AS sources,
              (SELECT count(*) FROM user_tags f WHERE f.user_id=u.id) AS tags,
              (SELECT count(*) FROM article_likes l WHERE l.user_id=u.id) AS likes,
              octet_length(feed_settings::text) AS settings_bytes
            FROM user_accounts u WHERE subject IN
              ('reader-profile-1','reader-profile-2','reader-profile-3',
               'reader-profile-4','reader-profile-5') ORDER BY subject
        """)
            )
            .mappings()
            .all()
        )
        hottest_sources = (
            connection.execute(
                text("""
            SELECT source_id,count(*) AS origins FROM article_origins
            GROUP BY source_id ORDER BY count(*) DESC,source_id LIMIT 5
        """)
            )
            .mappings()
            .all()
        )
        disabled = connection.execute(
            text("""
            SELECT c.relname,t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema() AND NOT t.tgisinternal AND t.tgenabled='D'
        """)
        ).all()
        if disabled:
            raise ValueError(f"Measurement refuses disabled application triggers: {disabled}")
    missing = {
        name: {"actual": actual[name], "requested": count}
        for name, count in counts.items()
        if actual[name] != count
    }
    if missing:
        raise ValueError(f"Fixture differs from requested scale: {missing}")
    return {
        "counts": actual,
        "fixture_clock_utc": fixture_clock.isoformat(),
        "qualification": qualification(actual),
        "postgres": resources,
        "profiles": [dict(row) for row in profiles],
        "hottest_sources": [
            {"source_id": str(row.source_id), "origins": row.origins} for row in hottest_sources
        ],
    }


class RecordingSession:
    def __init__(self, session):
        self.session, self.statements = session, []

    def scalars(self, statement):
        self.statements.append(statement)
        return self.session.scalars(statement)


def starter_plans(factory, helper):
    from devfeed_core.models import UserAccount
    from devfeed_core.reader_column_starters import personal_column_starters
    from devfeed_core.user_settings import normalize_feed_settings
    from sqlalchemy import text

    result = {}
    for label, number in SCENARIOS.items():
        with factory() as session:
            session.execute(text("SET TRANSACTION READ ONLY"))
            session.execute(text("SET LOCAL statement_timeout='20s'"))
            account = session.get(UserAccount, helper.fixture_id("user_accounts", number))
            recorder = RecordingSession(session)
            selected = personal_column_starters(
                recorder, account.id, normalize_feed_settings(account.feed_settings)
            )
            plans = []
            for statement in recorder.statements:
                compiled = statement.compile(
                    dialect=session.bind.dialect, compile_kwargs={"literal_binds": True}
                )
                plan = session.execute(
                    text("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + str(compiled))
                ).scalar_one()[0]
                plans.append({"sql": str(compiled), "explain": plan})
            result[label] = {
                "feeds": [feed.model_dump(mode="json") for feed in selected],
                "queries": len(plans),
                "plans": plans,
            }
            if label in {"empty", "restricted", "sparse"} and selected:
                raise AssertionError(f"Expected empty starters for {label}")
            if label in {"ordinary", "prolific"} and not selected:
                raise AssertionError(f"Missing eligible starters for {label}")
    return result


def applications(factory, helper):
    from devfeed_api.dependencies import get_session as public_session
    from devfeed_api.main import create_app as public_app
    from devfeed_user_api import auth, oidc
    from devfeed_user_api.dependencies import get_session as user_session
    from devfeed_user_api.main import create_app as user_app

    def sessions():
        with factory() as session:
            yield session

    apps = {"public": public_app(), "user": user_app()}
    apps["public"].dependency_overrides[public_session] = sessions
    apps["user"].dependency_overrides[user_session] = sessions
    settings, redis = auth.get_settings(), auth.get_redis()
    identities, keys = {}, []
    session_users = SCENARIOS | {f"writer-{number}": number for number in range(6, 22)}
    for label, number in session_users.items():
        subject = f"reader-profile-{number}"
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(time.time())
        user_id = str(helper.fixture_id("user_accounts", number))
        record = {
            "subject": subject,
            "issuer": ISSUER,
            "organization_id": PROFILE_ORGANIZATION,
            "user_id": user_id,
            "expires_at": now + 3600,
            "absolute_expires_at": now + 3600,
            "renewed_at": now,
            "csrf_token": csrf,
            "policy": oidc.policy_key(settings),
            "provider_session": {
                "issuer": ISSUER,
                "client_id": settings.oidc_client_id,
                "subject": subject,
                "issued_at": now,
                "sid": None,
            },
        }
        session_key, activity_key = auth.key("session", token), auth.key("activity", user_id)
        redis.set(session_key, json.dumps(record), ex=3600)
        redis.set(activity_key, "1", ex=3600)
        keys.extend([session_key, activity_key])
        identities[label] = {
            "Cookie": f"{oidc.cookie_name(settings, 'session')}={token}",
            "Origin": ORIGIN,
            "X-CSRF-Token": csrf,
        }
    return apps, identities, (redis, keys)


def workload(helper):
    user = [
        ("feed-settings", "user", "/v1/user/settings/feed", "prolific"),
        (FEED_VIEW_LABEL, "user", "/v1/user/settings/feed-view?feed=my_feed", "prolific"),
        ("columns-latest", "user", "/v1/user/settings/columns?workspace=latest", "prolific"),
        ("columns-my-feed", "user", "/v1/user/settings/columns?workspace=my_feed", "prolific"),
    ]
    user += [
        (f"starters-{label}", "user", "/v1/user/settings/columns/starters", label)
        for label in SCENARIOS
    ]
    public = [
        ("latest", "public", "/v1/feed?limit=24&languages=en&diverse=true", "empty"),
        (
            "column-source",
            "public",
            f"/v1/feed?limit=24&languages=en&source_id={helper.fixture_id('sources', 3)}",
            "empty",
        ),
        (
            "column-topic",
            "public",
            f"/v1/feed?limit=24&languages=en&topic={helper.fixture_id('topics', 3)}",
            "empty",
        ),
        ("column-tag", "public", "/v1/feed?limit=24&languages=en&tag=reader-tag-3", "empty"),
    ]
    return user + public


def percentile(values, fraction):
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * fraction) - 1)], 3)


def request_summary(records):
    values = [row["ms"] for row in records]
    return {
        "samples": len(records),
        "p50_ms": percentile(values, 0.5),
        "p95_ms": percentile(values, 0.95),
        "p99_ms": percentile(values, 0.99),
        "statuses": dict(Counter(row["status"] for row in records)),
        "queries_min": min(row["queries"] for row in records),
        "queries_max": max(row["queries"] for row in records),
        "invalid_payloads": sum(not row["valid"] for row in records),
    }


def query_budget(label, sql_budgets=None):
    if sql_budgets is not None and label in sql_budgets:
        return sql_budgets[label]
    if label.startswith("starters-"):
        return 5
    if label == "latest":
        return 6
    if label.startswith("column-"):
        return 4
    return 1


def route_failure_reasons(row, ceiling, p95_budget_ms):
    reasons = []
    if set(map(int, row["statuses"])) != {200} or row["invalid_payloads"]:
        reasons.append("failed responses")
    if row["queries_min"] < 1 or row["queries_max"] > ceiling:
        reasons.append("SQL budget")
    if row["p95_ms"] > p95_budget_ms:
        reasons.append("p95 latency")
    return reasons


def measurement_gates(measurements, p95_budget_ms, sql_budgets=None):
    failures = []
    for concurrency, measurement in measurements.items():
        for label, row in measurement["routes"].items():
            reasons = route_failure_reasons(row, query_budget(label, sql_budgets), p95_budget_ms)
            if reasons:
                failures.append({"concurrency": concurrency, "route": label, "reasons": reasons})
    return {"passed": not failures, "p95_budget_ms": p95_budget_ms, "failures": failures}


async def column_wave(app, helper, repeats):
    """Twelve independent columns, admitting three HTTP reads at a time."""
    import httpx

    slots, samples = asyncio.Semaphore(3), []
    statuses: Counter[int] = Counter()
    paths = ["/v1/feed?limit=24&languages=en&diverse=true"]
    paths += [
        f"/v1/feed?limit=24&languages=en&source_id={helper.fixture_id('sources', n)}"
        for n in (3, 5, 7, 9)
    ]
    paths += [
        f"/v1/feed?limit=24&languages=en&topic={helper.fixture_id('topics', n)}"
        for n in (3, 5, 7, 9)
    ]
    paths += [f"/v1/feed?limit=24&languages=en&tag=reader-tag-{n}" for n in (3, 5, 7)]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url=ORIGIN
    ) as client:

        async def read(path):
            async with slots:
                response = await client.get(path)
                statuses[response.status_code] += 1
                if response.status_code == 200 and len(response.json().get("items", [])) != 24:
                    raise ValueError("A positive column must return 24 articles")

        for _ in range(repeats):
            started = time.perf_counter()
            await asyncio.gather(*(read(path) for path in paths))
            samples.append((time.perf_counter() - started) * 1000)
    return {
        "columns": 12,
        "concurrency": 3,
        "samples": repeats,
        "p50_ms": percentile(samples, 0.5),
        "p95_ms": percentile(samples, 0.95),
        "p99_ms": percentile(samples, 0.99),
        "statuses": dict(statuses),
        "raw_samples_ms": samples,
        "paths": paths,
    }


def restore_settings(factory, snapshots):
    from devfeed_core.models import UserAccount
    from sqlalchemy import update

    with factory.begin() as session:
        for identifier, saved in snapshots.items():
            session.execute(
                update(UserAccount).where(UserAccount.id == identifier).values(feed_settings=saved)
            )


def write_payload(kind, number, helper):
    if kind == FEED_VIEW_LABEL:
        return {"view": "compact" if number % 2 else "stacked"}
    return {"columns": settings_payload(helper)["columns"]}


async def sample_write_request(client, path, identity, payload, tag, counters):
    token = REQUEST.set(tag)
    started = time.perf_counter()
    try:
        response = await client.put(path, headers=identity, json=payload)
        body = response.json() if response.status_code == 200 else {}
        valid = all(body.get(field) == value for field, value in payload.items())
        return {
            "ms": (time.perf_counter() - started) * 1000,
            "status": response.status_code,
            "queries": counters[tag],
            "valid": valid,
        }
    finally:
        REQUEST.reset(token)


async def sample_write_phase(client, identities, helper, counters, repeats, operation, contention):
    kind, path = operation
    label = f"{kind}-{'same-account' if contention else 'parallel-accounts'}"
    records: list[dict] = []

    async def worker(slot):
        identity = identities[f"writer-{6 if contention else 6 + slot}"]
        for number in range(slot, repeats, 16):
            payload = write_payload(kind, number, helper)
            record = await sample_write_request(
                client, path, identity, payload, f"write:{label}:{number}", counters
            )
            records.append(record)

    await asyncio.gather(*(worker(slot) for slot in range(16)))
    return label, request_summary(records) | {
        "concurrency": 16,
        "accounts": 1 if contention else 16,
        "raw_samples": records,
    }


async def sample_writes(app, identities, factory, engine, helper, repeats):
    """Separate parallel accounts from deliberate contention on one account row."""
    import httpx
    from devfeed_core.models import UserAccount
    from sqlalchemy import event, select

    identifiers = [helper.fixture_id("user_accounts", n) for n in range(6, 22)]
    with factory() as session:
        snapshots = dict(
            session.execute(
                select(UserAccount.id, UserAccount.feed_settings).where(
                    UserAccount.id.in_(identifiers)
                )
            ).all()
        )
    counters: Counter[str] = Counter()
    guard = threading.Lock()

    def count_query(*_):
        if tag := REQUEST.get():
            with guard:
                counters[tag] += 1

    results = {}
    event.listen(engine, "before_cursor_execute", count_query)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url=ORIGIN
        ) as client:
            for operation in (
                (FEED_VIEW_LABEL, "/v1/user/settings/feed-view?feed=my_feed"),
                ("columns", "/v1/user/settings/columns?workspace=my_feed"),
            ):
                for contention in (False, True):
                    label, result = await sample_write_phase(
                        client, identities, helper, counters, repeats, operation, contention
                    )
                    results[label] = result
                    # Restore the same JSON before the next phase and revision.
                    restore_settings(factory, snapshots)
    finally:
        event.remove(engine, "before_cursor_execute", count_query)
        restore_settings(factory, snapshots)
    return results


def response_shape(response, label, service):
    payload = response.json() if response.status_code == 200 else None
    items = payload.get("items") if isinstance(payload, dict) else None
    item_count = len(items) if isinstance(items, list) else None
    valid = isinstance(payload, dict) and (service != "public" or item_count is not None)
    if service == "public" or label.startswith("recommended-"):
        valid = valid and item_count == 24
    return valid, item_count


async def sample_api_request(clients, identities, counters, number, target):
    label, service, url, identity = target
    tag = f"{label}:{number}"
    token = REQUEST.set(tag)
    started = time.perf_counter()
    try:
        response = await clients[service].get(
            url, headers=identities[identity] if service == "user" else {}
        )
        valid, item_count = response_shape(response, label, service)
        return {
            "route": label,
            "ms": (time.perf_counter() - started) * 1000,
            "status": response.status_code,
            "queries": counters[tag],
            "bytes": len(response.content),
            "valid": valid,
            "items": item_count,
        }
    finally:
        REQUEST.reset(token)


async def sample_apis(apps, identities, engine, helper, *, repeats, concurrency, targets=None):
    import httpx
    from sqlalchemy import event

    counters: Counter[str] = Counter()
    lock = threading.Lock()

    def count_query(*_):
        if tag := REQUEST.get():
            with lock:
                counters[tag] += 1

    clients = {
        name: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url=ORIGIN
        )
        for name, app in apps.items()
    }
    slots, records = asyncio.Semaphore(concurrency), []

    async def request(number, target):
        async with slots:
            records.append(await sample_api_request(clients, identities, counters, number, target))

    event.listen(engine, "before_cursor_execute", count_query)
    try:
        await asyncio.gather(
            *(
                request(n, target)
                for n in range(repeats)
                for target in (targets or workload(helper))
            )
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_query)
        for client in clients.values():
            await client.aclose()
    result = {}
    for label in sorted({row["route"] for row in records}):
        rows = [row for row in records if row["route"] == label]
        result[label] = request_summary(rows) | {
            "response_bytes_max": max(row["bytes"] for row in rows),
            "items_min": min(
                (row["items"] for row in rows if row["items"] is not None), default=None
            ),
            "items_max": max(
                (row["items"] for row in rows if row["items"] is not None), default=None
            ),
        }
    return {"routes": result, "raw_samples": records}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, default=ROOT)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--plans-only", action="store_true")
    parser.add_argument("--recommendations-only", action="store_true")
    parser.add_argument("--prepare-recommendations", action="store_true")
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--repeats", type=int, default=100)
    parser.add_argument("--concurrency", default="1,8,16")
    parser.add_argument("--p95-budget-ms", type=float, default=2000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.counts = fixture_counts(args.scale)
    args.concurrency = [int(value) for value in args.concurrency.split(",")]
    if (
        not 1 <= args.repeats <= 500
        or not args.concurrency
        or any(not 1 <= n <= 16 for n in args.concurrency)
        or not math.isfinite(args.p95_budget_ms)
        or args.p95_budget_ms <= 0
    ):
        parser.error("Use repeats 1..500 and concurrency values 1..16")
    if args.prepare_recommendations and not args.recommendations_only:
        parser.error("--prepare-recommendations requires --recommendations-only")
    return args


def configure_runtime(database, redis):
    helper = helper_module()
    helper.validate_targets(database, redis)
    isolated_environment(database, redis)
    os.environ.update(helper.ISOLATED_ENV)
    from devfeed_core.config import Settings, get_settings
    from devfeed_core.db import create_database_engine
    from devfeed_user_api.config import Settings as UserSettings
    from devfeed_user_api.config import get_settings as user_settings
    from devfeed_user_api.dependencies import get_redis as user_redis
    from sqlalchemy.orm import sessionmaker

    Settings.model_config["env_file"] = None
    UserSettings.model_config["env_file"] = None
    get_settings.cache_clear()
    user_settings.cache_clear()
    if user_redis.cache_info().currsize:
        user_redis().close()
    user_redis.cache_clear()
    engine = create_database_engine(get_settings())
    return helper, engine, sessionmaker(engine, expire_on_commit=False)


def initial_report(args, provenance):
    identity = source_identity(args.app_root)
    if args.recommendations_only:
        identity["recommendation_fixture_sha256"] = hashlib.sha256(
            (ROOT / "scripts/reader_recommendation_fixture.py").read_bytes()
        ).hexdigest()
    return {
        "complete": False,
        "started_at": datetime.now(UTC).isoformat(),
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=args.app_root, text=True
        ).strip(),
        "imports": provenance,
        "source_identity": identity,
        "production_snapshot_utc": PRODUCTION_SNAPSHOT_AT,
        "production": PRODUCTION,
        "requested_counts": args.counts,
        "hardware": {
            "architecture": platform.machine(),
            "cpus": os.cpu_count(),
            "memory_bytes": os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"),
        },
        "workload": {
            "profile": "recommendations" if args.recommendations_only else "reader-views",
            "scale": args.scale,
            "repeats": args.repeats,
            "concurrency": args.concurrency,
            "cache": "Response cache disabled; authoritative Latest/recommendation "
            "Redis cursor sequences active",
            "scope": "Warm PostgreSQL/authenticated ASGI; includes admitted "
            "thread/database waits; excludes generator semaphore queue, "
            "OIDC exchange, network, ingress, browser and ARM64 production; "
            "public/user apps share this benchmark interpreter",
            "tail_samples_qualified": args.repeats >= 100,
            "scenarios": SCENARIOS,
        },
    }


def checkpoint_report(output, path, stage):
    output["last_completed_stage"] = stage
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(output, indent=2) + "\n")
    temporary.replace(path)
    print(json.dumps({"stage": stage}), file=sys.stderr, flush=True)


@dataclass
class ProfileRun:
    args: argparse.Namespace
    factory: Any
    engine: Any
    helper: Any
    output: dict

    def checkpoint(self, stage):
        checkpoint_report(self.output, self.args.output, stage)


def measure_routes(run, apps, identities, *, targets=None):
    run.output["measurements"] = {}
    for limit in run.args.concurrency:
        asyncio.run(
            sample_apis(
                apps,
                identities,
                run.engine,
                run.helper,
                repeats=1,
                concurrency=limit,
                targets=targets,
            )
        )
        run.output["measurements"][str(limit)] = asyncio.run(
            sample_apis(
                apps,
                identities,
                run.engine,
                run.helper,
                repeats=run.args.repeats,
                concurrency=limit,
                targets=targets,
            )
        )
        run.checkpoint(f"concurrency_{limit}")


def core_load(run, apps, identities):
    output = run.output
    measure_routes(run, apps, identities)
    output["column_wave"] = asyncio.run(column_wave(apps["public"], run.helper, run.args.repeats))
    output["writes"] = asyncio.run(
        sample_writes(
            apps["user"], identities, run.factory, run.engine, run.helper, run.args.repeats
        )
    )
    output["gates"] = measurement_gates(
        output["measurements"] | {"writes": {"routes": output["writes"]}},
        run.args.p95_budget_ms,
        sql_budgets={label: 3 for label in output["writes"]},
    )
    if set(output["column_wave"]["statuses"]) != {200}:
        output["gates"]["passed"] = False
        output["gates"]["failures"].append(
            {"route": "column_wave", "reasons": ["failed responses"]}
        )


def recommendation_module():
    spec = importlib.util.spec_from_file_location(
        "reader_recommendation_fixture", ROOT / "scripts/reader_recommendation_fixture.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def recommendation_page_proof(app, identities, fixture, qualification):
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        pending = await client.get(RECOMMENDED_FEED_PATH, headers=identities["ordinary"])
        ready = await client.get(RECOMMENDED_FEED_PATH, headers=identities["prolific"])
        if pending.status_code != 200 or ready.status_code != 200:
            raise ValueError("Recommendation page qualification failed")
        fixture.validate_pending_page(pending.json())
        previous = fixture.validate_ready_page(ready.json(), qualification["ready"])
        second = await client.get(
            "/v1/user/feed?" + urlencode({"limit": 24, "cursor": ready.json()["next_cursor"]}),
            headers=identities["prolific"],
        )
        if second.status_code != 200:
            raise ValueError("Recommendation cursor page qualification failed")
        fixture.validate_ready_page(second.json(), qualification["ready"], previous_ids=previous)
        return {
            "pending": pending.json(),
            "ready_first": ready.json(),
            "ready_second": second.json(),
        }


def recommended_load(run, apps, identities):
    output = run.output
    fixture = recommendation_module()
    proof = fixture.qualification(run.factory, run.helper)
    output["recommendation_qualification"] = proof
    run.checkpoint("recommendation_qualified")
    if not proof["ready"]["qualified"] or not proof["pending"]["qualified"]:
        raise SystemExit("Recommendation measurement requires genuine ready and pending states")
    output["recommendation_pages"] = asyncio.run(
        recommendation_page_proof(apps["user"], identities, fixture, proof)
    )
    targets = [
        ("recommended-starter", "user", RECOMMENDED_FEED_PATH, "ordinary"),
        ("recommended-ready", "user", RECOMMENDED_FEED_PATH, "prolific"),
    ]
    measure_routes(run, apps, identities, targets=targets)
    output["gates"] = measurement_gates(
        output["measurements"],
        run.args.p95_budget_ms,
        sql_budgets={"recommended-starter": 9, "recommended-ready": 6},
    )


def profile_load(run):
    apps, identities, cleanup = applications(run.factory, run.helper)
    try:
        load = recommended_load if run.args.recommendations_only else core_load
        load(run, apps, identities)
    finally:
        cleanup[0].delete(*cleanup[1])


def run_profile(args, provenance, database, redis):
    helper, engine, factory = configure_runtime(database, redis)
    output = initial_report(args, provenance)
    run = ProfileRun(args, factory, engine, helper, output)

    try:
        if args.seed:
            from alembic import command
            from alembic.config import Config

            command.upgrade(Config(str(args.app_root / "alembic.ini")), "head")
            output["seed"] = seed_fixture(engine, args.counts, helper)
            run.checkpoint("seeded")
        output["dataset"] = dataset(engine, args.counts)
        run.checkpoint("cardinalities")
        if args.prepare_recommendations:
            fixture = recommendation_module()
            output["recommendation_preparation"] = fixture.prepare_fixture(engine, factory, helper)
            run.checkpoint("recommendation_prepared")
            prepared = output["recommendation_preparation"]
            if not prepared["cold_build"]["completed"] or not prepared["ready_proof"]["qualified"]:
                raise SystemExit("Real recommendation build failed; inspect retained evidence")
        if not args.seed_only:
            output["starter_plans"] = starter_plans(factory, helper)
            run.checkpoint("starter_plans")
        if not args.seed_only and not args.plans_only:
            profile_load(run)
        usage = resource.getrusage(resource.RUSAGE_SELF)
        output["process"] = {
            "peak_rss_kib": usage.ru_maxrss,
            "cpu_user_seconds": usage.ru_utime,
            "cpu_system_seconds": usage.ru_stime,
        }
        output["complete"] = True
        run.checkpoint("complete")
        if output.get("gates", {}).get("passed") is False:
            raise SystemExit("Reader profile gates failed; inspect report")
    finally:
        engine.dispose()


def main():
    args = parse_args()
    database, redis = (
        os.environ[name] for name in ("DEVFEED_TEST_DATABASE_URL", "DEVFEED_TEST_REDIS_URL")
    )
    with revision_imports(args.app_root.resolve()) as provenance:
        run_profile(args, provenance, database, redis)


if __name__ == "__main__":
    main()
