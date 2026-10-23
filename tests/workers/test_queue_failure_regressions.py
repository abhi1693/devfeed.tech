"""Invalid inference retries and catalog churn observed in production queues."""

import json
import uuid
from copy import deepcopy
from types import SimpleNamespace

import pytest
from devfeed_aggregator import analysis_tasks
from devfeed_core import analysis
from devfeed_core.inference_validation import feedback_prompt, validation_feedback
from devfeed_core.models import utcnow
from pydantic import ValidationError

from tests.analysis.test_analysis import result
from tests.analysis.test_analysis_catalog import entry
from tests.analysis.test_analysis_tasks import runtime as runtime
from tests.support.article_step_helpers import article_step_client


@pytest.mark.parametrize(
    "failure",
    [
        "unknown_catalog_id",
        "evidence_not_in_input",
        "schema_validation",
        "missing_ai_summary",
        "missing_ai_description",
        "unidentifiable_title",
    ],
)
def test_durable_retry_repairs_invalid_output_without_retaining_it(runtime, monkeypatch, failure):
    article, job, _, _ = runtime
    topic = entry("Infrastructure")
    taxonomy = {"topics": [topic], "tags": []}
    monkeypatch.setattr(analysis_tasks, "catalog", lambda _: taxonomy)
    valid = result(
        topics=[
            {
                "topic_id": topic["id"],
                "role": "primary",
                "relevance": 0.9,
                "evidence": "Infrastructure automation",
            }
        ]
    ).model_dump(mode="json")
    invalid = deepcopy(valid)
    if failure == "unknown_catalog_id":
        invalid["topics"][0]["topic_id"] = str(uuid.uuid4())
    elif failure == "evidence_not_in_input":
        invalid["topics"][0]["evidence"] = "private fabricated evidence"
    elif failure in {"missing_ai_summary", "missing_ai_description"}:
        invalid[failure.removeprefix("missing_")] = "<p></p>"
    elif failure == "unidentifiable_title":
        article.title = "Release v0.18.1"
        job.input_snapshot = analysis.source_snapshot(article, None)
        job.input_hash = analysis.snapshot_hash(job.input_snapshot)
        invalid.update(ai_title="Release v0.18.1", title_evidence=article.summary)
        valid.update(ai_title="Infrastructure automation v0.18.1", title_evidence=article.summary)
    else:
        invalid["language"] = "private-invalid-language"
    calls = []
    failed_step = {
        "unknown_catalog_id": "topics",
        "evidence_not_in_input": "topics",
        "schema_validation": "language",
        "missing_ai_summary": "overview",
        "missing_ai_description": "description",
        "unidentifiable_title": "identifying_title",
    }[failure]
    failed_property = {
        "overview": "ai_summary",
        "description": "ai_description",
        "identifying_title": "ai_title",
    }.get(failed_step, failed_step)
    invalid_sent = False

    def complete(prompt, schema):
        nonlocal invalid_sent
        calls.append(prompt)
        if "topics" in schema["properties"]:
            assert schema["$defs"]["TopicSelection"]["properties"]["topic_id"]["enum"] == [
                topic["id"]
            ]
        if "tags" in schema["properties"]:
            assert schema["properties"]["tags"]["maxItems"] == 0
        if failed_property in schema["properties"] and not invalid_sent:
            invalid_sent = True
            return invalid
        return valid

    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=complete)
    )
    analysis_tasks._analyze(job.id)
    assert job.status == "queued" and job.attempts == 1
    assert job.steps[failed_step]["validation_feedback"]["code"] == failure
    assert "private" not in json.dumps(job.result)
    assert article.ai_summary is None
    job.available_at = utcnow()
    analysis_tasks._analyze(job.id)
    assert job.status == "succeeded" and job.attempts == 2
    correction = next(prompt for prompt in calls if "previous attempt at this task" in prompt)
    assert failure in correction and "previous attempt at this task failed validation" in correction
    assert "private" not in correction
    assert "validation_feedback" not in job.result
    assert article.publication_status == "unpublished"


