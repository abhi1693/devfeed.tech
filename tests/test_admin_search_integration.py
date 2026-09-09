"""Admin search normalizes queries and stored text before filtering and paging."""

import uuid

import pytest
from devfeed_admin_api.search import text_search
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    Source,
    Tag,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    TopicRelation,
    TopicRelationProposal,
)
from devfeed_core.topic_relationships import topic_snapshot
from devfeed_core.urls import fingerprint
from sqlalchemy import String, column, select, values

pytestmark = pytest.mark.integration

PHRASES = [
    "testing where",
    "Testing: where",
    "testing,where",
    "testing.where",
    "testing; where",
    "testing!? where",
    "testing-where",
    "testing–where",
    "testing—where",
    "testing_where",
    "testing/where",
    "testing\\where",
    "testing (where)",
    "[testing] {where}",
    '"testing where"',
    "'testing' 'where'",
    "‘testing’ “where”",
    "testing…where",
    "testing & where",
    "testing|where",
    "testing\t\n  where",
    "testing\u00a0where",
]


def test_punctuation_and_spacing_variants_match_in_both_directions(database):
    rows = (
        values(column("title", String))
        .data([(phrase,) for phrase in [*PHRASES, "testing elsewhere", "where testing", None]])
        .cte("documents")
    )
    with database() as session:
        for query in PHRASES:
            matches = session.scalars(select(rows.c.title).where(text_search(query, rows.c.title)))
            assert set(matches) == set(PHRASES), query


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("CAFÉ: DÉJÀ", {"Café — déjà"}),
        ("東京,検索", {"東京: 検索"}),
        ("C++: guide", {"C++ guide"}),
        ("C# guide", {"C# guide"}),
        ("C guide", {"C guide"}),
        ("%_", {"100%_ complete"}),
        ("///", {"///"}),
        ("' OR 1=1 --", set()),
    ],
)
def test_unicode_technology_symbols_and_literal_only_queries(database, query, expected):
    rows = (
        values(column("title", String))
        .data(
            [
                (value,)
                for value in [
                    "Café — déjà",
                    "東京: 検索",
                    "C++ guide",
                    "C# guide",
                    "C guide",
                    "100%_ complete",
                    "100 percent complete",
                    "///",
                    "Unrelated",
                    None,
                ]
            ]
        )
        .cte("documents")
    )
    with database() as session:
        matches = session.scalars(select(rows.c.title).where(text_search(query, rows.c.title)))
        assert set(matches) == expected


@pytest.fixture
def search_catalog(database):
    with database.begin() as session:
        target = Topic(name="Target", slug="target", kind="technology", status="active")
        session.add(target)
        session.flush()
        for index, name in enumerate(["Testing: where", "Testing where", "Unrelated"]):
            url = f"https://example.com/{index}"
            topic = Topic(
                name=name, slug=f"topic-{index}", kind="technology", status="active", aliases=["CD"]
            )
            article = Article(title=name, canonical_url=url, url_hash=fingerprint(url))
            proposal = TopicProposal(
                batch_id=uuid.uuid4(),
                slug=f"proposal-{index}",
                action="create",
                origin="import",
                source_name="Fixture",
                created_by={},
                proposed={
                    "name": name,
                    "slug": f"proposal-{index}",
                    "kind": "technology",
                    "aliases": ["CD"],
                },
            )
            session.add_all(
                [
                    topic,
                    article,
                    proposal,
                    Source(name=name, feed_url=url + "/feed", source_type="publisher"),
                    Tag(name=name, slug=f"tag-{index}"),
                ]
            )
            session.flush()
            job = TopicAnalysisJob(
                proposal_id=proposal.id,
                input_hash="a" * 64,
                input_snapshot={},
                requested_by={},
                prompt_version="test",
            )
            session.add_all([job, ArticleAnalysisJob(article_id=article.id)])
            session.flush()
            session.add_all(
                [
                    TopicRelation(
                        topic_id=topic.id, related_topic_id=target.id, relation="related_to"
                    ),
                    TopicRelationProposal(
                        job_id=job.id,
                        topic_id=topic.id,
                        related_topic_id=target.id,
                        relation="depends_on",
                        explanation="A documented dependency",
                        evidence_url=url,
                        evidence_title="Documentation",
                        evidence_quote="Evidence",
                        topic_snapshot=topic_snapshot(topic),
                        related_topic_snapshot=topic_snapshot(target),
                        created_by={},
                    ),
                ]
            )
        return str(target.id)


@pytest.mark.parametrize(
    ("resource", "total", "filters"),
    [
        ("articles", 2, {"publication_status": "unpublished"}),
        ("sources", 2, {"source_type": "publisher"}),
        ("topics", 2, {"status": "active"}),
        ("tags", 2, {}),
        ("topic-proposals", 2, {"status": "pending"}),
        ("topic-relations", 2, {}),
        ("topic-relationships", 4, {}),
        ("topic-relationship-proposals", 2, {"status": "pending"}),
        ("jobs/ai-analysis", 4, {"status": "queued"}),
    ],
)
def test_all_admin_lists_normalize_before_counting_and_paging(
    admin_client, search_catalog, resource, total, filters
):
    path = f"/v1/admin/{resource}"
    expected = None
    for query in ["testing where", "testing: where", "[TESTING]—where"]:
        response = admin_client.get(path, params={"q": query, **filters})
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["total"] == total
        if expected is None:
            expected = page["items"]
        assert page["items"] == expected
        for offset in range(total):
            response = admin_client.get(
                path, params={"q": query, "limit": 1, "offset": offset, **filters}
            )
            assert response.status_code == 200, response.text
            assert response.json()["total"] == total
            assert response.json()["items"] == expected[offset : offset + 1]


def test_exact_filters_still_apply_and_relationship_labels_accept_spaces(
    admin_client, search_catalog
):
    for path, filters in [
        ("sources", {"source_type": "aggregator"}),
        ("topics", {"status": "rejected"}),
        ("topic-proposals", {"source": "fixture"}),
        ("jobs/ai-analysis", {"status": "failed"}),
    ]:
        response = admin_client.get(f"/v1/admin/{path}", params={"q": "testing where", **filters})
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 0
    for path in ["topic-relations", "topic-relationships"]:
        response = admin_client.get(
            f"/v1/admin/{path}", params={"q": "related to", "topic_id": search_catalog}
        )
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 3


@pytest.mark.parametrize(
    ("resource", "count"),
    [
        ("topics", 3),
        ("topic-proposals", 3),
        ("topic-relations", 3),
        ("topic-relationships", 6),
        ("topic-relationship-proposals", 3),
    ],
)
def test_alias_search_finds_all_matching_topics_and_relationships(
    admin_client, search_catalog, resource, count
):
    response = admin_client.get(f"/v1/admin/{resource}", params={"q": "cd"})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == count
