"""The operations page lists both AI pipelines with one ordering and page count."""

import uuid
from datetime import timedelta

import pytest
from devfeed_core.models import Article, ArticleAnalysisJob, TopicAnalysisJob, TopicProposal, utcnow
from devfeed_core.urls import fingerprint

pytestmark = pytest.mark.integration


@pytest.fixture
def analysis_runs(database):
    created = utcnow()
    with database.begin() as session:
        articles = []
        proposals = []
        for index in range(2):
            url = f"https://example.com/analysis-{index}"
            article = Article(
                title="Shared phrase guide" if index == 0 else "Active article",
                canonical_url=url,
                url_hash=fingerprint(url),
                summary="",
            )
            proposal = TopicProposal(
                batch_id=uuid.uuid4(),
                slug=f"topic-{index}",
                action="create",
                origin="import",
                source_name="GitHub curated topics",
                status="pending",
                created_by={},
                proposed={
                    "name": "Shared phrase topic" if index == 0 else "100%_ topic",
                    "slug": f"topic-{index}",
                    "kind": "technology",
                },
            )
            session.add_all([article, proposal])
            session.flush()
            articles.append(article.id)
            proposals.append(proposal.id)
        for index, status in enumerate(["failed", "running"]):
            session.add(
                ArticleAnalysisJob(
                    id=uuid.UUID(int=index + 1),
                    article_id=articles[index],
                    status=status,
                    created_at=created - timedelta(minutes=3 - index * 2),
                    input_snapshot={"private": "internal-snapshot-marker"},
                    catalog_snapshot={"private": "internal-snapshot-marker"},
                )
            )
        for index, status in enumerate(["queued", "succeeded"]):
            session.add(
                TopicAnalysisJob(
                    id=uuid.UUID(int=1 if index == 0 else 3),
                    proposal_id=proposals[index],
                    status=status,
                    created_at=created - timedelta(minutes=2 - index * 2),
                    input_hash="a" * 64,
                    input_snapshot={"private": "internal-snapshot-marker"},
                    requested_by={},
                    prompt_version="test",
                )
            )
    return articles, proposals


def listing(client, **params):
    response = client.get("/v1/admin/jobs/ai-analysis", params=params)
    assert response.status_code == 200, response.text
    assert "internal-snapshot-marker" not in response.text
    return response.json()


def test_unified_count_order_and_pagination(admin_client, analysis_runs):
    page = listing(admin_client, limit=2, offset=1)
    assert page["total"] == 4 and page["offset"] == 1 and page["limit"] == 2
    assert [(row["kind"], row["status"]) for row in page["items"]] == [
        ("analysis", "running"),
        ("topic-analysis", "queued"),
    ]
    assert [row["target_name"] for row in page["items"]] == [
        "Active article",
        "Shared phrase topic",
    ]
    full = listing(admin_client)["items"]
    assert len({(row["kind"], row["id"]) for row in full}) == 4
    assert [
        (row["kind"], row["id"]) for row in listing(admin_client, sort="created_at")["items"]
    ] == [(row["kind"], row["id"]) for row in reversed(full)]


def test_type_status_and_subject_filters_preserve_related_article_scope(
    admin_client, analysis_runs
):
    articles, proposals = analysis_runs
    assert listing(admin_client, analysis_type="articles")["total"] == 2
    assert listing(admin_client, analysis_type="topics")["total"] == 2
    assert listing(admin_client, analysis_type="topics", status="running")["total"] == 0
    assert listing(admin_client, analysis_type="topics", status="queued")["total"] == 1
    assert listing(admin_client, article_id=str(articles[0]))["items"][0]["kind"] == "analysis"
    assert (
        listing(admin_client, proposal_id=str(proposals[0]))["items"][0]["kind"] == "topic-analysis"
    )
    assert listing(admin_client, analysis_type="topics", article_id=str(articles[0]))["total"] == 0
    assert admin_client.get("/v1/admin/jobs/analysis").json()["total"] == 2
    assert admin_client.get("/v1/admin/jobs/topic-analysis").json()["total"] == 2


def test_search_by_subject_or_run_id_and_escape_wildcards(admin_client, analysis_runs):
    assert listing(admin_client, q="SHARED PHRASE")["total"] == 2
    assert listing(admin_client, q="%_")["total"] == 1
    assert listing(admin_client, q=str(uuid.UUID(int=1)))["total"] == 2
    assert listing(admin_client, q="does not exist")["total"] == 0


def test_each_pipeline_keeps_its_own_details_and_logs(admin_client, analysis_runs):
    identifier = str(uuid.UUID(int=1))
    for kind, status in [("analysis", "failed"), ("topic-analysis", "queued")]:
        result = admin_client.get(f"/v1/admin/jobs/{kind}/{identifier}")
        assert result.status_code == 200
        assert result.json()["kind"] == kind and result.json()["status"] == status
        assert "internal-snapshot-marker" not in result.text
        logs = admin_client.get(f"/v1/admin/jobs/{kind}/{identifier}/logs")
        assert logs.status_code == 200 and logs.json()["job_status"] == status


@pytest.mark.parametrize(
    "params", [{"analysis_type": "unknown"}, {"sort": "name"}, {"status": "unknown"}]
)
def test_invalid_filters_are_rejected(admin_client, params):
    assert admin_client.get("/v1/admin/jobs/ai-analysis", params=params).status_code == 422