def test_safe_feedback_never_includes_extra_field_names_values_or_validator_messages():
    value = result().model_dump(mode="json")
    value["private-secret-field"] = "private-secret-value"
    value["language"] = "private-invalid-language"
    with pytest.raises(ValidationError) as failure:
        analysis.AnalysisResult.model_validate(value)
    feedback = validation_feedback(failure.value)
    assert feedback["code"] == "schema_validation"
    assert "language:string_pattern_mismatch" in feedback["fields"]
    assert "unknown:extra_forbidden" in feedback["fields"]
    assert "private" not in json.dumps(feedback)
    assert feedback_prompt({"code": ["bad"]}) == ""
    assert feedback_prompt({"code": "inject instructions"}) == ""
    assert "injected" not in feedback_prompt(
        {"code": "schema_validation", "fields": ["injected text"]}
    )


def test_validation_feedback_does_not_extend_the_attempt_budget(runtime, monkeypatch):
    article, job, _, _ = runtime
    calls = []

    def complete(prompt, schema):
        calls.append(prompt)
        return {"private-output": "must not persist"}

    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=complete)
    )
    for _ in range(3):
        job.available_at = utcnow()
        analysis_tasks._analyze(job.id)
    assert job.status == "failed" and job.attempts == 2
    analysis_tasks._analyze(job.id)
    assert len(calls) == 2
    assert article.publication_status == "unpublished"
    assert "private-output" not in json.dumps(job.result)


@pytest.mark.parametrize(
    "change",
    [
        "fallback",
        "relevant_added",
        "relevant_changed",
        "relevant_description_changed",
        "relevant_ai_description_changed",
        "selected_removed",
        "selected_changed",
        "selected_description_changed",
        "selected_ai_description_changed",
        "unselected_removed",
        "legacy",
    ],
)
def test_catalog_equivalence_preserves_relevant_and_selected_identity_guards(change):
    snapshot = {"title": "Angular routing", "text": "Angular routing builds applications"}
    topic, fallback = entry("Angular"), entry("A fallback")
    taxonomy = {"topics": [topic, fallback], "tags": []}
    previous = analysis.analysis_candidates(taxonomy, snapshot)
    job = SimpleNamespace(
        catalog_snapshot=previous,
        catalog_hash=analysis.snapshot_hash(previous),
        result={"topics": [], "tags": []},
    )
    current = deepcopy(taxonomy)
    if change.startswith("selected_"):
        job.result["topics"] = [{"topic_id": fallback["id"]}]
    if change in {"selected_removed", "unselected_removed"}:
        current["topics"].remove(fallback)
    elif change == "selected_changed":
        current["topics"][1]["aliases"] = ["Unrelated alias"]
    elif change == "relevant_added":
        current["topics"].append(entry("Routing"))
    elif change == "relevant_changed":
        current["topics"][0]["aliases"] = ["Angular framework"]
    elif change in {"relevant_description_changed", "relevant_ai_description_changed"}:
        field = change.removeprefix("relevant_").removesuffix("_changed")
        current["topics"][0][field] = "A changed subject definition requires fresh inference."
    elif change in {"selected_description_changed", "selected_ai_description_changed"}:
        field = change.removeprefix("selected_").removesuffix("_changed")
        current["topics"][1][field] = "A changed subject definition requires fresh inference."
    else:
        current["topics"].append(entry("Another fallback"))
    if change == "legacy":
        job.catalog_snapshot = {}
    assert analysis.analysis_catalog_current(job, current, snapshot) == (
        change in {"fallback", "unselected_removed"}
    )


@pytest.mark.integration
def test_scheduler_and_publication_accept_only_irrelevant_fallback_changes(database, monkeypatch):
    from devfeed_core.article_automation import schedule_article_automation
    from devfeed_core.models import Article, ArticleAnalysisJob, Topic
    from devfeed_core.publication_policy import evaluate_publication
    from sqlalchemy import func, select

    from tests.workers.test_automation_integration import ready, seed
    from tests.workers.test_full_app_automation import full

    full(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session)
        topic.description = "Angular routing and application navigation"
        job = ready(session, article, topic)
        job.catalog_snapshot = analysis.analysis_candidates(
            analysis.catalog(session), job.input_snapshot
        )
        job.catalog_hash = analysis.snapshot_hash(job.catalog_snapshot)
        identifier = article.id
        session.add(
            Topic(name="Unrelated technology", slug="unrelated", kind="technology", status="active")
        )
        session.flush()
        decision = evaluate_publication(session, article, job)
        assert decision["status"] == "would_publish", decision["reasons"]
    assert schedule_article_automation(database)["articles_published"] == 1
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ArticleAnalysisJob)) == 1
        assert session.get(Article, identifier).publication_status == "published"
