"""Article worker decisions over actual content and ownership-service read boundaries."""

import hashlib
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import article_tasks as worker
from devfeed_aggregator.article_pages import PageArticle
from devfeed_core import image_jobs
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.models import Article, ArticleContent, ArticleEnrichmentJob, Tag, Topic
from devfeed_core.urls import fingerprint
from test_source_readiness_unit import NOW, configuration


def article(**changes):
    url = "https://publisher.example/article"
    return Article(
        **{
            "id": uuid.uuid4(),
            "canonical_url": url,
            "url_hash": fingerprint(url),
            "title": "Publisher title",
            "summary": None,
            "author": None,
            "language": None,
            "content_type": "article",
            "content_format": "article",
            "published_at": None,
            "image_url": None,
            "metadata_source_type": "aggregator",
            "review_status": "pending",
            "publication_status": "unpublished",
            "classification_provenance": {},
            "editorial_revision": 0,
            **changes,
        }
    )


def page(**changes):
    return PageArticle(
        **{
            "title": "Python routing tutorial",
            "summary": "Python maps request paths to handlers.",
            "author": "Publisher writer",
            "published_at": NOW - timedelta(days=1),
            "image_url": None,
            "language": "en",
            "text_source": "body",
            "text": "Python maps request paths to handlers. Route parameters carry named values.",
            "evidence": {"language": "en"},
            **changes,
        }
    )


@pytest.mark.parametrize("origin", ["ai", "manual"])
def test_page_merge_keeps_contextual_classification_and_never_restores_keyword_guesses(
    monkeypatch, origin
):
    monkeypatch.setattr(worker, "get_settings", lambda: configuration(ai_enabled=False))
    attach = Mock(return_value=0)
    monkeypatch.setattr(worker, "attach_source_tags", attach)
    current = article(
        language="ja", content_type="news", classification_provenance={"origin": origin}
    )
    session = Mock()
    changed = worker.apply_page(session, current, page())
    assert current.title == "Python routing tutorial" and current.summary
    assert current.language == "ja" and current.content_type == "news"
    assert not {"language", "content_type"}.intersection(changed)
    session.scalars.assert_not_called()
    session.execute.assert_not_called()


def test_non_ai_fallback_uses_real_keyword_matching_and_keeps_source_tags_explicit(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", lambda: configuration(ai_enabled=False))
    current, session = article(), Mock()
    matched = Topic(
        id=uuid.uuid4(),
        name="Python",
        slug="python",
        kind="language",
        status="active",
        keywords=["Python"],
    )
    unrelated = Topic(
        id=uuid.uuid4(), name="Go", slug="go", kind="language", status="active", keywords=["Golang"]
    )
    tag = Tag(id=uuid.uuid4(), name="Python", slug="python", aliases=[])
    session.scalars.side_effect = [
        SimpleNamespace(all=lambda: [matched, unrelated]),
        SimpleNamespace(all=lambda: [tag]),
    ]
    attach = Mock(return_value=1)
    monkeypatch.setattr(worker, "attach_source_tags", attach)
    changed = worker.apply_page(session, current, page(), source_tag_ids=[tag.id])
    assert current.content_type == "tutorial" and current.language == "en" and "tags" in changed
    attach.assert_called_once_with(session, current.id, [tag.id])
    topic_params, tag_params = [
        call.args[0].compile().params for call in session.execute.call_args_list
    ]
    assert topic_params["topic_id"] == matched.id and topic_params["role"] == "supporting"
    assert topic_params["origin"] == "heuristic" and topic_params["relevance"] == 0.5
    assert tag_params["tag_id"] == tag.id and tag_params["article_id"] == current.id


def test_new_publisher_image_requests_storage_after_merge_without_erasing_existing_metadata(
    monkeypatch,
):
    monkeypatch.setattr(
        worker, "get_settings", lambda: configuration(ai_enabled=True, image_storage_enabled=True)
    )
    monkeypatch.setattr(worker, "attach_source_tags", Mock(return_value=0))
    request = Mock()
    monkeypatch.setattr(image_jobs, "request_image", request)
    current, session = article(), Mock()
    changed = worker.apply_page(
        session,
        current,
        page(image_url="https://publisher.example/new.png", text_source=None, text=""),
    )
    assert (
        changed == ["image_url"] and current.title == "Publisher title" and current.summary is None
    )
    assert current.image_url.endswith("/new.png")
    request.assert_called_once_with(session, current.id, automatic=True)
    session.flush.assert_called_once()


@pytest.mark.parametrize(
    "guard",
    [
        "none",
        "automation-off",
        "forbidden",
        "transport",
        "changed-url",
        "rejected",
        "summary",
        "body",
    ],
)
def test_missing_publisher_policy_requires_an_explicit_missing_page_and_no_retained_text(
    monkeypatch, guard
):
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: configuration(ai_enabled=True, full_automation=guard != "automation-off"),
    )
    current, session = article(), Mock()
    status = 403 if guard == "forbidden" else 404
    transport = FeedError(
        "upstream private content",
        status=status,
        reason="transport_error" if guard == "transport" else "http_error",
    )
    original = current.canonical_url
    if guard == "changed-url":
        current.canonical_url += "/new"
    elif guard == "rejected":
        current.review_status = "rejected"
    elif guard == "summary":
        current.summary = "Useful publisher guidance explains Python routing behavior."
    session.get.return_value = (
        ArticleContent(
            article_id=current.id,
            text="Useful retained full article text about Python routing.",
            method="body",
        )
        if guard == "body"
        else None
    )
    job = ArticleEnrichmentJob(
        id=uuid.uuid4(),
        article_id=current.id,
        status="failed",
        attempts=1,
        created_at=NOW - timedelta(minutes=2),
        available_at=NOW - timedelta(minutes=1),
        finished_at=NOW,
        result={},
        error="Article lookup failed: http_error",
    )
    decide = Mock()
    monkeypatch.setattr(worker, "decide_article", decide)
    assert worker.reject_missing_article(session, current, job, transport, original) is (
        guard == "none"
    )
    if guard == "none":
        body = decide.call_args.args[2]
        assert body.action == "reject" and body.expected_revision == 0 and "HTTP 404" in body.note
        assert decide.call_args.kwargs["automation"] == {
            "policy": "missing-publisher-article-v1",
            "job_id": str(job.id),
            "http_status": 404,
        }
    else:
        decide.assert_not_called()


