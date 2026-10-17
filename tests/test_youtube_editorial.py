"""Video metadata is distinct from publisher articles and never proves playback."""

import json
import uuid
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest
from devfeed_aggregator import article_tasks
from devfeed_aggregator import source_relevance as source_worker
from devfeed_core import analysis, editorial, publication_policy
from devfeed_core.models import ArticleContent, ArticleEnrichmentJob, utcnow
from devfeed_core.source_relevance import video_sample
from devfeed_core.video_content import has_video_description, verified_video_metadata
from devfeed_core.youtube import YouTubeClient, YouTubeResource, YouTubeVideo
from test_analysis import inputs, result
from test_editorial import article as publisher_article
from test_publication_policy_unit import scenario as scenario

VIDEO = "abcdefghijk"
CHANNEL = "UC" + "a" * 22
DESCRIPTION = (
    "This technical talk explains PostgreSQL row locks and concurrent transactions. "
    "It compares transaction isolation levels and demonstrates how developers diagnose deadlocks."
)


def video(**changes):
    return YouTubeVideo(
        id=VIDEO,
        title="PostgreSQL locking and transaction isolation",
        description=DESCRIPTION,
        channel_id=CHANNEL,
        channel_title="Database engineering",
        published_at=utcnow(),
        language="en",
        duration_seconds=1200,
        **changes,
    )


def video_article(**changes):
    metadata = {
        **video().metadata,
        "language": "en",
    }
    return publisher_article(
        provider="youtube",
        provider_resource_id=VIDEO,
        canonical_url=f"https://www.youtube.com/watch?v={VIDEO}",
        title="PostgreSQL locking and transaction isolation",
        summary=DESCRIPTION,
        ai_summary=DESCRIPTION,
        content_format="video",
        video_metadata=metadata,
        classification_provenance={"developer_relevance": "relevant", "page_kind": "video"},
        **changes,
    )


def test_substantive_public_video_has_a_distinct_editorial_contract():
    current = video_article(review_status="approved")
    assert verified_video_metadata(current) and has_video_description(current)
    assert editorial.publication_blockers(current) == []
    # Existing articles keep their original contract and do not need provider metadata.
    assert editorial.publication_blockers(publisher_article(review_status="approved")) == []


@pytest.mark.parametrize(
    "change",
    [
        "article-kind",
        "article-format",
        "unavailable",
        "missing-retrieval",
        "title-only",
        "unknown-language",
    ],
)
def test_video_publication_keeps_purpose_availability_evidence_and_language_gates(change):
    current = video_article(review_status="approved")
    if change == "article-kind":
        current.classification_provenance["page_kind"] = "article"
    elif change == "article-format":
        current.content_format = "article"
    elif change == "unavailable":
        current.video_metadata["availability"] = "unavailable"
    elif change == "missing-retrieval":
        current.video_metadata.pop("retrieved_at")
    elif change == "title-only":
        current.video_metadata["description"] = current.title
    else:
        current.language = None
    assert editorial.publication_blockers(current)


def test_video_snapshot_hash_ignores_refresh_time_but_tracks_semantic_evidence():
    current = video_article()
    snapshot = analysis.source_snapshot(current, None)
    current.video_metadata["retrieved_at"] = "2026-10-18T13:00:00+00:00"
    assert analysis.source_snapshot(current, None) == snapshot
    for key, value in (
        ("availability", "unavailable"),
        ("description", "Changed source evidence"),
        ("language", "fr"),
    ):
        changed = video_article()
        changed.video_metadata[key] = value
        assert analysis.source_snapshot(changed, None) != snapshot


def test_video_snapshot_uses_supported_description_not_scraped_html_or_generated_prose():
    current = video_article()
    content = ArticleContent(
        method="body", text="Untrusted watch-page navigation and transcript guess"
    )
    snapshot = analysis.source_snapshot(current, content)
    assert snapshot["text"] == DESCRIPTION and snapshot["text_source"] == "youtube_metadata"
    assert "transcript guess" not in json.dumps(snapshot)


@pytest.mark.parametrize("wire", ["original", "compact", "passages"])
def test_video_policy_and_metadata_survive_all_inference_transports(wire):
    from devfeed_core.analysis_wire import compact_request

    snapshot = analysis.source_snapshot(video_article(), None)
    taxonomy = {"topics": [], "tags": []}
    prompt = (
        analysis.analysis_prompt(snapshot, taxonomy)
        if wire == "original"
        else compact_request(snapshot, taxonomy, evidence_refs=wire == "passages")[0]
    )
    assert "no transcript or playback was retrieved" in prompt
    assert "an approved or relevant channel does not make every upload relevant" in prompt
    assert "Sparse descriptions" in prompt
    assert json.loads(prompt.rsplit("\n", 1)[1])["article"]["provider"] == "youtube"


