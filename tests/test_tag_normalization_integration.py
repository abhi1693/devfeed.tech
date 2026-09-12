import importlib.util
from pathlib import Path

import pytest
from devfeed_core.models import Article, ArticleTag, Tag, Topic
from devfeed_core.source_tags import resolve_source_tags
from sqlalchemy import select

SPEC = importlib.util.spec_from_file_location(
    "normalize_tags", Path(__file__).resolve().parents[1] / "scripts/normalize_tags.py"
)
assert SPEC and SPEC.loader
cleanup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup)
pytestmark = pytest.mark.integration


def test_cleanup_merges_links_preserves_manual_origin_and_retains_old_aliases(database):
    with database.begin() as session:
        target = Tag(name="AI", slug="ai", aliases=["artificial intelligence"])
        duplicate = Tag(name="#ai", slug="sharp-ai", aliases=[])
        quoted = Tag(name='"audits"', slug="audits", aliases=[])
        article = Article(
            title="Article", canonical_url="https://example.com/one", url_hash="a" * 64
        )
        session.add_all([target, duplicate, quoted, article])
        session.flush()
        session.add_all(
            [
                ArticleTag(article_id=article.id, tag_id=target.id, origin="ai"),
                ArticleTag(article_id=article.id, tag_id=duplicate.id, origin="manual"),
            ]
        )
        session.flush()
        preview = cleanup.normalize_tags(session)
        assert len(preview["changes"]) == 2 and not preview["conflicts"]
        assert duplicate.name == "#ai" and quoted.name == '"audits"'
        result = cleanup.normalize_tags(session, apply=True)
        assert len(result["changes"]) == 2 and not result["conflicts"]
        assert session.get(Tag, duplicate.id) is None
        assert quoted.name == "audits" and quoted.slug == "audits"
        assert set(target.aliases) == {"artificial intelligence", "#ai", "sharp-ai"}
        link = session.scalar(select(ArticleTag))
        assert link.tag_id == target.id and link.origin == "manual"
        resolved, created = resolve_source_tags(session, ["#ai", "AI", "sharp-ai"])
        assert not created and set(resolved.values()) == {target.id}
        assert cleanup.normalize_tags(session)["changes"] == []


def test_cleanup_keeps_custom_slugs_and_reports_conflicting_topic_links(database):
    with database.begin() as session:
        topics = [
            Topic(name=name, slug=name.lower(), status="active", kind="technology")
            for name in ["One", "Two"]
        ]
        session.add_all(topics)
        session.flush()
        target = Tag(name="AI", slug="ai", aliases=[], topic_id=topics[0].id)
        duplicate = Tag(
            name="#ai", slug="sharp-ai", aliases=[], topic_id=topics[1].id, auto_link_topic=False
        )
        custom = Tag(name='"Kubernetes"', slug="cluster-platform", aliases=[])
        session.add_all([target, duplicate, custom])
        session.flush()
        result = cleanup.normalize_tags(session)
        assert len(result["conflicts"]) == 1
        assert result["changes"][0]["slug"] == "cluster-platform"
        assert duplicate.topic_id == topics[1].id
