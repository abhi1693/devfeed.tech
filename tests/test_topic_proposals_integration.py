import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from devfeed_core.models import (
    Article,
    ArticleTag,
    ArticleTopic,
    Tag,
    Topic,
    TopicProposal,
    utcnow,
)
from devfeed_core.urls import fingerprint
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


def topic(client, slug="engineering", **values):
    response = client.post(
        "/v1/admin/topics",
        json={"name": slug.title(), "slug": slug, "kind": "discipline", **values},
    )
    assert response.status_code == 201, response.text
    return response.json()


def preview(client, rows):
    body = {
        "format": "json",
        "content": json.dumps([{"kind": "discipline", **row} for row in rows]),
        "source_name": "reviewed-catalog.json",
    }
    response = client.post("/v1/admin/topic-imports/preview", json=body)
    assert response.status_code == 200, response.text
    return body, response.json()


def submit(client, rows):
    body, result = preview(client, rows)
    assert result["can_submit"], result
    response = client.post(
        "/v1/admin/topic-imports", json={**body, "preview_token": result["preview_token"]}
    )
    assert response.status_code == 201, response.text
    return response.json()


def approve(client, proposal, **changes):
    return client.post(
        f"/v1/admin/topic-proposals/{proposal['id']}/review",
        json={
            "decision": "approved",
            "topic": {**proposal["proposed"], **changes},
            "note": "Reviewed scope and matching keywords",
        },
    )


def test_imports_remain_outside_live_taxonomy_until_individual_review(
    admin_client, client, database
):
    rows = [
        {
            "name": "Infrastructure",
            "slug": "infrastructure",
            "description": "Operating systems and infrastructure.",
        },
        {"name": "Unwanted", "slug": "unwanted"},
    ]
    body, result = preview(admin_client, rows)
    assert result["can_submit"]
    with database() as session:
        assert session.scalar(select(func.count()).select_from(TopicProposal)) == 0
    proposals = submit(admin_client, rows)
    assert all(p["status"] == "pending" for p in proposals)
    assert admin_client.get("/v1/admin/topic-proposals").json()["total"] == 2
    assert client.get("/v1/topics").json() == []
    result = approve(admin_client, proposals[0], name="Platform infrastructure").json()
    assert result["status"] == "approved"
    assert result["proposed"]["name"] == "Infrastructure"
    assert result["applied"]["name"] == "Platform infrastructure"
    assert result["reviewed_by"]["subject"] == "integration-admin"
    assert result["reviewed_by"]["issuer"] == "https://identity.example"
    rejected = admin_client.post(
        f"/v1/admin/topic-proposals/{proposals[1]['id']}/review",
        json={"decision": "rejected", "note": "Outside editorial scope"},
    )
    assert rejected.status_code == 200
    assert [c["slug"] for c in client.get("/v1/topics").json()] == ["infrastructure"]
    assert approve(admin_client, proposals[0]).status_code == 409


def test_update_preserves_omitted_fields_and_rejects_stale_approval(admin_client):
    existing = topic(
        admin_client,
        "backend",
        description="Human description",
        keywords=["api"],
    )
    proposal = submit(admin_client, [{"name": "Server engineering", "slug": "backend"}])[0]
    assert proposal["proposed"]["description"] == "Human description"
    assert proposal["proposed"]["keywords"] == ["api"]
    assert admin_client.get(f"/v1/admin/topics/{existing['id']}").json()["name"] == "Backend"
    admin_client.put(
        f"/v1/admin/topics/{existing['id']}",
        json={**proposal["proposed"], "description": "Newer human description"},
    ).raise_for_status()
    assert approve(admin_client, proposal).status_code == 409
    assert (
        admin_client.get(f"/v1/admin/topic-proposals/{proposal['id']}").json()["status"]
        == "pending"
    )
    assert (
        admin_client.get(f"/v1/admin/topics/{existing['id']}").json()["description"]
        == "Newer human description"
    )


@pytest.mark.parametrize(
    "rows",
    [
        [{"name": "A", "slug": "a"}, {"name": "B", "slug": "a"}],
        [
            {"name": "A", "slug": "a", "parent_slug": "b"},
            {"name": "B", "slug": "b", "parent_slug": "a"},
        ],
        [{"name": "A", "slug": "a", "parent_slug": "missing"}],
        [{"name": "A", "slug": "a", "status": "approved"}],
        [{"name": "A", "slug": "a", "topic_slug": "missing"}],
    ],
)
def test_invalid_imports_do_not_partially_write(admin_client, database, rows):
    body, result = preview(admin_client, rows)
    assert not result["can_submit"]
    assert any(row["issues"] for row in result["rows"])
    response = admin_client.post(
        "/v1/admin/topic-imports", json={**body, "preview_token": result["preview_token"]}
    )
    assert response.status_code == 409
    with database() as session:
        assert session.scalar(select(func.count()).select_from(TopicProposal)) == 0
        assert session.scalar(select(func.count()).select_from(Topic)) == 0