@pytest.mark.parametrize(
    "change",
    [
        "provider",
        "page-kind",
        "format",
        "empty-description",
        "unknown-language",
        "fabricated-quote",
    ],
)
def test_ready_video_cannot_bypass_verified_provider_description_and_language(change):
    current = video_article()
    value = result(page_kind="video", content_format="video", topics=[])
    if change == "provider":
        current.provider = None
    elif change == "page-kind":
        value.page_kind = "article"
    elif change == "format":
        value.content_format = "article"
    elif change == "empty-description":
        current.video_metadata["description"] = ""
    elif change == "unknown-language":
        current.video_metadata["language"] = None
    else:
        value.ai_title, value.title_evidence = (
            "Unsupported new headline",
            "A fabricated playback result",
        )
    with pytest.raises(ValueError):
        analysis.validate_evidence(
            value, analysis.source_snapshot(current, None), {"topics": [], "tags": []}
        )


def test_sparse_video_remains_pending_without_inventing_a_summary(monkeypatch):
    current, content, job, session, added = inputs()
    current.provider, current.provider_resource_id = "youtube", VIDEO
    current.canonical_url, current.content_format = video().canonical_url, "video"
    current.video_metadata = {**video().metadata, "description": "", "language": None}
    content.method, content.text = "youtube_metadata", ""
    snapshot = analysis.source_snapshot(current, content)
    job.input_hash = analysis.snapshot_hash(snapshot)
    value = result(
        outcome="insufficient_evidence",
        page_kind="uncertain",
        language=None,
        topics=[],
        ai_summary="Must be discarded",
    )
    monkeypatch.setattr(analysis, "approved_sources", lambda *args: [uuid.uuid4()])
    analysis.apply_analysis(session, current, job, value)
    assert current.review_status == "pending" and current.publication_status == "unpublished"
    assert current.ai_summary is None and not added
    assert job.outcome == "insufficient_evidence"


@pytest.mark.parametrize("state", ["ready", "unrelated", "unapproved", "inactive"])
def test_video_automatic_publication_retains_individual_relevance_and_origin_gates(scenario, state):
    _, current, job, session = scenario
    current.provider, current.provider_resource_id = "youtube", VIDEO
    current.canonical_url, current.content_format = video().canonical_url, "video"
    current.summary = DESCRIPTION
    current.ai_title = current.ai_description = None
    current.video_metadata = {**video().metadata, "language": "en"}
    current.classification_provenance["page_kind"] = "video"
    job.result["page_kind"] = "video"
    job.input_hash = analysis.snapshot_hash(analysis.source_snapshot(current, None))
    if state == "unrelated":
        current.classification_provenance["developer_relevance"] = "unrelated"
        job.result["developer_relevance"] = "unrelated"
    elif state == "unapproved":
        current.origins[0].source.approval_status = "pending"
    elif state == "inactive":
        current.origins[0].active = False
    decision = publication_policy.evaluate_publication(session, current, job)
    assert (decision["status"] == "would_publish") is (state == "ready")


def test_youtube_source_assessment_uses_bounded_api_videos_without_fetching_rss(monkeypatch):
    resource = YouTubeResource(
        "channel", CHANNEL, "UU" + "a" * 22, "https://www.youtube.com/channel/" + CHANNEL
    )
    entries = [replace(video(), id=f"video{i:06d}") for i in range(15)]
    calls = []
    client = NS(sample_resource=lambda source, limit: calls.append((source, limit)) or entries)
    monkeypatch.setattr(
        source_worker,
        "validate_feed",
        lambda *args, **kwargs: pytest.fail("RSS fetched for YouTube"),
    )

    def complete(prompt, schema):
        assert "YouTube metadata sample" in prompt
        return {
            "relevance": "relevant",
            "confidence": 0.95,
            "reason": "Grounded database engineering talks appear alongside unrelated uploads.",
            "entries": [
                {
                    "index": i,
                    "relevance": "relevant" if i == 0 else "uncertain",
                    "evidence": f"e{i}_0" if i == 0 else "none",
                }
                for i in range(10)
            ],
        }

    monkeypatch.setattr(source_worker, "CodexClient", lambda settings: NS(complete=complete))
    assessment = source_worker.assess_youtube_source(resource, client=client)
    assert calls == [(resource, 10)] and len(assessment["sample"]) == 10
    assert assessment["approval_supported"] is True
    assert assessment["rejection_supported"] is False
    assert [entry["relevance"] for entry in assessment["entries"]].count("uncertain") == 9
    assert len(video_sample([replace(video(), availability="unavailable")])) == 0


def test_single_video_submission_requires_explicit_source_review_without_approving_channel(
    monkeypatch,
):
    resource = YouTubeResource("video", VIDEO, None, video().canonical_url)
    monkeypatch.setattr(
        source_worker,
        "CodexClient",
        lambda settings: pytest.fail("Sparse collection invoked inference"),
    )
    assessment = source_worker.assess_youtube_source(
        resource, client=NS(sample_resource=lambda *args, **kw: [video()])
    )
    assert assessment["provider_resource_id"] == VIDEO
    assert assessment["approval_supported"] is False and assessment["relevance"] == "uncertain"


