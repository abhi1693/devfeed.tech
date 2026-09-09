"""Topology is a read-only projection of stored evidence, with bounded traversal."""

import uuid
from datetime import UTC, datetime

import pytest
from devfeed_admin_api import knowledge
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTag,
    ArticleTopic,
    Source,
    Tag,
    Topic,
    TopicAnalysisJob,
    TopicRelation,
    TopicRelationProposal,
)

pytestmark = pytest.mark.integration
BASE = "/v1/admin/knowledge"


@pytest.fixture
def catalog(database):
    with database.begin() as session:
        topics = {
            name: Topic(
                name=name,
                slug=name.lower(),
                kind="technology",
                status="active",
                aliases=["shared acronym"] if name in {"React", "Vue"} else [],
            )
            for name in ["React", "JavaScript", "Vue", "Isolated", "Proposed"]
        }
        topics["Proposed"].status = "proposed"
        article = Article(
            title="Testing: where it matters",
            canonical_url="https://example.com/article",
            url_hash="a" * 64,
            summary="Article summary",
            review_status="approved",
            publication_status="published",
        )
        draft = Article(
            title="A draft", canonical_url="https://example.com/draft", url_hash="b" * 64
        )
        source = Source(
            name="Example source", feed_url="https://example.com/rss", source_type="publisher"
        )
        session.add_all([*topics.values(), article, draft, source])
        session.flush()
        tag = Tag(
            name="React testing", slug="react-testing", aliases=[], topic_id=topics["React"].id
        )
        job = TopicAnalysisJob(
            topic_id=topics["React"].id,
            input_hash="x" * 64,
            input_snapshot={},
            requested_by={},
            prompt_version="fixture",
        )
        session.add_all([tag, job])
        session.flush()
        session.add_all(
            [
                TopicRelation(
                    topic_id=topics["React"].id,
                    related_topic_id=topics["JavaScript"].id,
                    relation="uses_language",
                    evidence_url="https://react.dev/",
                ),
                TopicRelation(
                    topic_id=topics["Vue"].id,
                    related_topic_id=topics["JavaScript"].id,
                    relation="uses_language",
                ),
                TopicRelation(
                    topic_id=topics["React"].id,
                    related_topic_id=topics["Proposed"].id,
                    relation="related_to",
                ),
                ArticleTopic(
                    article_id=article.id,
                    topic_id=topics["React"].id,
                    role="primary",
                    relevance=0.95,
                    evidence="Discusses React testing",
                    origin="ai",
                ),
                ArticleTopic(
                    article_id=article.id,
                    topic_id=topics["Vue"].id,
                    role="comparison",
                    relevance=0.4,
                    evidence="Compares Vue",
                    origin="manual",
                ),
                ArticleTopic(
                    article_id=draft.id,
                    topic_id=topics["React"].id,
                    role="supporting",
                    relevance=0.8,
                    evidence="Draft evidence",
                    origin="manual",
                ),
                ArticleTag(article_id=article.id, tag_id=tag.id, origin="source"),
                *[
                    ArticleOrigin(
                        article_id=article.id,
                        source_id=source.id,
                        entry_key=str(i),
                        original_url=article.canonical_url,
                    )
                    for i in range(2)
                ],
            ]
        )
        for target, status in [
            ("Vue", "pending"),
            ("JavaScript", "approved"),
            ("Isolated", "rejected"),
        ]:
            session.add(
                TopicRelationProposal(
                    job_id=job.id,
                    topic_id=topics["React"].id,
                    related_topic_id=topics[target].id,
                    relation="related_to",
                    status=status,
                    reviewed_at=datetime.now(UTC) if status != "pending" else None,
                    reviewed_by={} if status != "pending" else None,
                    explanation="A suggested association",
                    evidence_url="https://example.com/evidence",
                    evidence_title="Documentation",
                    evidence_quote="Quoted evidence",
                    topic_snapshot={},
                    related_topic_snapshot={},
                    created_by={},
                )
            )
        return {
            **{name: f"topic:{topic.id}" for name, topic in topics.items()},
            "article": f"article:{article.id}",
            "draft": f"article:{draft.id}",
            "tag": f"tag:{tag.id}",
            "source": f"source:{source.id}",
        }


