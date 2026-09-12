import io
import zipfile

import pytest
from devfeed_core import github_topics as github
from devfeed_core.feeds.fetcher import FeedError
from pydantic import ValidationError

REVISION = "a" * 40
DOCUMENT = b"""---
topic: python
display_name: Python
aliases: py, python3
short_description: Programming language
url: https://www.python.org/
logo: python.png
---
Python is a programming language.
"""


def test_repository_pull_reads_only_topic_documents_and_caches_the_pinned_revision(monkeypatch):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("explore-sha/topics/python/index.md", DOCUMENT)
        archive.writestr("explore-sha/topics/python/python.png", b"not an image")
        archive.writestr("explore-sha/../../outside", b"ignored")
    calls = []

    def read(url, maximum, **kwargs):
        calls.append(url)
        assert kwargs["timeout"] == 30
        return stream.getvalue()

    monkeypatch.setattr(github, "read_document", read)
    github.repository_topics.cache_clear()
    try:
        rows = github.repository_topics(REVISION)
        assert len(rows) == 1
        assert rows[0]["fields"]["name"] == "Python"
        assert rows[0]["fields"]["kind"] == "unclassified"
        assert rows[0]["fields"]["aliases"] == ["py", "python3"]
        assert rows[0]["fields"]["logo_url"] == f"{github.RAW}/{REVISION}/topics/python/python.png"
        assert github.repository_topics(REVISION) == rows
        assert len(calls) == 1
    finally:
        github.repository_topics.cache_clear()


def test_repository_pull_rejects_duplicate_topic_files(monkeypatch):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("a/topics/python/index.md", DOCUMENT)
        archive.writestr("b/topics/python/index.md", DOCUMENT)
    monkeypatch.setattr(github, "read_document", lambda *a, **kw: stream.getvalue())
    github.repository_topics.cache_clear()
    with pytest.raises(github.GitHubUnavailable, match="invalid topic archive"):
        github.repository_topics(REVISION)


@pytest.mark.parametrize("aliases", ["", "null", "~", '""'])
def test_blank_optional_aliases_produce_a_valid_proposal(aliases):
    document = DOCUMENT.replace(b"aliases: py, python3", f"aliases: {aliases}".encode())
    draft = github.TopicDraft.model_validate(
        github.topic_document(document, "python", REVISION, "unclassified")
    )
    assert draft.aliases == []
    assert draft.name == "Python"


@pytest.mark.parametrize("aliases", ["false", "0", "[]", "{}", "[py, python3]"])
def test_invalid_alias_types_are_not_treated_as_empty(aliases):
    document = DOCUMENT.replace(b"aliases: py, python3", f"aliases: {aliases}".encode())
    with pytest.raises(ValueError, match="comma-separated"):
        github.topic_document(document, "python", REVISION, "unclassified")


def test_repository_caps_imported_aliases_and_reports_duplicate_metadata(monkeypatch):
    stream = io.BytesIO()
    aliases = ", ".join(f"alias{i}" for i in range(102)).encode()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("explore/topics/python/index.md", DOCUMENT)
        archive.writestr(
            "explore/topics/ludum-dare/index.md",
            DOCUMENT.replace(b"topic: python", b"topic: ludum-dare").replace(
                b"aliases: py, python3", b"aliases: " + aliases
            ),
        )
        archive.writestr(
            "explore/topics/qiskit/index.md",
            DOCUMENT.replace(
                b"topic: python", b"topic: qiskit\nreleased: February 2021\nreleased: March 2017"
            ),
        )
    monkeypatch.setattr(github, "read_document", lambda *a, **kw: stream.getvalue())
    github.repository_topics.cache_clear()
    try:
        rows = {row["slug"]: row for row in github.repository_topics(REVISION)}
        assert rows["python"]["fields"]["name"] == "Python"
        assert rows["ludum-dare"]["fields"]["aliases"] == [f"alias{i}" for i in range(50)]
        assert rows["qiskit"]["issue"] == "Duplicate front matter fields: released"
        assert "issue" not in rows["ludum-dare"]
        assert "fields" not in rows["qiskit"]
    finally:
        github.repository_topics.cache_clear()


@pytest.mark.parametrize(
    "header",
    [
        "topic: python\ntopic: rust\ndisplay_name: Python",
        "topic: python\ndisplay_name: !!python/object/apply:os.system [echo injected]",
        "topic: python\ndisplay_name: &name Python\naliases: *name",
        "topic: rust\ndisplay_name: Python",
    ],
)
def test_front_matter_rejects_ambiguous_unsafe_or_mismatched_data(header):
    with pytest.raises((ValueError, github.yaml.YAMLError)):
        github.topic_document(
            f"---\n{header}\n---\nDescription".encode(), "python", REVISION, "language"
        )


def test_rate_limit_returns_actionable_error_without_exposing_upstream_content(monkeypatch):
    def limited(*args, **kwargs):
        raise FeedError("upstream secret", status=403)

    monkeypatch.setattr(github, "_fetch", limited)
    with pytest.raises(github.GitHubUnavailable, match="rate limit") as error:
        github.read_document(github.API, 1000)
    assert "secret" not in str(error.value)


def test_github_submit_rejects_untrusted_revision_paths():
    with pytest.raises(ValidationError):
        github.GitHubPull(revision="../main")


def test_timeout_returns_a_safe_resumable_error(monkeypatch):
    import httpcore

    def timed_out(*args, **kwargs):
        raise FeedError("upstream secret", reason="transport_error") from httpcore.ReadTimeout(
            "upstream secret"
        )

    monkeypatch.setattr(github, "_fetch", timed_out)
    with pytest.raises(github.GitHubUnavailable, match="Continue pulling") as error:
        github.read_document(github.API, 1000)
    assert "secret" not in str(error.value)