def test_repeated_import_and_changed_preview_cannot_duplicate_or_retarget(admin_client):
    rows = [{"name": "Engineering", "slug": "engineering"}]
    body, result = preview(admin_client, rows)
    topic(admin_client)
    assert (
        admin_client.post(
            "/v1/admin/topic-imports", json={**body, "preview_token": result["preview_token"]}
        ).status_code
        == 409
    )
    proposal = submit(admin_client, [{"name": "New name", "slug": "engineering"}])[0]
    _, duplicate = preview(admin_client, [{"name": "Another", "slug": "engineering"}])
    assert not duplicate["can_submit"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: approve(admin_client, proposal).status_code, range(2)))
    assert sorted(outcomes) == [200, 409]


def test_deleted_target_is_not_recreated_by_a_pending_update(admin_client):
    existing = topic(admin_client)
    proposal = submit(admin_client, [{"name": "New name", "slug": "engineering"}])[0]
    admin_client.delete(f"/v1/admin/topics/{existing['id']}").raise_for_status()
    assert approve(admin_client, proposal).status_code == 409
    assert admin_client.get("/v1/admin/topics").json()["total"] == 0


def seed_evidence(database, topic_id):
    with database.begin() as session:
        supported = Tag(name="PostgreSQL", slug="postgresql", aliases=["postgres"])
        isolated = Tag(name="Random label", slug="random-label", aliases=[])
        unreviewed = Tag(name="Unreviewed label", slug="unreviewed-label", aliases=[])
        session.add_all([supported, isolated, unreviewed])
        session.flush()
        for index in range(4):
            url = f"https://example.com/evidence-{index}"
            article = Article(
                title=f"Database engineering {index}",
                summary="",
                canonical_url=url,
                url_hash=fingerprint(url),
                review_status="approved" if index < 2 else "pending",
                publication_status="published" if index < 2 else "unpublished",
                published_to_feed_at=utcnow() if index < 2 else None,
            )
            session.add(article)
            session.flush()
            session.add(
                ArticleTopic(
                    article_id=article.id,
                    topic_id=uuid.UUID(topic_id),
                    role="supporting",
                    relevance=1,
                    evidence="Reviewed topic assignment",
                    origin="manual",
                )
            )
            session.add(
                ArticleTag(
                    article_id=article.id, tag_id=supported.id if index < 2 else unreviewed.id
                )
            )
            if index == 0:
                session.add(ArticleTag(article_id=article.id, tag_id=isolated.id))


def test_enrichment_uses_reviewed_evidence_and_requires_approval(admin_client, database):
    existing = topic(admin_client, keywords=["database"])
    seed_evidence(database, existing["id"])
    path = f"/v1/admin/topics/{existing['id']}/enrichment"
    preview = admin_client.post(path + "/preview").json()
    assert preview["articles_examined"] == 2
    assert {s["keyword"] for s in preview["suggestions"]} == {"PostgreSQL", "postgres"}
    assert all(s["article_count"] == 2 and len(s["articles"]) == 2 for s in preview["suggestions"])
    assert (
        admin_client.post(
            path, json={"preview_token": preview["preview_token"], "keywords": ["random"]}
        ).status_code
        == 409
    )
    response = admin_client.post(
        path, json={"preview_token": preview["preview_token"], "keywords": ["postgres"]}
    )
    assert response.status_code == 201, response.text
    proposal = response.json()
    assert proposal["origin"] == "article_enrichment"
    assert admin_client.get(f"/v1/admin/topics/{existing['id']}").json()["keywords"] == ["database"]
    approve(admin_client, proposal).raise_for_status()
    assert admin_client.get(f"/v1/admin/topics/{existing['id']}").json()["keywords"] == [
        "database",
        "postgres",
    ]


def test_enrichment_rechecks_changed_evidence(admin_client, database):
    existing = topic(admin_client)
    seed_evidence(database, existing["id"])
    path = f"/v1/admin/topics/{existing['id']}/enrichment"
    preview = admin_client.post(path + "/preview").json()
    with database.begin() as session:
        article = session.scalar(
            select(Article).where(Article.review_status == "approved").limit(1)
        )
        article.review_status = "rejected"
        article.publication_status = "unpublished"
    assert (
        admin_client.post(
            path, json={"preview_token": preview["preview_token"], "keywords": ["postgres"]}
        ).status_code
        == 409
    )


