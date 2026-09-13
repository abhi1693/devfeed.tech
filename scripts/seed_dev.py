#!/usr/bin/env python3
"""Load the checked-in published sample into this checkout's Compose PostgreSQL."""

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]


def validate_snapshot(data):
    if data.get("version") != 1 or not data.get("articles"):
        raise ValueError("Expected a version 1 published seed with articles")
    topics = {str(UUID(t["id"])) for t in data["topics"]}
    seen = set()
    for article in data["articles"]:
        identifier = str(UUID(article["id"]))
        if identifier in seen:
            raise ValueError("Duplicate article in seed")
        seen.add(identifier)
        if not article.get("sources") or not article.get("published_to_feed_at"):
            raise ValueError("Seed articles must have published provenance and sources")
        for topic in article["topics"]:
            if str(UUID(topic["id"])) not in topics:
                raise ValueError("Missing referenced topic")
        for field in ("published_at", "feed_at", "published_to_feed_at"):
            if article.get(field) and datetime.fromisoformat(article[field]).tzinfo is None:
                raise ValueError("Seed dates must include a timezone")
    return data


def validate_target(url, database, address, allowed_addresses):
    if (
        url.host != "postgres"
        or url.database != "devfeed"
        or database != "devfeed"
        or str(address) not in allowed_addresses
    ):
        raise ValueError("Refusing to seed anything except the local Compose devfeed database")


def import_sample(session, data):
    # A plain Session deliberately avoids application hooks that create AI/notification jobs.
    # PostgreSQL search change triggers still run in this same atomic transaction.
    from devfeed_core.models import (
        Article,
        ArticleOrigin,
        ArticleTag,
        ArticleTopic,
        Source,
        Tag,
        Topic,
        TopicRelationshipScan,
    )
    from devfeed_core.topic_relationships import topic_snapshot
    from devfeed_core.urls import fingerprint
    from sqlalchemy import or_, select, text

    session.execute(text("SELECT pg_advisory_xact_lock(736334781)"))
    counts = dict(articles=0, sources=0, topics=0, tags=0, skipped_articles=0)
    sources, topics, tags = {}, {}, {}
    source_data = {s["id"]: s for a in data["articles"] for s in a["sources"]}
    for key, value in source_data.items():
        source = session.scalar(
            select(Source).where(or_(Source.id == UUID(key), Source.slug == value["slug"]))
        )
        if source is None:
            source = Source(
                id=UUID(key),
                **{
                    k: value.get(k)
                    for k in (
                        "name",
                        "slug",
                        "description",
                        "website_url",
                        "logo_url",
                        "image_url",
                        "language",
                    )
                },
                source_type=value.get("source_type", "publisher"),
                feed_url=f"https://seed.invalid/{value['slug']}.xml",
                enabled=True,
                next_fetch_at=datetime(2100, 1, 1, tzinfo=UTC),
                approval_status="approved",
                publication_policy="manual",
                review_note="Development seed: polling deferred until 2100.",
            )
            session.add(source)
            counts["sources"] += 1
        sources[key] = source.id
    for value in data["topics"]:
        key = value["id"]
        topic = session.scalar(
            select(Topic).where(or_(Topic.id == UUID(key), Topic.slug == value["slug"]))
        )
        if topic is None:
            topic = Topic(
                id=UUID(key),
                status="active",
                aliases=[],
                **{
                    k: value.get(k)
                    for k in (
                        "name",
                        "slug",
                        "kind",
                        "description",
                        "ai_description",
                        "website_url",
                        "logo_url",
                    )
                },
            )
            session.add(topic)
            session.flush()
            session.add(
                TopicRelationshipScan(
                    topic_id=topic.id,
                    topic_snapshot=topic_snapshot(topic),
                    next_run_at=datetime(2100, 1, 1, tzinfo=UTC),
                    last_error="Development seed: automatic relationship research deferred.",
                )
            )
            counts["topics"] += 1
        topics[key] = topic.id
    for value in sorted({t for a in data["articles"] for t in a["tags"]}):
        tag = session.scalar(select(Tag).where(Tag.slug == value))
        if tag is None:
            tag = Tag(
                id=uuid5(NAMESPACE_URL, f"devfeed-dev-seed:tag:{value}"),
                name=value,
                slug=value,
                auto_link_topic=False,
            )
            session.add(tag)
            counts["tags"] += 1
        tags[value] = tag.id
    session.flush()
    for value in data["articles"]:
        identifier, digest = UUID(value["id"]), fingerprint(value["canonical_url"])
        exists = session.scalar(
            select(Article.id).where(
                or_(
                    Article.id == identifier,
                    Article.url_hash == digest,
                    Article.slug == value["slug"],
                )
            )
        )
        if exists:
            counts["skipped_articles"] += 1
            continue
        article = Article(
            id=identifier,
            url_hash=digest,
            review_status="approved",
            publication_status="published",
            editorial_revision=1,
            **{
                k: value.get(k)
                for k in (
                    "slug",
                    "canonical_url",
                    "title",
                    "summary",
                    "ai_summary",
                    "ai_description",
                    "author",
                    "image_url",
                    "language",
                    "content_type",
                    "content_format",
                    "metadata_source_type",
                )
            },
            **{
                k: datetime.fromisoformat(value[k]) if value.get(k) else None
                for k in ("published_at", "feed_at", "discovered_at", "published_to_feed_at")
            },
            classification_provenance={
                "origin": "development_seed",
                "source": data["source"],
                "captured_at": data["captured_at"],
            },
        )
        session.add(article)
        session.flush()
        for source in value["sources"]:
            origin = next(
                (o for o in value.get("origins", []) if o["source_id"] == source["id"]), {}
            )
            session.add(
                ArticleOrigin(
                    article_id=identifier,
                    source_id=sources[source["id"]],
                    entry_key=fingerprint(f"development-seed:{identifier}:{source['id']}"),
                    original_url=origin.get("original_url", value["canonical_url"]),
                    source_metadata={"origin": "development_seed"},
                )
            )
        for tag in value["tags"]:
            session.add(ArticleTag(article_id=identifier, tag_id=tags[tag], origin="source"))
        for topic in value["topics"]:
            session.add(
                ArticleTopic(
                    article_id=identifier,
                    topic_id=topics[topic["id"]],
                    role=topic["role"],
                    relevance=topic["relevance"],
                    origin="manual",
                    evidence="Existing public classification copied into the development database.",
                )
            )
        counts["articles"] += 1
    session.flush()
    return counts


