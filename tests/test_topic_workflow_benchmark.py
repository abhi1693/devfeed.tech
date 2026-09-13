import importlib
from pathlib import Path

import pytest
from test_topic_decisions import IDENTIFIER, TOPIC, answer, bundle


@pytest.fixture
def benchmark(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    return importlib.import_module("topic_workflow_benchmark")


def test_frozen_workflow_benchmark_runs_every_stage_without_database(benchmark, tmp_path):
    evidence, calls = bundle(), []

    class Client:
        def __init__(self, settings, *, usage_recorder):
            assert callable(usage_recorder)
            self.usage = {"inputTokens": 100, "cachedInputTokens": 50, "outputTokens": 20}
            self.web_search_count = 0

        def complete(self, prompt, schema, *, allow_web_search):
            assert allow_web_search is False
            stage = {
                "DiscoveryResult": "discovery",
                "MinimalDraft": "draft",
                "EvidenceVerdict": "verification",
            }[schema["title"]]
            calls.append((stage, self.route_override.model, self.route_override.effort))
            return answer(stage, prompt, evidence)

    plan = benchmark.read(Path(__file__).parents[1] / "benchmarks/topic-workflows.json")
    plan.update(repeats=1, variants=[plan["variants"][-1]])
    cases = [
        {
            "id": "example",
            "proposal_id": IDENTIFIER,
            "domain": "tools",
            "topic": TOPIC,
            "evidence": evidence,
            "provenance": "synthetic test, not a model-quality claim",
        }
    ]
    rows = benchmark.execute(cases, plan, tmp_path, client_factory=Client)
    assert rows[0]["decision"] == "approved" and rows[0]["calls"] == 3
    assert rows[0]["tokens"] == 360 and rows[0]["api_equivalent_usd"] > 0
    assert [effort for _, _, effort in calls] == ["medium", "low", "medium"]
    assert benchmark.read(tmp_path / "summary.json")[0]["quality_gate"] == "needs_review"
    assert (tmp_path / "results.json").stat().st_mode & 0o777 == 0o600
    assert benchmark.read(tmp_path / "completion.json")["unstarted_workflows"] == 0
    assert benchmark.read(tmp_path / "manifest.json")["implementation_hash"]


def test_plan_rejects_unsafe_output_paths_and_nonfinite_prices(benchmark):
    plan = benchmark.read(Path(__file__).parents[1] / "benchmarks/topic-workflows.json")
    with pytest.raises(ValueError, match="safe IDs"):
        benchmark.validate_plan([{"id": "../escape"}], plan)
    plan["models"][0]["rates"][0] = float("nan")
    with pytest.raises(ValueError, match="price ceiling"):
        benchmark.validate_plan([{"id": "safe"}], plan)


def test_frozen_legacy_quotes_accept_exact_substrings_not_only_selector_chunks(benchmark):
    from devfeed_core.research_evidence import citation_verified

    evidence = bundle()
    quote = "a developer tool for building reliable applications."
    checks = benchmark.frozen_checks(evidence, [("https://example.com/", quote)])
    assert citation_verified(checks, "https://example.com/", quote)


def test_quality_gate_requires_distinct_reviewed_cases_and_exact_output_hashes(benchmark):
    rows = [
        {
            "id": str(i),
            "variant": "candidate",
            "repeat": 0,
            "domain": "tools",
            "output_hash": "actual",
            "decision": "approved",
            "seconds": 10,
            "tokens": 100,
            "calls": 3,
            "escalations": 0,
            "api_equivalent_usd": 0.01,
            "unreported_calls": 0,
        }
        for i in range(30)
    ]
    reviews = [
        {
            "id": str(i),
            "variant": "candidate",
            "repeat": 0,
            "output_hash": "actual",
            "reviewer": "independent-human",
            "acceptable": True,
            "factual_errors": 0,
        }
        for i in range(30)
    ]
    assert all(row["quality_gate"] == "pass" for row in benchmark.summarize(rows, reviews))
    reviews[0]["factual_errors"] = 1
    assert all(row["quality_gate"] == "fail" for row in benchmark.summarize(rows, reviews))
    for review in reviews:
        review["output_hash"] = "different-output"
    assert all(row["quality_gate"] == "needs_review" for row in benchmark.summarize(rows, reviews))


def test_unfinished_work_stays_in_cost_denominator_and_missing_usage_is_unknown(benchmark):
    rows = [
        {
            "id": "one",
            "variant": "candidate",
            "repeat": 0,
            "domain": "tools",
            "output_hash": "actual",
            "decision": "deferred",
            "seconds": 10,
            "tokens": 100,
            "calls": 3,
            "escalations": 1,
            "api_equivalent_usd": None,
            "unreported_calls": 1,
        }
    ]
    report = benchmark.summarize(rows)[0]
    assert report["completed"] == 0 and report["deferred"] == 1
    assert report["tokens_including_unfinished"] == 100
    assert report["tokens_per_decision_including_unfinished"] is None
    assert report["api_equivalent_usd"] is None