@pytest.mark.parametrize("content_state", ["new", "changed", "same", "same-new-title"])
def test_body_application_records_real_content_hashes_and_invalidates_only_new_evidence(
    monkeypatch, content_state
):
    settings = configuration(ai_enabled=True)
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "utcnow", lambda: NOW)
    current = article(summary="Previous summary", metadata_source_type="page")
    current.editorial_revision = 2
    result = page(
        title="Changed publisher title" if content_state == "same-new-title" else current.title,
        summary=current.summary,
        language=current.language,
        author=current.author,
        published_at=current.published_at,
    )
    digest = hashlib.sha256(result.text.encode()).hexdigest()
    content = (
        None
        if content_state == "new"
        else ArticleContent(
            article_id=current.id,
            text=result.text if content_state.startswith("same") else "Previous body.",
            content_hash=digest
            if content_state.startswith("same")
            else hashlib.sha256(b"Previous body.").hexdigest(),
            url=current.canonical_url,
            method="body",
            retrieved_at=NOW - timedelta(hours=1),
        )
    )
    job = ArticleEnrichmentJob(
        id=uuid.uuid4(),
        article_id=current.id,
        status="running",
        attempts=1,
        lease_token=uuid.uuid4(),
        lease_until=NOW + timedelta(minutes=4),
        created_at=NOW - timedelta(minutes=2),
        available_at=NOW - timedelta(minutes=1),
        result={},
    )
    session = Mock()
    session.scalar.return_value = current
    session.get.return_value = content
    opened = []

    @contextmanager
    def begin():
        opened.append(True)
        try:
            yield session
        finally:
            opened.pop()

    factory = SimpleNamespace(begin=begin)

    def fetch(url):
        assert not opened
        return FetchResult(200, b"<article>Body</article>", url)

    monkeypatch.setattr(worker, "fetch_article_page", fetch)
    monkeypatch.setattr(worker, "publisher_redirect", lambda _: None)
    monkeypatch.setattr(worker, "extract_article", lambda *_: result)
    owner = Mock(return_value=job)
    monkeypatch.setattr(worker, "owned_job", owner)
    monkeypatch.setattr(worker, "approved_sources", Mock(return_value=[uuid.uuid4()]))
    monkeypatch.setattr(worker, "resolve_source_tags", lambda *_: ({}, 0))
    monkeypatch.setattr(worker, "attach_source_tags", Mock(return_value=0))
    analyze = Mock()
    monkeypatch.setattr(worker, "request_analysis", analyze)
    token = job.lease_token
    worker._enrich_claimed(factory, job.id, token, current.id, current.canonical_url, 0)
    owner.assert_called_once_with(session, ArticleEnrichmentJob, job.id, token)
    if content_state == "new":
        content = session.add.call_args.args[0]
    assert content.text == result.text and content.content_hash == digest
    assert current.editorial_revision == (2 if content_state == "same" else 3)
    assert (
        current.review_status == "pending"
        and job.status == "succeeded"
        and job.outcome == "enriched"
    )
    analyze.assert_called_once_with(session, current.id, automatic=True)
