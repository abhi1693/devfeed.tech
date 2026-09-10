"""Every collection API, every filter parameter, and deterministic table pagination."""

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import pytest
from devfeed_admin_api.jobs import MODELS, retry_candidates
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.job_retries import retry_candidate
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticlePublicationDecision,
    ArticleReview,
    IngestionJob,
    NotificationDelivery,
    Source,
    SourceEnrichmentJob,
    SourcePublicationPolicyReview,
    SourceReview,
    Tag,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    TopicRelationProposal,
)
from redis import Redis
from sqlalchemy import insert, select, text, update
from test_api_query_budgets import (
    identity,
    profile_data,  # noqa: F401
    profile_request,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def table_data(profile_data):  # noqa: F811 - imported pytest fixture
    size = profile_data
    now = datetime.now(UTC) - timedelta(minutes=1)
    with get_engine().begin() as c:
        for i in range(size):
            status = ("approved", "pending", "rejected")[i % 3]
            c.execute(
                update(Source)
                .where(Source.id == identity("source", i))
                .values(
                    approval_status=status,
                    enabled=i % 2 == 0,
                    source_type="publisher" if i % 4 < 2 else "aggregator",
                )
            )
            c.execute(
                update(Topic)
                .where(Topic.id == identity("topic", i))
                .values(status=("active", "proposed", "rejected")[i % 3])
            )
            c.execute(
                update(Tag)
                .where(Tag.id == identity("tag", i))
                .values(topic_id=identity("topic", i))
            )
            c.execute(
                update(Article)
                .where(Article.id.in_([identity("published", i), identity("pending", i)]))
                .values(
                    language="en" if i % 2 == 0 else "fr",
                    content_type="article" if i % 2 == 0 else "release",
                )
            )
            if i % 7 == 0:
                c.execute(
                    update(Article)
                    .where(Article.id == identity("pending", i))
                    .values(review_status="rejected")
                )
            draft = {"name": f"Proposal {i}", "slug": f"proposal-{i}", "kind": "technology"}
            c.execute(
                update(TopicProposal)
                .where(TopicProposal.id == identity("proposal", i))
                .values(
                    status=status,
                    action="update" if i % 2 else "create",
                    topic_id=identity("topic", i) if i % 2 else None,
                    baseline={"draft": draft} if i % 2 else None,
                    reviewed_at=now if status != "pending" else None,
                    reviewed_by={} if status != "pending" else None,
                    applied={"draft": draft} if status == "approved" else None,
                )
            )
            for model, kind in ((ArticleAnalysisJob, "analysis"), (TopicAnalysisJob, "research")):
                c.execute(
                    update(model)
                    .where(model.id == identity(f"{kind}-0", i))
                    .values(status="failed")
                )
                c.execute(
                    update(model)
                    .where(model.id == identity(f"{kind}-2", i))
                    .values(
                        status=("queued", "running", "succeeded", "succeeded", "failed")[i % 5],
                        outcome="enriched" if i % 5 == 2 else None,
                    )
                )
        # Real no-analysis proposals, and topic-targeted research for the topic_id filter.
        c.execute(
            insert(TopicProposal.__table__),
            [
                dict(
                    id=identity("unresearched", i),
                    batch_id=identity("batch", 1),
                    slug=f"unresearched-{i}",
                    action="create",
                    origin="import",
                    source_name="Other",
                    proposed={
                        "name": f"Other {i}",
                        "slug": f"unresearched-{i}",
                        "kind": "language",
                    },
                    created_by={},
                )
                for i in range(size // 5)
            ],
        )
        c.execute(
            insert(TopicAnalysisJob.__table__),
            [
                dict(
                    id=identity("relationship-job", i),
                    topic_id=identity("topic", i),
                    status="succeeded",
                    input_hash="a" * 64,
                    input_snapshot={},
                    requested_by={},
                    prompt_version="profile",
                )
                for i in range(size)
            ],
        )
        c.execute(
            insert(TopicRelationProposal.__table__),
            [
                dict(
                    id=identity("relationship", i),
                    job_id=identity("relationship-job", i),
                    topic_id=identity("topic", i),
                    related_topic_id=identity("topic", (i + 1) % size),
                    relation="related_to",
                    explanation=f"Relationship {i}",
                    evidence_url="https://example.test/evidence",
                    evidence_title="Evidence",
                    evidence_quote="Evidence quote",
                    topic_snapshot={"name": f"Topic {i:05}"},
                    related_topic_snapshot={"name": f"Topic {(i + 1) % size:05}"},
                    status=("pending", "approved", "rejected")[i % 3],
                    created_by={},
                    reviewed_at=now if i % 3 else None,
                    reviewed_by={} if i % 3 else None,
                )
                for i in range(size)
            ],
        )
        for model, kind, subject in (
            (IngestionJob, "ingestion", "source"),
            (SourceEnrichmentJob, "source-enrichment", "source"),
            (ArticleEnrichmentJob, "article-enrichment", "pending"),
            (ArticleImageJob, "images", "pending"),
            (NotificationDelivery, "notifications", None),
        ):
            for generation in range(2):
                c.execute(
                    insert(model.__table__),
                    [
                        dict(
                            id=identity(f"{kind}-{generation}", i),
                            status="failed"
                            if generation == 0
                            else ("queued", "running", "succeeded", "failed")[i % 4],
                            created_at=now - timedelta(hours=1 - generation),
                            **(
                                {
                                    "source_id" if subject == "source" else "article_id": identity(
                                        subject, i
                                    )
                                }
                                if subject
                                else dict(
                                    event_key=f"event-{generation}-{i}",
                                    dedup_key=identity(f"delivery-{generation}", i).hex,
                                    audience="admin",
                                    category="pipeline.failed",
                                    payload={"title": f"Event {i}"},
                                )
                            ),
                        )
                        for i in range(size)
                    ],
                )
        # Histories must have enough rows on the same parent to exercise large pages.
        for model, values in (
            (
                ArticleReview,
                lambda i: dict(
                    article_id=identity("published", 0),
                    action="update",
                    revision=i + 1,
                    actor=f"needle-{i}",
                    note="History",
                ),
            ),
            (
                SourceReview,
                lambda i: dict(
                    source_id=identity("source", 0),
                    decision="approved",
                    actor=f"needle-{i}",
                    note="History",
                ),
            ),
            (
                SourcePublicationPolicyReview,
                lambda i: dict(
                    source_id=identity("source", 0), revision=i, mode="manual", actor=f"needle-{i}"
                ),
            ),
            (
                ArticlePublicationDecision,
                lambda i: dict(
                    article_id=identity("published", 0),
                    fingerprint=identity("decision", i).hex,
                    decision={"reason": f"needle-{i}"},
                ),
            ),
        ):
            c.execute(
                insert(model.__table__),
                [
                    dict(**values(i), created_at=now + timedelta(microseconds=i))
                    for i in range(size)
                ],
            )
        # Source/topic zero remain visible; give the public relation list a visible target.
        c.execute(update(Topic).where(Topic.id == identity("topic", 1)).values(status="active"))
        c.execute(text("ANALYZE"))
    return size


@dataclass
class Table:
    template: str
    budget: int
    filters: dict[str, list] = field(default_factory=dict)
    sorts: tuple[str, ...] = ()
    bindings: dict[str, str] = field(default_factory=dict)
    search: str | None = None

    @property
    def path(self):
        return self.template.format(**self.bindings)


def tables():
    aid, sid, tid = (str(identity(k, 0)) for k in ("published", "source", "topic"))
    missing = str(identity("missing", 0))
    yield Table(
        "/v1/admin/articles",
        5,
        dict(
            review_status=["approved", "pending", "rejected"],
            publication_status=["published", "unpublished"],
            source_id=[sid, missing],
            topic_id=[tid, missing],
            tag_id=[str(identity("tag", 0)), missing],
        ),
        ("title", "discovered_at", "review_status", "publication_status"),
        search="Database",
    )
    yield Table(
        "/v1/admin/sources",
        2,
        dict(
            source_type=["publisher", "aggregator"],
            approval_status=["approved", "pending", "rejected"],
            enabled=["true", "false"],
        ),
        ("name", "created_at", "approval_status"),
        search="Source",
    )
    yield Table(
        "/v1/admin/topics",
        2,
        {"status": ["active", "proposed", "rejected"]},
        ("name", "kind", "status", "created_at"),
        search="Topic",
    )
    yield Table("/v1/admin/tags", 2, {"topic_id": [tid, missing]}, ("name", "slug"), search="Tag")
    yield Table(
        "/v1/admin/topic-proposals",
        3,
        dict(
            status=["pending", "approved", "rejected"],
            batch_id=[str(identity("batch", 0)), missing],
            kind=["technology", "language"],
            source=["Profile", "Other"],
            action=["create", "update"],
            analysis=["queued", "not_run", "running", "enriched", "no_additions", "failed"],
            missing=[
                "any",
                "description",
                "aliases",
                "keywords",
                "website_url",
                "logo_url",
                "facts",
            ],
        ),
        ("slug", "created_at", "status"),
        search="Proposal",
    )
    yield Table(
        "/v1/admin/topic-relations", 2, {"topic_id": [tid, missing]}, ("relation",), search="Topic"
    )
    yield Table(
        "/v1/admin/topic-relationships",
        4,
        dict(topic_id=[tid, missing], status=["approved", "pending"]),
        ("relation", "status"),
        search="Topic",
    )
    yield Table(
        "/v1/admin/topic-relationship-proposals",
        3,
        dict(
            topic_id=[tid, missing],
            job_id=[str(identity("relationship-job", 0)), missing],
            status=["pending", "approved", "rejected"],
        ),
        ("created_at", "relation", "status"),
        search="Topic",
    )
    yield Table(
        "/v1/admin/topic-replacements",
        2,
        {"exclude_topic_id": [tid, missing]},
        ("name", "slug", "kind", "status"),
        search="Topic",
    )
    for kind in (
        "ingestion",
        "article-enrichment",
        "images",
        "source-enrichment",
        "analysis",
        "topic-analysis",
        "notifications",
    ):
        yield Table(
            "/v1/admin/jobs/{kind}",
            3,
            dict(
                status=["queued", "running", "succeeded", "failed", "retried"],
                source_id=[sid, missing],
                article_id=[str(identity("pending", 0)), missing],
                retryable_only=["true", "false"],
            ),
            ("created_at", "status"),
            {"kind": kind},
            search=str(identity(f"{kind}-0", 0))
            if kind != "topic-analysis"
            else str(identity("research-0", 0)),
        )
    yield Table(
        "/v1/admin/jobs/ai-analysis",
        4,
        dict(
            status=["queued", "running", "succeeded", "failed", "retried"],
            analysis_type=["articles", "topics"],
            article_id=[str(identity("pending", 0)), missing],
            proposal_id=[str(identity("proposal", 0)), missing],
            topic_id=[tid, missing],
            retryable_only=["true", "false"],
        ),
        ("created_at", "status"),
        search="Topic",
    )
    for suffix, key, value in (
        ("jobs", "source_id", sid),
        ("article-jobs", "article_id", str(identity("pending", 0))),
    ):
        yield Table(
            f"/v1/admin/ingestion/{suffix}",
            1,
            {key: [value, missing], "status": ["queued", "running", "succeeded", "failed"]},
        )
    for path, bindings in (
        ("/v1/admin/articles/{article_id}/reviews", {"article_id": aid}),
        ("/v1/admin/sources/{source_id}/reviews", {"source_id": sid}),
        ("/v1/admin/automation/articles/{article_id}/decisions", {"article_id": aid}),
        ("/v1/admin/automation/sources/{source_id}/policies", {"source_id": sid}),
    ):
        yield Table(path, 3, sorts=("created_at",), bindings=bindings, search="needle")
    yield Table(
        "/v1/sources", 1, dict(enabled=["true", "false"], source_type=["publisher", "aggregator"])
    )
    yield Table("/v1/topics", 1)
    yield Table("/v1/tags", 1)
    yield Table("/v1/topics/{slug}/relations", 2, bindings={"slug": "topic-0"})
    yield Table(
        "/v1/feed",
        4,
        dict(
            tag=["tag-0", "no-such-tag"],
            exclude_tag=["tag-0"],
            source_id=[sid, missing],
            exclude_source=[sid],
            content_type=["article", "news", "tutorial", "release", "comparison", "opinion"],
            language=["en", "fr"],
            topic=["topic-0", "absent-topic"],
        ),
        search="Database",
    )


def rows(body):
    return body if isinstance(body, list) else body["items"]


def check_filters(spec, values, records, source_enabled):
    for row in records:
        for key in (
            "status",
            "review_status",
            "publication_status",
            "source_type",
            "content_type",
            "language",
            "batch_id",
            "action",
            "article_id",
            "proposal_id",
            "job_id",
        ):
            if key in values:
                assert str(row[key]) == str(values[key]), (spec.path, key, row)
        if "enabled" in values:
            enabled = row["enabled"] if "enabled" in row else source_enabled[row["id"]]
            assert enabled is (values["enabled"] == "true")
        if "source_id" in values:
            sources = [s["id"] for s in row["sources"]] if "sources" in row else [row["source_id"]]
            assert values["source_id"] in sources
        if "topic_id" in values:
            if spec.path == "/v1/admin/articles":
                assert values["topic_id"] in [t["topic_id"] for t in row["topics"]]
            else:
                assert values["topic_id"] in [row.get("topic_id"), row.get("related_topic_id")]
        if "exclude_topic_id" in values:
            assert row["topic_id"] != values["exclude_topic_id"]
        if "tag_id" in values:
            assert values["tag_id"] in [tag["id"] for tag in row["tags"]]
        if "retryable_only" in values and values["retryable_only"] == "true":
            assert row["retryable"]
        if "kind" in values:
            assert row["proposed"]["kind"] == values["kind"]
        if "source" in values:
            assert row["source_name"] == values["source"]
        if "analysis_type" in values:
            assert row["kind"] == (
                "analysis" if values["analysis_type"] == "articles" else "topic-analysis"
            )
        if "analysis" in values:
            analysis = row["analysis"]
            state = values["analysis"]
            if state == "not_run":
                assert analysis is None
            elif state in {"enriched", "no_additions"}:
                assert analysis["status"] == "succeeded"
                assert (analysis["outcome"] == "enriched") is (state == "enriched")
            else:
                assert analysis["status"] == state
        if "missing" in values:
            fields = ("description", "aliases", "keywords", "website_url", "logo_url", "facts")
            assert any(
                not row["proposed"].get(f)
                for f in (fields if values["missing"] == "any" else [values["missing"]])
            )
        if "tag" in values:
            assert values["tag"] in row["tags"]
        if "exclude_tag" in values:
            assert values["exclude_tag"] not in row["tags"]
        if "exclude_source" in values:
            assert values["exclude_source"] not in [s["id"] for s in row["sources"]]
        if "topic" in values:
            assert any(
                t["slug"] == values["topic"] and t["role"] in {"primary", "supporting"}
                for t in row["topics"]
            )


def test_all_table_calls(table_data, client, admin_client):
    specs = list(tables())
    reports = []
    issues = []
    repeats = int(os.environ.get("DEVFEED_PROFILE_REPEATS", "1"))
    report_path = os.environ.get("DEVFEED_PROFILE_REPORT")
    schemas = {False: client.app.openapi(), True: admin_client.app.openapi()}
    source_enabled = {str(identity("source", i)): i % 2 == 0 for i in range(table_data)}
    with get_engine().connect() as connection:
        for model in MODELS.values():
            assert set(connection.scalars(retry_candidates(model))) == set(
                connection.scalars(select(model.id).where(retry_candidate(model)))
            ), model.__name__
    # Fail when a new collection endpoint or query parameter lacks matrix coverage.
    collections = set()
    for schema in schemas.values():
        for path, operations in schema["paths"].items():
            operation = operations.get("get", {})
            response = (
                operation.get("responses", {})
                .get("200", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            if "Page" in str(response) or response.get("type") == "array":
                collections.add(path)
    assert collections <= {s.template for s in specs}, collections - {s.template for s in specs}
    for spec in specs:
        http = admin_client if spec.path.startswith("/v1/admin/") else client
        operation = schemas[http is admin_client]["paths"][spec.template]["get"]
        params = {p["name"]: p for p in operation.get("parameters", []) if p["in"] == "query"}
        assert set(params) <= set(spec.filters) | {"limit", "offset", "sort", "q", "cursor"}
        assert ("q" in params) == (spec.search is not None)
        for key, variants in spec.filters.items():
            schema = params[key]["schema"]
            schema = next((v for v in schema.get("anyOf", []) if v.get("type") != "null"), schema)
            if "$ref" in schema:
                schema = schemas[http is admin_client]["components"]["schemas"][
                    schema["$ref"].split("/")[-1]
                ]
            if "enum" in schema:
                assert set(variants) == set(schema["enum"]), (spec.path, key)
        requests = [({}, "unfiltered")]
        if "limit" in params:
            requests += [({"limit": n}, "page") for n in (1, 100)]
            if params["limit"]["schema"].get("maximum") == 500:
                requests.append(({"limit": 500}, "page"))
        if "offset" in params:
            requests += [({"offset": n, "limit": 100}, "offset") for n in (100, table_data * 20)]
        if "cursor" in params:
            first = http.get(spec.path, params={"limit": 1}).json()
            assert first["next_cursor"]
            requests.append(({"cursor": first["next_cursor"], "limit": 100}, "cursor"))
        for name, values in spec.filters.items():
            requests += [({name: value, "limit": 100}, "filter") for value in values]
        if spec.search is not None:
            requests += [
                ({"q": value, "limit": 100}, "search")
                for value in (spec.search, "zzzz-no-profile-match", "%_")
            ]
        for sort in spec.sorts:
            requests += [({"sort": value, "limit": 100}, "sort") for value in (sort, "-" + sort)]
        combined = {key: values[0] for key, values in spec.filters.items()}
        if combined:
            requests.append(({**combined, "limit": 100}, "combined"))
            if spec.search is not None:
                requests.append(({**combined, "q": spec.search, "limit": 100}, "combined-search"))
            keys = list(combined)
            requests += [
                ({key: combined[key], keys[i + 1]: combined[keys[i + 1]], "limit": 100}, "pair")
                for i, key in enumerate(keys[:-1])
            ]
        if "retryable_only" in params:
            requests.append(
                ({"status": "failed", "retryable_only": "true", "limit": 100}, "retry-filter")
            )
        page_identities = {}
        for values, category in requests:
            path = spec.path + ("?" + urlencode(values, doseq=True) if values else "")
            warm = http.get(path)
            assert warm.status_code == 200, (path, warm.text)
            result, body = profile_request(
                http, path, spec.budget, repeats, plans=bool(report_path)
            )
            result.update(table=spec.path, category=category, params=values, rows=len(rows(body)))
            reports.append(result)
            if not rows(body) and (
                spec.template == "/v1/admin/jobs/{kind}"
                or spec.path == "/v1/admin/topic-relationships"
            ):
                assert result["queries"] == 2, path
            assert rows(warm.json()) == rows(body), path
            if (
                category == "search"
                and values["q"] in {"zzzz-no-profile-match", "%_"}
                and rows(body)
            ):
                issues.append(f"Search ignored: {path}")
            if category == "search" and values["q"] == spec.search:
                assert rows(body), ("Positive search fixture did not match", path)
            if category == "sort":
                key = values["sort"].removeprefix("-")
                ordered = [
                    r["proposed"][key]
                    if key == "slug" and spec.path.endswith("/topic-proposals")
                    else r[key]
                    for r in rows(body)
                ]
                if spec.path == "/v1/admin/topic-replacements" and key == "name":
                    ordered = [v.lower() for v in ordered]
                assert ordered == sorted(ordered, reverse=values["sort"].startswith("-")), path
            if "total" in body:
                assert body["total"] >= len(rows(body))
                assert len(rows(body)) <= int(values.get("limit", 25))
            check_filters(spec, values, rows(body), source_enabled)
            if values == {"limit": 100} or values == {"offset": 100, "limit": 100}:
                page_identities[values.get("offset", 0)] = {
                    json.dumps(row, sort_keys=True) for row in rows(body)
                }
            if category == "cursor":
                assert first["items"][0]["id"] not in {r["id"] for r in rows(body)}
        if 0 in page_identities and 100 in page_identities:
            assert not page_identities[0] & page_identities[100], spec.path
        if "sort" in params:
            response = http.get(spec.path, params={"sort": "unsupported-field"})
            if response.status_code != 422:
                issues.append(f"Unsupported sort silently ignored: {spec.path}")
    auxiliary = []
    for path, budget in (
        ("/v1/admin/topic-proposals/filters", 2),
        ("/v1/admin/ingestion/status", 6),
        ("/v1/admin/workers", 1),
    ):
        result, _ = profile_request(admin_client, path, budget, repeats, plans=bool(report_path))
        auxiliary.append(result)
    with Redis.from_url(get_settings().redis_url) as redis:
        for population in (1, 20):
            for i in range(population):
                name = f"profile-{i}"
                redis.sadd("rq:workers", "rq:worker:" + name)
                redis.hset(
                    "rq:worker:" + name,
                    mapping=dict(
                        queues="analysis",
                        state="busy",
                        current_job=name,
                        last_heartbeat=datetime.now(UTC).isoformat(),
                    ),
                )
                redis.expire("rq:worker:" + name, 3600)
                redis.hset(
                    "rq:job:" + name,
                    mapping={
                        "data": json.dumps(
                            [
                                "devfeed_aggregator.analysis_tasks.analyze_article",
                                None,
                                [str(identity("analysis-2", i * 5 + 1))],
                                {},
                            ]
                        )
                    },
                )
            result, body = profile_request(
                admin_client, "/v1/admin/workers", 3, repeats, plans=bool(report_path)
            )
            assert len(body["workers"]) == population
            assert all(w["current_job"]["target_name"] for w in body["workers"])
            result["workers"] = population
            auxiliary.append(result)
        result, _ = profile_request(
            admin_client, "/v1/admin/workers/profile-0", 2, repeats, plans=bool(report_path)
        )
        auxiliary.append(result)
    if report_path:
        target = Path(report_path).with_suffix(".tables.json")
        target.write_text(
            json.dumps(
                dict(
                    rows_per_family=table_data,
                    repeats=repeats,
                    tables=len(specs),
                    cases=len(reports),
                    issues=issues,
                    endpoints=reports,
                    auxiliary=auxiliary,
                ),
                indent=2,
            )
            + "\n"
        )
    assert not issues, "\n".join(issues)