def test_analysis_discovery_stays_pending_and_rejection_prevents_rediscovery(
    admin_client, database
):
    from devfeed_core.models import ArticleAnalysisJob

    with database.begin() as session:
        article = Article(
            title="Python runtime",
            summary="A Python runtime guide",
            canonical_url="https://example.com/python",
            url_hash=fingerprint("python"),
        )
        session.add(article)
        session.flush()
        job = ArticleAnalysisJob(
            article_id=article.id,
            status="succeeded",
            outcome="applied",
            input_snapshot={},
            input_hash="a" * 64,
            editorial_revision=0,
            prompt_version="old-version",
            model="test-model",
            result={
                "proposed_topics": [
                    {
                        "name": "Python",
                        "slug": "python",
                        "kind": "language",
                        "evidence": "Python runtime",
                    }
                ]
            },
        )
        session.add(job)
    response = admin_client.post("/v1/admin/topic-discovery")
    assert response.status_code == 201, response.text
    proposal = response.json()[0]
    assert proposal["status"] == "pending" and proposal["origin"] == "ai_analysis"
    assert proposal["evidence"][0]["quote"] == "Python runtime"
    assert admin_client.get("/v1/admin/topics").json()["total"] == 0
    assert admin_client.post("/v1/admin/topic-discovery").json() == []
    admin_client.post(
        f"/v1/admin/topic-proposals/{proposal['id']}/review", json={"decision": "rejected"}
    ).raise_for_status()
    assert admin_client.post("/v1/admin/topic-discovery").json() == []
    assert admin_client.get("/v1/admin/topics").json()["total"] == 0


def test_topic_aliases_are_identity_and_keywords_are_not(admin_client):
    existing = topic(admin_client, "postgresql", aliases=["postgres"], keywords=["storage"])
    _, collision = preview(admin_client, [{"name": "Postgres", "slug": "postgres"}])
    assert not collision["can_submit"]
    proposal = submit(admin_client, [{"name": "Storage", "slug": "storage"}])[0]
    approve(admin_client, proposal).raise_for_status()
    stale = submit(admin_client, [{"name": "PostgreSQL database", "slug": "postgresql"}])[0]
    current = {**stale["proposed"], "name": "PostgreSQL updated"}
    admin_client.put(f"/v1/admin/topics/{existing['id']}", json=current).raise_for_status()
    assert approve(admin_client, stale).status_code == 409


def test_github_pull_batches_topics_for_review_and_skips_existing_identities(
    admin_client, database, monkeypatch
):
    from devfeed_core import github_topics

    sha = "a" * 40
    rows = [
        {
            "slug": f"subject-{i}",
            "fields": {"name": f"Subject {i}", "slug": f"subject-{i}", "kind": "technology"},
        }
        for i in range(105)
    ]
    monkeypatch.setattr(
        github_topics, "read_document", lambda *a: json.dumps({"object": {"sha": sha}}).encode()
    )
    monkeypatch.setattr(github_topics, "repository_topics", lambda revision: rows)
    topic(admin_client, "subject-0")
    first = admin_client.post("/v1/admin/topic-discovery/github", json={})
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["total"] == 105 and result["created"] == 99 and result["skipped"] == 1
    assert result["next_offset"] == 100
    second = admin_client.post(
        "/v1/admin/topic-discovery/github", json={"revision": sha, "offset": 100}
    ).json()
    assert second["created"] == 5 and second["next_offset"] is None
    assert admin_client.get("/v1/admin/topics").json()["total"] == 1
    with database() as session:
        proposals = session.scalars(select(TopicProposal)).all()
        assert len(proposals) == 104 and all(p.status == "pending" for p in proposals)
        proposal = proposals[0]
        assert (
            proposal.evidence[0]["source_url"]
            == f"https://github.com/github/explore/blob/{sha}/topics/{proposal.slug}/index.md"
        )
    again = admin_client.post("/v1/admin/topic-discovery/github", json={}).json()
    assert again["created"] == 0 and again["skipped"] == 100


def test_github_pull_keeps_valid_rows_and_reports_bad_metadata(admin_client, monkeypatch):
    from devfeed_core import github_topics

    sha = "a" * 40
    monkeypatch.setattr(
        github_topics,
        "repository_topics",
        lambda revision: [
            {
                "slug": "python",
                "fields": {"name": "Python", "slug": "python", "kind": "technology"},
            },
            {"slug": "invalid", "issue": "GitHub metadata needs manual correction"},
        ],
    )
    result = admin_client.post("/v1/admin/topic-discovery/github", json={"revision": sha}).json()
    assert result["created"] == 1 and len(result["issues"]) == 1
    assert admin_client.get("/v1/admin/topics").json()["total"] == 0
    proposal = admin_client.get("/v1/admin/topic-proposals").json()["items"][0]
    admin_client.post(
        f"/v1/admin/topic-proposals/{proposal['id']}/review", json={"decision": "rejected"}
    ).raise_for_status()
    assert (
        admin_client.post("/v1/admin/topic-discovery/github", json={"revision": sha}).json()[
            "created"
        ]
        == 0
    )