def test_explicit_video_enrichment_uses_metadata_api_outside_transaction_and_never_html(
    monkeypatch,
):
    current = video_article()
    job = ArticleEnrichmentJob(
        id=uuid.uuid4(), article_id=current.id, status="running", attempts=1, result={}
    )
    added, transactions = [], []
    session = NS(
        scalar=lambda statement: current,
        get=lambda *args: None,
        add=added.append,
        flush=lambda: None,
    )

    @contextmanager
    def begin():
        transactions.append(True)
        try:
            yield session
        finally:
            transactions.pop()

    def videos(ids):
        assert ids == [VIDEO] and not transactions
        return {VIDEO: video()}

    monkeypatch.setattr(YouTubeClient, "from_settings", lambda: NS(videos=videos))
    monkeypatch.setattr(
        article_tasks, "fetch_article_page", lambda *args: pytest.fail("YouTube HTML fetched")
    )
    monkeypatch.setattr(article_tasks, "owned_job", lambda *args: job)
    monkeypatch.setattr(article_tasks, "approved_sources", lambda *args, **kwargs: [uuid.uuid4()])
    article_tasks._enrich_video(
        NS(begin=begin), job.id, uuid.uuid4(), current.id, current.canonical_url, VIDEO
    )
    assert job.status == "succeeded" and job.result["provider"] == "youtube"
    assert next(item for item in added if isinstance(item, ArticleContent)).text == DESCRIPTION


@pytest.mark.integration
@pytest.mark.parametrize(
    "state", ["relevant", "unrelated", "sparse", "unknown-language", "unapproved", "inactive"]
)
def test_imported_video_uses_normal_analysis_and_publication_review(database, monkeypatch, state):
    from devfeed_core.config import get_settings
    from devfeed_core.models import Article, Source, Topic
    from devfeed_core.services import OperationConflict
    from devfeed_core.youtube import YouTubePlaylistItem
    from devfeed_core.youtube_ingestion import remove_memberships, store_videos
    from sqlalchemy import select

    if state == "unrelated":
        monkeypatch.setenv("DEVFEED_FULL_AUTOMATION", "true")
        get_settings.cache_clear()
    item = replace(
        video(),
        description="" if state == "sparse" else DESCRIPTION,
        language=None if state == "unknown-language" else "en",
    )
    with database.begin() as session:
        source = Source(
            name="A single submitted video",
            provider="youtube",
            resource_kind="video",
            provider_resource_id=VIDEO,
            recurring=False,
            source_type="publisher",
            website_url=item.canonical_url,
            approval_status="approved",
            publication_policy="auto",
        )
        topic = Topic(name="PostgreSQL", slug="postgresql", kind="technology", status="active")
        session.add_all([source, topic])
        session.flush()
        store_videos(
            session, source, {VIDEO: item}, [YouTubePlaylistItem(VIDEO, VIDEO, None)], uuid.uuid4()
        )
        source_id, topic_id = source.id, topic.id
        identifier = session.scalar(select(Article.id).where(Article.provider_resource_id == VIDEO))
    with database.begin() as session:
        if state == "unapproved":
            session.get(Source, source_id).approval_status = "pending"
        if state == "inactive":
            remove_memberships(session, source_id, [identifier])
    with database.begin() as session:
        current = session.get(Article, identifier)
        if state in {"unapproved", "inactive"}:
            with pytest.raises(OperationConflict, match="approved source"):
                analysis.request_analysis(session, identifier)
            assert current.publication_status == "unpublished"
            return
        job = analysis.request_analysis(session, identifier, automatic=True)
        if state == "sparse":
            assert job is None and current.review_status == "pending"
            assert current.publication_status == "unpublished" and current.ai_summary is None
            return
        snapshot = analysis.source_snapshot(current, session.get(ArticleContent, identifier))
        taxonomy = analysis.catalog(session)
        job.catalog_snapshot = analysis.analysis_candidates(taxonomy, snapshot)
        value = result(
            page_kind="video",
            content_format="video",
            developer_relevance="unrelated" if state == "unrelated" else "relevant",
            outcome="insufficient_evidence" if state == "unknown-language" else "ready",
            language=None if state == "unknown-language" else "en",
            ai_summary=DESCRIPTION,
            topics=[
                {
                    "topic_id": topic_id,
                    "role": "primary",
                    "relevance": 0.95,
                    "evidence": "PostgreSQL row locks",
                }
            ],
        )
        analysis.validate_evidence(value, snapshot, taxonomy)
        job.result = value.model_dump(mode="json")
        analysis.apply_analysis(session, current, job, value)
        decision = publication_policy.apply_publication_policy(
            session, current, job, taxonomy=taxonomy
        )
        assert (decision["status"] == "published") is (state == "relevant")
        assert current.content_format == "video"
        if state == "unrelated":
            assert decision["status"] == "rejected"
        if state == "unknown-language":
            assert current.review_status == "pending" and current.ai_summary is None