def get(client, route="graph", **params):
    response = client.get(f"{BASE}/{route}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_empty_catalog(admin_client):
    graph = get(admin_client)
    assert graph["nodes"] == graph["edges"] == []
    assert graph["catalog_counts"] == {}
    assert not graph["truncated"]


def test_refresh_uses_a_consistent_snapshot_during_concurrent_changes(
    admin_client, catalog, database, monkeypatch
):
    original = knowledge.load_nodes
    changed = False

    def change_during_read(session, projection, identifiers):
        nonlocal changed
        if identifiers and not changed:
            changed = True
            with database.begin() as writer:
                topic = writer.get(Topic, uuid.UUID(catalog["JavaScript"].split(":")[1]))
                topic.status = "rejected"
        return original(session, projection, identifiers)

    monkeypatch.setattr(knowledge, "load_nodes", change_during_read)
    result = get(admin_client)
    ids = {node["id"] for node in result["nodes"]}
    assert catalog["JavaScript"] in ids
    assert all(edge["source"] in ids and edge["target"] in ids for edge in result["edges"])
    following = get(admin_client)
    assert catalog["JavaScript"] not in {node["id"] for node in following["nodes"]}
    assert following["edges"] == []


def test_overview_active_topics_and_actual_edges_only(admin_client, catalog):
    graph = get(admin_client)
    assert {n["label"] for n in graph["nodes"]} == {"React", "JavaScript", "Vue", "Isolated"}
    assert len(graph["edges"]) == 2
    assert graph["catalog_counts"] == {"topic": 4}
    assert not graph["truncated"]
    assert all(e["kind"] == "uses_language" and e["directed"] for e in graph["edges"])


def test_focus_depth_expansion_and_cross_links(admin_client, catalog):
    first = get(admin_client, focus=catalog["React"])
    assert {n["id"] for n in first["nodes"]} == {catalog["React"], catalog["JavaScript"]}
    for params in [{"depth": 2}, {"expand": [catalog["JavaScript"]]}]:
        graph = get(admin_client, focus=catalog["React"], **params)
        assert {n["id"] for n in graph["nodes"]} == {
            catalog[k] for k in ["React", "JavaScript", "Vue"]
        }
        assert len(graph["edges"]) == 2


def test_context_layers_preserve_meaning_and_deduplicate_origins(admin_client, catalog):
    graph = get(admin_client, layers=["article", "tag", "source"])
    assert graph["catalog_counts"] == {"topic": 4, "article": 2, "tag": 1, "source": 1}
    assert len([e for e in graph["edges"] if e["kind"] == "provided"]) == 1
    tagged = next(e for e in graph["edges"] if e["kind"] == "tagged_with")
    assert tagged["origin"] == "source"
    assert tagged["status"] == "saved"
    comparison = next(e for e in graph["edges"] if e["role"] == "comparison")
    assert comparison["origin"] == "manual"
    assert comparison["evidence"] == "Compares Vue"
    assert comparison["relevance"] == 0.4
    assert not any("content" in n for n in graph["nodes"])
    published = get(admin_client, layers=["article"], published_only=True)
    assert published["catalog_counts"]["article"] == 1
    assert catalog["draft"] not in {n["id"] for n in published["nodes"]}


def test_pending_overlay_is_explicit_and_never_history(admin_client, catalog, database):
    graph = get(admin_client, include_pending=True)
    pending = [e for e in graph["edges"] if e["status"] == "pending"]
    assert len(pending) == 1
    assert not pending[0]["directed"]
    assert "Quoted evidence" in pending[0]["evidence"]
    with database.begin() as session:
        session.add(
            TopicRelation(
                topic_id=uuid.UUID(catalog["Vue"].split(":")[1]),
                related_topic_id=uuid.UUID(catalog["React"].split(":")[1]),
                relation="related_to",
            )
        )
    assert not [
        e for e in get(admin_client, include_pending=True)["edges"] if e["status"] == "pending"
    ]


def test_search_uses_shared_normalization_and_keeps_alias_ambiguity(admin_client, catalog):
    for query in ["testing: where", "testing where", "testing — where", "testing / where"]:
        found = get(admin_client, "search", q=query, layers=["article"])
        assert found["total"] == 1
        assert found["items"][0]["id"] == catalog["article"]
    assert get(admin_client, "search", q="shared acronym")["total"] == 2
    assert get(admin_client, "search", q="Proposed")["total"] == 0


def test_connection_path_hops_direction_and_pending(admin_client, catalog):
    params = {"from_node": catalog["React"], "to_node": catalog["Vue"]}
    result = get(admin_client, "path", **params)
    assert result["found"] and len(result["edges"]) == 2
    assert not result["truncated"]
    assert not get(admin_client, "path", **params, max_hops=1)["found"]
    assert not get(admin_client, "path", **params, direction="outgoing")["found"]
    pending = get(admin_client, "path", **params, include_pending=True, direction="outgoing")
    assert pending["found"] and len(pending["edges"]) == 1
    assert pending["edges"][0]["status"] == "pending"
    assert get(admin_client, "path", from_node=catalog["React"], to_node=catalog["React"])["found"]


def test_paths_use_entire_filtered_catalog_and_can_follow_context(admin_client, catalog):
    result = get(
        admin_client,
        "path",
        from_node=catalog["source"],
        to_node=catalog["React"],
        layers=["source", "article"],
        direction="outgoing",
    )
    assert result["found"]
    assert [e["kind"] for e in result["edges"]] == ["provided", "classified_as"]
    assert not get(
        admin_client,
        "path",
        from_node=catalog["React"],
        to_node=catalog["Vue"],
        relation="depends_on",
    )["found"]


def test_limits_are_explicit_and_leave_no_dangling_edges(admin_client, catalog, database):
    with database.begin() as session:
        for i in range(45):
            topic = Topic(
                name=f"Neighbour {i:03}", slug=f"neighbour-{i}", kind="technology", status="active"
            )
            session.add(topic)
            session.flush()
            session.add(
                TopicRelation(
                    topic_id=uuid.UUID(catalog["React"].split(":")[1]),
                    related_topic_id=topic.id,
                    relation="related_to",
                )
            )
    for params in [{}, {"focus": catalog["React"], "depth": 2}]:
        result = get(admin_client, limit=25, **params)
        assert result["truncated"]
        assert len(result["nodes"]) == 25
        ids = {n["id"] for n in result["nodes"]}
        assert all(e["source"] in ids and e["target"] in ids for e in result["edges"])


def test_exhausted_path_budget_is_not_reported_as_definite_no_path(
    admin_client, catalog, monkeypatch
):
    monkeypatch.setattr(knowledge, "PATH_EDGE_BUDGET", 1)
    result = get(admin_client, "path", from_node=catalog["React"], to_node=catalog["Vue"])
    assert result["truncated"] and not result["found"]


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 301},
        {"depth": 9},
        {"focus": "topic:invalid"},
        {"layers": ["unknown"]},
        {"expand": [f"topic:{uuid.uuid4()}"] * 21},
    ],
)
def test_invalid_requests_rejected(admin_client, params):
    assert admin_client.get(f"{BASE}/graph", params=params).status_code == 422


def test_missing_and_filtered_focus_are_clear(admin_client, catalog):
    for focus in [catalog["Proposed"], catalog["article"], f"topic:{uuid.uuid4()}"]:
        response = admin_client.get(f"{BASE}/graph", params={"focus": focus})
        assert response.status_code == 404
        assert "selected layers" in response.json()["detail"]
