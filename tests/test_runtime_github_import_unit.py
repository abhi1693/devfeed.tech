"""Pinned GitHub catalog failures and supervised import command contracts."""

import io
import json
import uuid
import zipfile
from unittest.mock import Mock

import pytest
from devfeed_core import github_topics as github
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.topic_proposals import TopicDraft
from test_github_topics import DOCUMENT, REVISION
from test_source_readiness_unit import configuration

ACTOR = {"subject": "editor-123", "issuer": "https://identity.example"}


@pytest.fixture(autouse=True)
def clean_revision_cache():
    reader = github.repository_topics
    reader.cache_clear()
    yield
    reader.cache_clear()


def archive(*files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zipped:
        for filename, content in files:
            zipped.writestr(filename, content)
    return stream.getvalue()


@pytest.mark.parametrize("problem", ["bad-zip", "no-topics", "oversized-topic"])
def test_unusable_archives_fail_as_a_whole_without_importing_or_echoing_document_contents(
    monkeypatch, problem
):
    content = (
        b"private document contents"
        if problem == "bad-zip"
        else archive(("explore/README.md", b"No curated topics"))
        if problem == "no-topics"
        else archive(("explore/topics/python/index.md", b"x" * 65537))
    )
    monkeypatch.setattr(github, "read_document", lambda *_args, **_kw: content)
    with pytest.raises(github.GitHubUnavailable, match="No topics were imported") as caught:
        github.repository_topics(REVISION)
    assert "private document" not in str(caught.value)


@pytest.mark.parametrize(
    "invalid", ["long-description", "unsafe-url", "missing-name", "invalid-yaml"]
)
def test_bad_topic_metadata_is_reported_per_row_while_valid_topics_remain_previewable(
    monkeypatch, invalid
):
    bad = DOCUMENT.replace(b"topic: python", b"topic: broken")
    if invalid == "long-description":
        bad = bad.replace(b"Python is a programming language.", b"Detailed description. " * 110)
        expected = "description: String should have at most 2000 characters"
    elif invalid == "unsafe-url":
        bad = bad.replace(b"https://www.python.org/", b"http://127.0.0.1/private")
        expected = "website_url: invalid value"
    elif invalid == "missing-name":
        bad = bad.replace(b"display_name: Python\n", b"")
        expected = "GitHub metadata needs manual correction"
    else:
        bad = bad.replace(b"display_name: Python", b"display_name: [unterminated")
        expected = "GitHub metadata needs manual correction"
    payload = archive(
        ("explore/topics/python/index.md", DOCUMENT), ("explore/topics/broken/index.md", bad)
    )
    monkeypatch.setattr(github, "read_document", lambda *_args, **_kw: payload)
    rows = github.repository_topics(REVISION)
    assert rows[0] == {"slug": "broken", "issue": expected}
    assert rows[1]["fields"]["name"] == "Python" and "issue" not in rows[1]


def test_missing_front_matter_is_a_specific_per_topic_issue(monkeypatch):
    payload = archive(("explore/topics/python/index.md", b"Plain markdown only"))
    monkeypatch.setattr(github, "read_document", lambda *_args, **_kw: payload)
    assert github.repository_topics(REVISION) == [
        {"slug": "python", "issue": "Missing topic front matter"}
    ]


def test_non_timeout_fetch_failure_has_a_safe_general_error_and_preserves_the_cause(monkeypatch):
    failure = FeedError("private upstream content", status=502)
    monkeypatch.setattr(github, "_fetch", Mock(side_effect=failure))
    with pytest.raises(github.GitHubUnavailable, match="could not be read") as caught:
        github.read_document(github.API, 32768)
    assert caught.value.__cause__ is failure and "private" not in str(caught.value)


@pytest.mark.parametrize(
    "response", [b"not json", b"{}", b'{"object":{"sha":42}}', b'{"object":{"sha":"../main"}}']
)
def test_pull_resolves_only_a_valid_commit_hash_before_reading_any_catalog_rows(
    monkeypatch, response
):
    monkeypatch.setattr(github, "read_document", Mock(return_value=response))
    repository = Mock()
    monkeypatch.setattr(github, "repository_topics", repository)
    session = Mock()
    with pytest.raises(github.GitHubUnavailable, match="revision could not be read"):
        github.pull_topics(session, github.GitHubPull(), ACTOR)
    repository.assert_not_called()
    session.add.assert_not_called()


def draft(name, **changes):
    return TopicDraft(name=name, slug=name.lower(), kind="unclassified", **changes)


@pytest.mark.parametrize("ai", [False, True])
def test_pull_keeps_imports_pending_and_skips_existing_or_same_batch_identities(monkeypatch, ai):
    python = draft("Python", aliases=["Python language"])
    existing = draft("Rust")
    pending = draft("Golang", aliases=["Go language"])
    rows = [
        {"slug": "python", "fields": python.model_dump(mode="json")},
        {
            "slug": "pythonruntime",
            "fields": TopicDraft(
                name="Python",
                slug="pythonruntime",
                kind="unclassified",
                aliases=["Python language"],
            ).model_dump(mode="json"),
        },
        {"slug": "rust", "fields": existing.model_dump(mode="json")},
        {
            "slug": "go-language",
            "fields": TopicDraft(name="Golang", slug="go-language", kind="unclassified").model_dump(
                mode="json"
            ),
        },
        {"slug": "broken", "issue": "Missing topic front matter"},
    ]
    monkeypatch.setattr(
        github,
        "read_document",
        Mock(return_value=json.dumps({"object": {"sha": REVISION}}).encode()),
    )
    rows.sort(key=lambda row: row["slug"])
    repository = Mock(return_value=rows)
    monkeypatch.setattr(github, "repository_topics", repository)
    monkeypatch.setattr(github, "catalogs", lambda _: {uuid.uuid4(): existing})
    lock = Mock()
    monkeypatch.setattr(github, "lock_topics", lock)
    monkeypatch.setattr(
        github, "get_settings", lambda: configuration(ai_enabled=ai, auto_research_imports=True)
    )
    session = Mock()
    session.scalars.return_value = [pending.model_dump(mode="json")]
    result = github.pull_topics(session, github.GitHubPull(), ACTOR)
    assert result.model_dump() == {
        "revision": REVISION,
        "total": 5,
        "processed": 5,
        "created": 1,
        "skipped": 3,
        "issues": ["broken: Missing topic front matter"],
        "next_offset": None,
    }
    created = session.add.call_args.args[0]
    assert created.slug == "python" and created.origin == "import" and created.action == "create"
    assert created.created_by == ACTOR and created.reviewed_at is None and created.applied is None
    assert created.research_requested is ai
    assert created.batch_id == uuid.uuid5(
        uuid.NAMESPACE_URL, f"https://github.com/github/explore/tree/{REVISION}"
    )
    assert created.evidence == [
        {
            "source_url": f"https://github.com/github/explore/blob/{REVISION}/topics/python/index.md",
            "provider": "github/explore",
            "revision": REVISION,
        }
    ]
    lock.assert_called_once_with(session)
    repository.assert_called_once_with(REVISION)


def test_pinned_pull_resumes_at_the_given_offset_without_repeating_prior_rows(monkeypatch):
    rows = [
        {
            "slug": f"topic-{i:03}",
            "fields": TopicDraft(
                name=f"Topic {i}", slug=f"topic-{i:03}", kind="unclassified"
            ).model_dump(mode="json"),
        }
        for i in range(103)
    ]
    monkeypatch.setattr(github, "repository_topics", lambda _: rows)
    monkeypatch.setattr(github, "catalogs", lambda _: {})
    monkeypatch.setattr(github, "lock_topics", Mock())
    monkeypatch.setattr(github, "get_settings", lambda: configuration())
    read = Mock()
    monkeypatch.setattr(github, "read_document", read)
    session = Mock()
    session.scalars.return_value = []
    first = github.pull_topics(session, github.GitHubPull(revision=REVISION), ACTOR)
    assert first.created == 100 and first.next_offset == 100
    session.add.reset_mock()
    last = github.pull_topics(
        session, github.GitHubPull(revision=REVISION, offset=first.next_offset), ACTOR
    )
    assert last.created == 3 and last.processed == 103 and last.next_offset is None
    assert [call.args[0].slug for call in session.add.call_args_list] == [
        "topic-100",
        "topic-101",
        "topic-102",
    ]
    read.assert_not_called()
