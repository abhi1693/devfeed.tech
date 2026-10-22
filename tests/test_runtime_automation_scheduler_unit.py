"""Bounded orchestration over preselected repository rows, not SQL eligibility proofs."""

import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import article_jobs
from devfeed_core import automation_scheduler as scheduler
from devfeed_core.models import (
    Article,
    ArticleContent,
    ArticleOrigin,
    TopicProposal,
    TopicReanalysis,
)
from test_runtime_automation_events_unit import topic
from test_source_readiness_unit import NOW, configuration, source


class ReadFactory:
    def __init__(self, *sessions):
        self.sessions = iter(sessions)
        self.open = False

    @contextmanager
    def begin(self):
        self.open = True
        try:
            yield next(self.sessions)
        finally:
            self.open = False


@pytest.fixture
def runtime(monkeypatch):
    value = SimpleNamespace(
        settings=configuration(
            ai_enabled=True, auto_research_imports=False, auto_reanalyze_topics=False
        ),
    )
    monkeypatch.setattr(scheduler, "get_settings", lambda: value.settings)
    monkeypatch.setattr(scheduler, "utcnow", lambda: NOW)
    for name, result in (
        ("schedule_tag_topic_discovery", {"tags_scanned": 2}),
        ("schedule_source_admission", 3),
        ("schedule_article_automation", {"articles_checked": 4}),
        ("schedule_topic_corrections", 5),
        ("schedule_topic_branding", 6),
        ("schedule_relationship_coverage", {"relationship_jobs_scheduled": 7}),
        ("finalize_relationship_reviews", 8),
    ):
        boundary = Mock(return_value=result)
        monkeypatch.setattr(scheduler, name, boundary)
        setattr(value, name, boundary)
    value.research = Mock()
    value.analyze = Mock(return_value=object())
    value.authorized = Mock(return_value=True)
    monkeypatch.setattr(scheduler, "request_topic_analysis", value.research)
    monkeypatch.setattr(scheduler, "request_analysis", value.analyze)
    monkeypatch.setattr(article_jobs, "approved_sources", value.authorized)
    return value


def test_exact_identity_discovery_remains_available_with_the_ai_kill_switch(runtime):
    runtime.settings = configuration(ai_enabled=False)
    factory = ReadFactory()
    counts = scheduler.schedule_automation(factory)
    assert counts == {
        "topic_research_scheduled": 0,
        "articles_reanalyzed": 0,
        "reanalysis_scanned": 0,
        "relationship_jobs_scheduled": 0,
        "relationship_scans_completed": 0,
        "tags_scanned": 2,
    }
    runtime.schedule_tag_topic_discovery.assert_called_once_with(factory)
    runtime.schedule_source_admission.assert_not_called()
    runtime.schedule_article_automation.assert_not_called()
    runtime.schedule_topic_corrections.assert_not_called()
    runtime.schedule_relationship_coverage.assert_not_called()


def test_scheduler_combines_independent_stage_results_without_losing_default_counters(runtime):
    counts = scheduler.schedule_automation(ReadFactory())
    assert counts == {
        "topic_research_scheduled": 0,
        "articles_reanalyzed": 0,
        "reanalysis_scanned": 0,
        "relationship_jobs_scheduled": 7,
        "relationship_scans_completed": 0,
        "tags_scanned": 2,
        "source_reviews_scheduled": 3,
        "articles_checked": 4,
        "topic_corrections_scheduled": 5,
        "topic_branding_scheduled": 6,
        "relationships_rejected": 8,
    }


def proposal(*, complete=False):
    proposed = {
        "name": "Python",
        "slug": "python",
        "kind": "language",
        "description": "A programming language.",
        "aliases": ["Python language"],
        "keywords": ["scripting"],
        "website_url": "https://python.example",
        "logo_url": "https://python.example/logo.png",
        "facts": [
            {
                "name": "maintainer",
                "value": "Python community",
                "source_url": "https://python.example/about",
                "retrieved_at": NOW.isoformat(),
            }
        ],
    }
    if not complete:
        proposed["description"] = None
    return TopicProposal(
        id=uuid.uuid4(),
        batch_id=uuid.uuid4(),
        slug="python",
        action="create",
        origin="import",
        source_name="Language catalog",
        proposed=proposed,
        baseline=None,
        evidence=[],
        status="pending",
        created_at=NOW,
        created_by=scheduler.ACTOR,
        research_requested=True,
    )


