import copy
import json
import uuid

import pytest
from devfeed_core.analysis import AnalysisResult, analysis_prompt, validate_evidence
from devfeed_core.analysis_wire import compact_request, restore_identities
from devfeed_core.inference_validation import InferenceValidationError


def catalog_item(name):
    return dict(
        id=str(uuid.uuid4()),
        name=name,
        slug=name.lower(),
        aliases=[name, name],
        keywords=[],
        kind=None,
    )


def test_wire_compaction_preserves_candidates_evidence_and_uuid_contract():
    taxonomy = {
        "topics": [catalog_item("Rust"), catalog_item("Python")],
        "tags": [catalog_item("Rust")],
    }
    snapshot = {
        "title": "Rust and Python",
        "source_summary": "",
        "text": "Rust integrates with Python.",
    }
    prompt, schema, ids = compact_request(snapshot, taxonomy, short_ids=True)
    data = json.loads(prompt.rsplit("\n", 1)[1])
    assert data["article"] == snapshot
    assert [r["name"] for r in data["catalog"]["topics"]] == ["Rust", "Python"]
    assert data["catalog"]["topics"][0]["aliases"] == ["Rust"]
    assert len(prompt) < len(analysis_prompt(snapshot, taxonomy))
    assert schema["$defs"]["TopicSelection"]["properties"]["topic_id"]["enum"] == ["t0", "t1"]
    output = dict(
        outcome="ready",
        developer_relevance="relevant",
        language="en",
        content_type="article",
        content_format="article",
        ai_summary="Rust integrates with Python.",
        ai_description=None,
        ai_title=None,
        title_evidence=None,
        page_kind="article",
        topics=[dict(topic_id="t0", role="primary", relevance=1, evidence="Rust")],
        tags=[dict(id="g0", evidence="Rust")],
        reasons=[],
    )
    restored = restore_identities(output, ids)
    assert output["topics"][0]["topic_id"] == "t0"  # No mutation of audit output.
    parsed = AnalysisResult.model_validate(restored)
    validate_evidence(parsed, snapshot, taxonomy)
    assert str(parsed.topics[0].topic_id) == taxonomy["topics"][0]["id"]
    output["topics"][0]["evidence"] = "Invented text"
    with pytest.raises(InferenceValidationError, match="not present"):
        validate_evidence(
            AnalysisResult.model_validate(restore_identities(output, ids)), snapshot, taxonomy
        )


@pytest.mark.parametrize("identifier", ["g0", "t100", str(uuid.uuid4()), None, []])
def test_wire_mapping_rejects_foreign_and_malformed_ids(identifier):
    with pytest.raises(InferenceValidationError):
        restore_identities(
            {"topics": [{"topic_id": identifier}]},
            {"topics": {"t0": str(uuid.uuid4())}, "tags": {}},
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [
        "fallback",
        "new_fallback",
        "relevant",
        "selected",
        "model",
        "editorial",
        "content",
        "force",
        "wire_success",
        "wire_failed",
    ],
)
def test_reanalysis_skips_only_irrelevant_fallback_churn(database, change, monkeypatch):
    from devfeed_core import analysis
    from devfeed_core.config import get_settings
    from devfeed_core.models import Article, ArticleOrigin, Source
    from devfeed_core.urls import fingerprint

    if change.startswith("wire_"):
        monkeypatch.setattr(get_settings(), "ai_compact_article_prompts", True)
    relevant, fallback = catalog_item("Rust"), catalog_item("Unrelated")
    taxonomy = {"topics": [relevant, fallback], "tags": []}
    with database.begin() as session:
        source = Source(
            name="Source",
            feed_url="https://example.com/rss",
            source_type="publisher",
            approval_status="approved",
        )
        article = Article(
            title="Rust deployment",
            summary="Rust deployment improves developer workflows. " * 4,
            canonical_url="https://example.com/rust",
            url_hash=fingerprint("https://example.com/rust"),
        )
        session.add_all([source, article])
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=article.id,
                source_id=source.id,
                entry_key="rust",
                original_url=article.canonical_url,
            )
        )
        session.flush()
        job = analysis.request_analysis(session, article.id, automatic=True, taxonomy=taxonomy)
        assert job is not None
        job.status, job.outcome = "succeeded", "applied"
        job.model = get_settings().codex_model
        job.catalog_snapshot = copy.deepcopy(
            analysis.analysis_candidates(taxonomy, job.input_snapshot)
        )
        job.result = {"topics": [{"topic_id": relevant["id"]}], "tags": []}
        if change.startswith("wire_"):
            job.usage = {"prompt_format": "compact-json-v1"}
            if change == "wire_failed":
                job.status, job.outcome = "failed", None
        elif change == "fallback":
            fallback["aliases"] = ["Another irrelevant alias"]
        elif change == "new_fallback":
            taxonomy["topics"].append(catalog_item("Another unrelated candidate"))
        elif change == "relevant":
            taxonomy["topics"].append(catalog_item("deployment"))
        elif change == "selected":
            relevant["kind"] = "technology"
        elif change == "model":
            job.model = "previous-model"
        elif change == "editorial":
            article.editorial_revision += 1
        elif change == "content":
            article.summary += "Updated source evidence."
        session.flush()
        next_job = analysis.request_analysis(
            session, article.id, automatic=True, force=change == "force", taxonomy=taxonomy
        )
        assert (next_job is None) == (change in {"fallback", "wire_success"})


