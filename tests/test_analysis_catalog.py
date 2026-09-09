"""Large supervised catalogs stay usable without overflowing inference input."""

import json
import uuid
from types import SimpleNamespace

import pytest
from devfeed_aggregator import analysis_tasks
from devfeed_core import analysis
from devfeed_core.models import Article, ArticleAnalysisJob, ArticleOrigin, Source, Tag, Topic
from devfeed_core.taxonomy import classify
from devfeed_core.urls import fingerprint


def entry(name, **values):
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "slug": name.lower(),
        "aliases": [],
        "keywords": [],
        **values,
    }


def test_candidates_rank_names_aliases_keywords_and_word_boundaries():
    taxonomy = {"topics": [entry(f"A filler {i}") for i in range(600)], "tags": []}
    relevant = [
        entry("Zulu", aliases=["autonomous-assistant"]),
        entry("Widgets", keywords=["agent tools"]),
        entry("C++"),
    ]
    unrelated = entry("Go")
    taxonomy["topics"] += [*relevant, unrelated]
    snapshot = {
        "title": "Autonomous assistant agent tools in C++",
        "text": "Google's engineering guide.",
    }
    candidates = analysis.analysis_candidates(taxonomy, snapshot)
    selected = {item["id"] for item in candidates["topics"]}
    assert len(selected) == 500
    assert all(item["id"] in selected for item in relevant)
    assert unrelated["id"] not in selected  # Go must not match Google.
    assert (
        analysis.analysis_candidates(
            {"topics": list(reversed(taxonomy["topics"])), "tags": []}, snapshot
        )
        == candidates
    )


def test_shared_alias_keeps_both_ai_candidates_without_fallback_assignment():
    shared = [
        entry("Continuous Delivery", aliases=["CD"]),
        entry("Continuous Deployment", aliases=["CD"]),
    ]
    taxonomy = {"topics": [entry(f"A filler {i}") for i in range(600)] + shared, "tags": []}
    candidates = analysis.analysis_candidates(taxonomy, {"title": "CD workflows"})
    assert {item["id"] for item in shared} <= {item["id"] for item in candidates["topics"]}
    topics = [Topic(**{**item, "status": "active"}) for item in shared]
    assert classify("CD workflows", "", [], topics) == []


def test_prompt_budget_counts_unicode_article_and_long_aliases():
    taxonomy = {
        field: [entry(f"Subject {i}", aliases=["編程" * 30] * 20) for i in range(650)]
        for field in ("topics", "tags")
    }
    snapshot = {"title": "Subject 649", "text": "工程 " * 12_000}
    candidates = analysis.analysis_candidates(taxonomy, snapshot)
    assert len(analysis.analysis_prompt(snapshot, candidates).encode()) <= 240_000
    assert candidates["topics"] and candidates["tags"]
    assert sum(map(len, candidates.values())) < 1000


@pytest.mark.integration
def test_large_catalog_runs_analysis_and_manual_classification(database, monkeypatch):
    with database.begin() as session:
        topics = [
            Topic(
                name=f"Catalog topic {i:04}", slug=f"topic-{i}", kind="technology", status="active"
            )
            for i in range(1300)
        ]
        relevant = Topic(
            name="AI agents",
            slug="ai-agents",
            kind="technology",
            status="active",
            aliases=["Autonomous assistants"],
        )
        hidden = Topic(
            name="Pending topic", slug="pending-topic", kind="technology", status="proposed"
        )
        source = Source(
            name="Publisher",
            feed_url="https://example.com/feed",
            source_type="publisher",
            approval_status="approved",
            enabled=True,
        )
        article = Article(
            title="Building autonomous assistants",
            canonical_url="https://example.com/guide",
            url_hash=fingerprint("https://example.com/guide"),
            summary="Autonomous assistants use tools to solve engineering tasks. " * 5,
        )
        session.add_all(
            [
                *topics,
                relevant,
                hidden,
                source,
                article,
                *[Tag(name=f"Tag {i}", slug=f"tag-{i}") for i in range(520)],
            ]
        )
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=article.id,
                source_id=source.id,
                entry_key="guide",
                original_url=article.canonical_url,
            )
        )
        session.flush()
        article_id, relevant_id = article.id, relevant.id
        job_id = analysis.request_analysis(session, article_id).id
        full = analysis.catalog(session)
        assert len(full["topics"]) == 1301 and len(full["tags"]) == 520
        assert str(hidden.id) not in {item["id"] for item in full["topics"]}

    def complete(prompt, schema):
        assert len(prompt.encode()) <= 240_000
        data = json.loads(prompt[prompt.index('{"article":') :])
        assert str(relevant_id) in {item["id"] for item in data["catalog"]["topics"]}
        return {
            "outcome": "ready",
            "developer_relevance": "relevant",
            "language": "en",
            "content_type": "tutorial",
            "content_format": "article",
            "ai_summary": "A guide to building autonomous assistants.",
            "ai_description": None,
            "topics": [
                {
                    "topic_id": str(relevant_id),
                    "role": "primary",
                    "relevance": 0.9,
                    "evidence": "Autonomous assistants",
                }
            ],
            "tags": [],
            "reasons": [],
        }

    monkeypatch.setattr(analysis_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(
        analysis_tasks,
        "get_settings",
        lambda: SimpleNamespace(ai_enabled=True, codex_model="fixture-model"),
    )
    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete))
    analysis_tasks._analyze(job_id)
    with database.begin() as session:
        job = session.get(ArticleAnalysisJob, job_id)
        assert job.status == "succeeded" and job.outcome == "applied", job.error
        assert len(job.catalog_snapshot["topics"]) == 500
        article = session.get(Article, article_id)
        assert article.publication_status == "unpublished" and article.review_status == "pending"
        body = analysis.ManualClassification(
            developer_relevance="relevant",
            language="en",
            content_type="tutorial",
            content_format="article",
            topics=[
                {
                    "topic_id": relevant_id,
                    "role": "primary",
                    "relevance": 1,
                    "evidence": "Autonomous assistants",
                }
            ],
            tags=[],
        )
        analysis.classify_manually(session, article_id, body)
        assert article.classification_provenance["origin"] == "manual"