def test_import_research_consumes_each_request_once_and_skips_prior_attempts_and_complete_metadata(
    runtime,
):
    runtime.settings = configuration(ai_enabled=True, auto_research_imports=True)
    requested, attempted, complete = proposal(), proposal(), proposal(complete=True)
    # Independently admitted imports have unique pending slugs.
    for index, item in enumerate((requested, attempted, complete)):
        item.slug = item.proposed["slug"] = f"python-{index}"
    session = Mock()
    session.scalars.return_value.all.return_value = [requested, attempted, complete]
    session.scalar.side_effect = [None, uuid.uuid4(), None]
    counts = scheduler.schedule_automation(ReadFactory(session))
    assert counts["topic_research_scheduled"] == 1
    runtime.research.assert_called_once_with(session, requested.id, scheduler.ACTOR)
    assert not any(item.research_requested for item in (requested, attempted, complete))
    assert all(item.status == "pending" for item in (requested, attempted, complete))


def test_bounded_topic_mode_does_not_also_run_legacy_import_research(runtime):
    runtime.settings = configuration(ai_enabled=True, ai_bounded_topics_enabled=True)
    session = Mock()
    session.scalar.return_value = None  # No outstanding reanalysis task in bounded mode.
    counts = scheduler.schedule_automation(ReadFactory(session))
    assert counts["topic_research_scheduled"] == 0
    session.scalars.assert_not_called()
    runtime.research.assert_not_called()


@pytest.mark.parametrize("state", ["absent-task", "deleted-topic", "retired-topic"])
def test_obsolete_reanalysis_tasks_finish_without_reclassifying_articles(runtime, state):
    runtime.settings = configuration(ai_enabled=True, auto_reanalyze_topics=True)
    current = topic(status="rejected" if state == "retired-topic" else "active")
    task = TopicReanalysis(
        id=uuid.uuid4(),
        topic_id=current.id,
        topic_snapshot={"name": "Python", "slug": "python", "aliases": [], "keywords": []},
        created_at=NOW,
        finished_at=None,
    )
    session = Mock()
    session.scalar.return_value = None if state == "absent-task" else task
    session.get.return_value = None if state == "deleted-topic" else current
    assert scheduler.schedule_automation(ReadFactory(session))["reanalysis_scanned"] == 0
    assert task.finished_at == (None if state == "absent-task" else NOW)
    session.scalars.assert_not_called()
    runtime.analyze.assert_not_called()


@pytest.mark.parametrize("full_page", [False, True])
def test_reanalysis_advances_past_nonmatches_and_unauthorized_rows_and_resumes_full_pages(
    runtime, full_page
):
    runtime.settings = configuration(
        ai_enabled=True, auto_reanalyze_topics=True, automation_batch_size=5
    )
    current = topic()
    task = TopicReanalysis(
        id=uuid.uuid4(),
        topic_id=current.id,
        topic_snapshot={
            "name": "Python runtime",
            "slug": "python-runtime",
            "aliases": ["Python"],
            "keywords": [],
        },
        created_at=NOW,
        finished_at=None,
        after_article_id=uuid.UUID(int=1),
    )
    articles = []
    for index, title in enumerate(
        ["Python tutorial", "Storage performance", "Python APIs", "Python tooling", "Python loops"]
    ):
        identifier = uuid.UUID(int=index + 2)
        publisher = source(approval="approved")
        article = Article(
            id=identifier,
            canonical_url=f"https://publisher.example/{index}",
            title=title,
            summary=title + " explained for engineers.",
            review_status="pending",
            publication_status="unpublished",
            editorial_revision=0,
            classification_provenance={},
            metadata_source_type="rss",
            topic_links=[],
            origins=[
                ArticleOrigin(
                    article_id=identifier,
                    source_id=publisher.id,
                    source=publisher,
                    entry_key=str(identifier),
                    original_url=f"https://publisher.example/{index}",
                )
            ],
        )
        articles.append(article)
    if not full_page:
        articles.pop()
    session = Mock()
    session.scalar.return_value = task
    session.scalars.return_value.all.return_value = articles
    content = {
        item.id: ArticleContent(article_id=item.id, text=item.summary, method="body")
        for item in articles
    }
    session.get.side_effect = lambda model, identifier: (
        current if model is type(current) else content[identifier]
    )
    runtime.authorized.side_effect = [True, False, True, *([True] if full_page else [])]
    # A coalesced/previously exhausted request is not counted as newly scheduled work.
    runtime.analyze.side_effect = [object(), None, *([object()] if full_page else [])]

    counts = scheduler.schedule_automation(ReadFactory(session))

    assert counts["reanalysis_scanned"] == len(articles)
    assert counts["articles_reanalyzed"] == (2 if full_page else 1)
    assert task.after_article_id == articles[-1].id
    assert task.finished_at == (None if full_page else NOW)
    assert [call.args[1] for call in runtime.authorized.call_args_list] == [
        item.id for item in articles if "Python" in item.title
    ]
    assert [call.args[1] for call in runtime.analyze.call_args_list] == [
        articles[index].id for index in ([0, 3, 4] if full_page else [0, 3])
    ]
    assert all(call.kwargs == {"automatic": True} for call in runtime.analyze.call_args_list)