@pytest.mark.integration
def test_per_call_ledger_is_idempotent_and_content_free(database):
    from devfeed_core.inference_usage import record_call
    from devfeed_core.models import InferenceCall, utcnow
    from sqlalchemy import select

    row = dict(
        id=uuid.uuid4(),
        started_at=utcnow(),
        finished_at=utcnow(),
        operation="source_relevance",
        job_id=None,
        attempt=1,
        reason="source_review",
        model="test-model",
        reasoning_effort=None,
        request_hash="a" * 64,
        status="failed",
        tokens={"inputTokens": 50},
        web_searches=2,
        duration_ms=10,
    )
    record_call(row)
    record_call(row)
    with database() as session:
        records = session.scalars(select(InferenceCall)).all()
        assert len(records) == 1 and records[0].web_searches == 2
        assert records[0].tokens == {"inputTokens": 50}


def test_cost_report_does_not_double_count_cache_or_reasoning():
    import importlib.util
    from decimal import Decimal
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "inference_usage_report", Path(__file__).parents[1] / "scripts/inference_usage.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    counts = {
        "inputTokens": 1_000_000,
        "cachedInputTokens": 500_000,
        "cacheWriteInputTokens": 100_000,
        "outputTokens": 100_000,
        "reasoningOutputTokens": 80_000,
        "web_searches": 3,
    }
    assert module.estimated_cost("gpt-5.6-terra", counts) == Decimal("2.38")
    assert module.estimated_cost("unknown-model", counts) is None
    assert (
        module.estimated_cost("gpt-5.6-terra", {**counts, "cachedInputTokens": 2_000_000}) is None
    )


def test_default_compaction_preserves_original_uuid_schema():
    from devfeed_core.analysis import analysis_output_schema

    taxonomy = {"topics": [catalog_item("Rust")], "tags": []}
    prompt, schema, identities = compact_request({"title": "Rust"}, taxonomy)
    assert schema == analysis_output_schema(taxonomy)
    assert taxonomy["topics"][0]["id"] in prompt
    assert identities["topics"] == {taxonomy["topics"][0]["id"]: taxonomy["topics"][0]["id"]}


def test_evidence_references_restore_only_supplied_source_passages():
    taxonomy = {"topics": [catalog_item("NVIDIA Dynamo")], "tags": []}
    snapshot = {
        "title": "Encode-prefill-decode disaggregation",
        "source_summary": "NVIDIA Dynamo serves multimodal models.",
        "text": "Vision encoding and LLM decoding scale independently.\n" * 40,
    }
    prompt, schema, mapping = compact_request(snapshot, taxonomy, evidence_refs=True)
    data = json.loads(prompt.rsplit("\n", 1)[1])
    reference = data["article"]["source_summary"][0]["id"]
    assert reference in schema["$defs"]["TopicSelection"]["properties"]["evidence"]["enum"]
    assert "Do not write quotes or invent IDs" in prompt
    for passage in mapping["evidence"].values():
        assert 4 <= len(passage) <= 500
        assert passage in " ".join(" ".join(snapshot.values()).split())
    output = {"topics": [{"topic_id": taxonomy["topics"][0]["id"], "evidence": reference}]}
    restored = restore_identities(output, mapping)
    assert restored["topics"][0]["evidence"] == snapshot["source_summary"]
    assert output["topics"][0]["evidence"] == reference
    output["topics"][0]["evidence"] = "A fabricated quote"
    with pytest.raises(InferenceValidationError, match="Unknown source passage"):
        restore_identities(output, mapping)
