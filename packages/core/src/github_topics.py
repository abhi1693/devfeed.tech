"""Read GitHub's curated Explore catalog into supervised topic import previews."""

import io
import json
import re
import uuid
import zipfile
from functools import lru_cache
from typing import Annotated

import httpcore
import yaml
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, _fetch
from devfeed_core.models import TopicProposal
from devfeed_core.schemas import InputModel
from devfeed_core.topic_proposals import TopicDraft, catalogs
from devfeed_core.topics import identity_terms, lock_topics

Revision = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
API = "https://api.github.com/repos/github/explore"
RAW = "https://raw.githubusercontent.com/github/explore"
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class GitHubUnavailable(ValueError):
    pass


class GitHubPull(InputModel):
    revision: Revision | None = None
    offset: int = Field(default=0, ge=0, le=10000)


class GitHubPullResult(InputModel):
    revision: Revision
    total: int
    processed: int
    created: int
    skipped: int
    issues: list[str]
    next_offset: int | None


def read_document(url: str, max_bytes: int, *, timeout: int = 8) -> bytes:
    try:
        return _fetch(
            url,
            None,
            None,
            accept="application/vnd.github+json, text/plain",
            max_bytes=max_bytes,
            timeout=timeout,
        ).body
    except FeedError as exc:
        if exc.status in {403, 429}:
            raise GitHubUnavailable(
                "GitHub's public API rate limit was reached. Try again later."
            ) from exc
        if exc.reason == "deadline_exceeded" or isinstance(
            exc.__cause__, httpcore.TimeoutException
        ):
            raise GitHubUnavailable(
                "GitHub took too long to respond. Continue pulling to retry the remaining topics."
            ) from exc
        raise GitHubUnavailable("GitHub could not be read. Try previewing again later.") from exc


class FrontMatterLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ValueError("YAML aliases are not accepted")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate front matter fields")
        return super().construct_mapping(node, deep=deep)


def topic_document(content: bytes, slug: str, revision: str, kind: str) -> dict:
    document = content.decode("utf-8-sig").replace("\r\n", "\n")
    if not document.startswith("---\n") or "\n---\n" not in document[4:]:
        raise ValueError("Missing topic front matter")
    header, description = document[4:].split("\n---\n", 1)
    fields = yaml.load(header, Loader=FrontMatterLoader)
    if not isinstance(fields, dict) or fields.get("topic") != slug:
        raise ValueError("Topic identity does not match its catalog path")
    aliases = fields.get("aliases", "")
    if not isinstance(aliases, str):
        raise ValueError("Topic aliases must be comma-separated names")
    result = dict(
        name=fields["display_name"],
        slug=slug,
        kind=kind,
        aliases=[v.strip() for v in aliases.split(",") if v.strip()],
        description=description.strip() or fields.get("short_description") or None,
    )
    if fields.get("url"):
        result["website_url"] = fields["url"]
    logo = fields.get("logo")
    if isinstance(logo, str) and re.fullmatch(r"[a-zA-Z0-9_-]+\.png", logo):
        result["logo_url"] = f"{RAW}/{revision}/topics/{slug}/{logo}"
    return result


@lru_cache(maxsize=2)
def repository_topics(revision: str) -> list[dict]:
    """Read only bounded topic documents; never extract or execute repository files."""
    payload = read_document(
        f"https://codeload.github.com/github/explore/zip/{revision}", 33_554_432, timeout=30
    )
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            files = archive.infolist()
            if len(files) > 20000 or sum(info.file_size for info in files) > 134_217_728:
                raise ValueError("Repository archive exceeds limits")
            rows = []
            seen = set()
            for info in files:
                match = re.fullmatch(
                    r"[^/]+/topics/([a-z0-9]+(?:-[a-z0-9]+)*)/index\.md", info.filename
                )
                if not match:
                    continue
                slug = match[1]
                if slug in seen or info.file_size > 65_536:
                    raise ValueError("Duplicate or oversized topic document")
                seen.add(slug)
                with archive.open(info) as document:
                    content = document.read(65_537)
                if len(content) > 65_536:
                    raise ValueError("Oversized topic document")
                try:
                    fields = topic_document(content, slug, revision, "technology")
                    # Missing/malformed metadata is reported per topic, not guessed.
                    TopicDraft.model_validate(fields)
                    rows.append({"slug": slug, "fields": fields})
                except (ValueError, KeyError, TypeError, yaml.YAMLError, RecursionError):
                    rows.append({"slug": slug, "issue": "GitHub metadata needs manual correction"})
            if not rows:
                raise ValueError("Repository has no curated topics")
            return sorted(rows, key=lambda row: row["slug"])
    except (ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise GitHubUnavailable(
            "GitHub returned an invalid topic archive. No topics were imported."
        ) from exc


def pull_topics(session: Session, body: GitHubPull, actor: dict[str, str]) -> GitHubPullResult:
    revision = body.revision
    if revision is None:
        try:
            revision = json.loads(read_document(f"{API}/git/ref/heads/main", 32_768))["object"][
                "sha"
            ]
            if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision):
                raise ValueError("Invalid catalog revision")
        except (ValueError, KeyError, TypeError) as exc:
            raise GitHubUnavailable(
                "GitHub's catalog revision could not be read. Try again later."
            ) from exc
    rows = repository_topics(revision)
    batch = rows[body.offset : body.offset + 100]
    lock_topics(session)
    known = [identity_terms(topic) for topic in catalogs(session).values()]
    known.extend(
        identity_terms(TopicDraft.model_validate(value))
        for value in session.scalars(select(TopicProposal.proposed))
    )
    proposals = []
    issues = []
    skipped = 0
    for row in batch:
        if "issue" in row:
            issues.append(f"{row['slug']}: {row['issue']}")
            continue
        fields = row["fields"]
        draft = TopicDraft.model_validate(fields)
        identity = identity_terms(draft)
        if any(identity & existing for existing in known):
            skipped += 1
            continue
        proposal = TopicProposal(
            batch_id=uuid.uuid5(
                uuid.NAMESPACE_URL, f"https://github.com/github/explore/tree/{revision}"
            ),
            slug=draft.slug,
            action="create",
            origin="import",
            source_name="GitHub curated topics",
            proposed=draft.model_dump(mode="json"),
            evidence=[
                {
                    "source_url": f"https://github.com/github/explore/blob/{revision}/topics/{draft.slug}/index.md",
                    "provider": "github/explore",
                    "revision": revision,
                }
            ],
            created_by=actor,
            research_requested=get_settings().ai_enabled and get_settings().auto_research_imports,
        )
        session.add(proposal)
        proposals.append(proposal)
        known.append(identity)
    session.flush()
    processed = min(len(rows), body.offset + len(batch))
    return GitHubPullResult(
        revision=revision,
        total=len(rows),
        processed=processed,
        created=len(proposals),
        skipped=skipped,
        issues=issues,
        next_offset=processed if processed < len(rows) else None,
    )