def apply_snapshot(data, addresses):
    from devfeed_core.cache import invalidate_public_cache
    from devfeed_core.db import get_engine
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    validate_snapshot(data)
    engine = get_engine()
    with Session(engine) as session, session.begin():
        database, address = session.execute(
            text("SELECT current_database(), inet_server_addr()")
        ).one()
        validate_target(engine.url, database, address, addresses)
        result = import_sample(session, data)
    invalidate_public_cache()
    print(json.dumps(result))


def command(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=ROOT / "dev/seed/published.json")
    parser.add_argument("--apply", nargs="+", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.apply:
        apply_snapshot(json.load(sys.stdin), args.apply)
        return
    data = validate_snapshot(json.loads(args.file.read_text()))
    context = json.loads(command("docker", "context", "inspect"))[0]
    endpoint = os.environ.get("DOCKER_HOST") or context["Endpoints"]["docker"]["Host"]
    if not endpoint.startswith("unix://"):
        raise ValueError("The dev seed requires a local Docker socket")
    container = command("docker", "compose", "ps", "-q", "postgres")
    if not container:
        raise ValueError("Start the local Compose postgres and api services first")
    details = json.loads(command("docker", "inspect", container))[0]
    labels = details["Config"]["Labels"]
    if Path(labels.get("com.docker.compose.project.working_dir", "")) != ROOT:
        raise ValueError("PostgreSQL does not belong to this checkout")
    addresses = [
        n["IPAddress"] for n in details["NetworkSettings"]["Networks"].values() if n["IPAddress"]
    ]
    if not addresses:
        raise ValueError("The local PostgreSQL container has no network address")
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            "__file__ = '/tmp/devfeed-seed.py'\n" + Path(__file__).read_text(),
            "--apply",
            *addresses,
        ],
        cwd=ROOT,
        input=json.dumps(data),
        text=True,
        check=True,
    )


if __name__ == "__main__":
    # Avoid printing database URLs or driver tracebacks containing credentials.
    try:
        main()
    except Exception as error:
        print(
            f"Seed failed ({type(error).__name__}). Check the local stack and seed file.",
            file=sys.stderr,
        )
        sys.exit(1)
