"""Upgrade populated release schemas in CI-owned databases and verify retained data."""

import ast
import json
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from services import disposable_services
from sqlalchemy import MetaData, Table, create_engine, select, text

ROOT = Path(__file__).resolve().parents[2]
TABLES = (
    "topics",
    "topic_proposals",
    "articles",
    "user_accounts",
    "article_topics",
    "user_topics",
    "article_bookmarks",
)


def release_baseline():
    tag = subprocess.check_output(
        ["git", "describe", "--tags", "--match", "v[0-9]*", "--abbrev=0", "HEAD^"],
        cwd=ROOT,
        text=True,
    ).strip()
    source = subprocess.check_output(
        ["git", "show", f"{tag}:packages/core/src/version.py"],
        cwd=ROOT,
        text=True,
    )
    revision = next(
        ast.literal_eval(node.value)
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "SCHEMA_REVISION"
            for target in node.targets
        )
    )
    # Building the old schema from today's chain is valid only if its historical
    # migration files are unchanged from the release. Never silently rewrite history.
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", tag, "migrations/versions"],
        cwd=ROOT,
        text=True,
    ).splitlines()
    for path in paths:
        if path.endswith(".py"):
            released = subprocess.check_output(["git", "show", f"{tag}:{path}"], cwd=ROOT)
            if not (ROOT / path).exists() or (ROOT / path).read_bytes() != released:
                raise ValueError(f"Released migration changed or disappeared: {tag}:{path}")
    return tag, revision


def seed(connection, legacy):
    metadata = MetaData()
    tables = {name: Table(name, metadata, autoload_with=connection) for name in TABLES}
    now = datetime(2026, 1, 1, tzinfo=UTC)
    topic, article, user, proposal = (uuid.UUID(int=value) for value in range(1, 5))
    kind = "programming language" if legacy else "language"
    rows = {
        "topics": dict(
            id=topic,
            name="Python",
            slug="python",
            kind=kind,
            aliases=["py"],
            status="active",
            facts=[],
            created_at=now,
            updated_at=now,
        ),
        "topic_proposals": dict(
            id=proposal,
            batch_id=uuid.UUID(int=5),
            topic_id=topic,
            slug="python",
            action="update",
            origin="import",
            source_name="CI",
            proposed={"kind": kind, "name": "Python"},
            baseline={"draft": {"kind": kind, "name": "Python"}},
            applied={"draft": {"kind": kind, "name": "Python"}},
            evidence=[],
            created_by={"subject": "migration-ci"},
            status="pending",
            created_at=now,
        ),
        "articles": dict(
            id=article,
            canonical_url="https://example.invalid/migration-check",
            url_hash="a" * 64,
            title="Preserve this article",
            slug="migration-check",
            summary="Stored before the upgrade",
            content_type="article",
            feed_at=now,
            discovered_at=now,
        ),
        "user_accounts": dict(
            id=user,
            issuer="https://identity.invalid",
            subject="upgrade-user",
            organization_id="ci",
            name="Migration reader",
            username="migration-reader",
            profile={"bio": "Retain this profile"},
            created_at=now,
            last_seen_at=now,
        ),
        "article_topics": dict(
            article_id=article,
            topic_id=topic,
            role="primary",
            relevance=1,
            evidence="Original association",
            origin="manual",
        ),
        "user_topics": dict(user_id=user, topic_id=topic, created_at=now),
        "article_bookmarks": dict(user_id=user, article_id=article, created_at=now),
    }
    for name, values in rows.items():
        connection.execute(tables[name].insert().values(**values))
    return snapshot(connection), (topic, article, user)


def snapshot(connection):
    metadata = MetaData()
    return {
        name: [
            dict(row)
            for row in connection.execute(
                select(Table(name, metadata, autoload_with=connection))
            ).mappings()
        ]
        for name in TABLES
    }


def verify(connection, before, legacy):
    after = snapshot(connection)
    if legacy:
        before["topics"][0]["kind"] = "language"
        proposal = before["topic_proposals"][0]
        proposal["proposed"]["kind"] = "language"
        proposal["baseline"]["draft"]["kind"] = "language"
        proposal["applied"]["draft"]["kind"] = "language"
    for table, old_rows in before.items():
        assert len(after[table]) == len(old_rows), f"Lost or duplicated rows in {table}"
        for old, new in zip(old_rows, after[table], strict=True):
            assert all(new.get(key) == value for key, value in old.items()), (
                f"Changed stored data in {table}"
            )


def exercise_current_models(engine, ids):
    from devfeed_core.models import Article, ArticleBookmark, Topic, UserAccount
    from devfeed_core.version import SCHEMA_REVISION
    from sqlalchemy.orm import Session

    topic, article, user = ids
    with Session(engine) as session, session.begin():
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_REVISION
        assert session.get(Topic, topic).kind == "language"
        assert session.get(Article, article).title == "Preserve this article"
        assert session.get(ArticleBookmark, (user, article)) is not None
        account = session.get(UserAccount, user)
        assert account.profile["bio"] == "Retain this profile"
        account.profile = {**account.profile, "bio": "Written after the upgrade"}
    with Session(engine) as session:
        assert session.get(UserAccount, user).profile["bio"] == "Written after the upgrade"


def check_upgrade(revision, label):
    with disposable_services() as env, tempfile.TemporaryDirectory() as directory:
        mapping = Path(directory) / "topic-kinds.json"
        mapping.write_text(json.dumps({"programming language": "language"}))
        env["DEVFEED_TOPIC_KIND_MAP_PATH"] = str(mapping)
        command = [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade"]
        subprocess.run([*command, revision], env=env, cwd=directory, check=True)
        engine = create_engine(env["DEVFEED_DATABASE_URL"])
        try:
            legacy = revision == "0018"
            with engine.begin() as connection:
                before, ids = seed(connection, legacy)
            subprocess.run([*command, "head"], env=env, cwd=directory, check=True)
            with engine.connect() as connection:
                verify(connection, before, legacy)
            exercise_current_models(engine, ids)
            print(
                f"PASS: {label} ({revision}) -> head; "
                "stored records, relationships and application reads/writes"
            )
        finally:
            engine.dispose()


if __name__ == "__main__":
    tag, revision = release_baseline()
    check_upgrade(revision, tag)
    if revision != "0018":
        check_upgrade("0018", "populated legacy topic-kind schema")
