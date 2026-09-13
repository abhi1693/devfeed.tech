import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "model_benchmark", Path(__file__).parents[1] / "scripts/model_benchmark.py"
)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def plan():
    return benchmark.Plan.model_validate(
        {
            "baseline": "base",
            "variants": [
                {"id": "base", "model": "baseline", "effort": "medium", "rates": [2, 0.2, 2.5, 12]},
                {
                    "id": "candidate",
                    "model": "candidate",
                    "effort": "low",
                    "rates": [0.2, 0.02, 0.25, 1.2],
                },
            ],
            "repeats": 1,
            "pricing_checked": "2026-09-13",
            "pricing_source": "test",
            "min_reviewed_cases": 1,
        }
    )


def case():
    return benchmark.Case.model_validate(
        {
            "id": "one",
            "task": "classify",
            "domain": "databases",
            "prompt": "Classify SQL",
            "schema": {"type": "object"},
            "expected": {"relevant": True},
            "label_provenance": "human:test",
        }
    )


def test_dry_run_and_price_ceiling(tmp_path):
    assert benchmark.run(plan(), [case()], tmp_path)["calls"] == 2
    assert not list(tmp_path.iterdir())
    data = plan().model_dump()
    data["variants"][1]["rates"] = [5, 0.5, 5, 30]
    with pytest.raises(ValueError, match="price ceiling"):
        benchmark.Plan.model_validate(data)
    data = plan().model_copy(update={"max_calls": 1})
    with pytest.raises(ValueError, match="max_calls"):
        benchmark.run(data, [case()], tmp_path)


def test_resume_grading_and_paired_review(tmp_path, monkeypatch):
    calls = []

    async def invoke(c, v, folder, timeout):
        calls.append(v.id)
        return {
            "exit": 0,
            "seconds": 1,
            "output": {"relevant": v.id == "base"},
            "usage": {"input_tokens": 100, "output_tokens": 10, "cached_input_tokens": 50},
        }

    monkeypatch.setattr(benchmark, "invoke", invoke)
    monkeypatch.setattr(benchmark.subprocess, "check_output", lambda *a, **kw: "test")
    summary = benchmark.run(plan(), [case()], tmp_path, True)
    assert summary["comparisons"][0]["decision"] == "quality_failures"
    benchmark.run(plan(), [case()], tmp_path, True)
    assert len(calls) == 2
    queue = benchmark.read(tmp_path / "review-queue.json")
    assert all("variant" not in row for row in queue)
    for row in queue:
        benchmark.write(
            tmp_path / "reviews" / f"{row['review_id']}.json",
            {"reviewer": "tester", "pass": row["output"]["relevant"]},
        )
    summary = benchmark.report(tmp_path)
    assert summary["comparisons"][0]["decision"] == "observed_regression"
    changed = case().model_copy(update={"prompt": "changed"})
    with pytest.raises(ValueError, match="Resume rejected"):
        benchmark.run(plan(), [changed], tmp_path, True)


def test_interrupted_call_is_not_reissued(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark.subprocess, "check_output", lambda *a, **kw: "test")
    benchmark.write(tmp_path / "one--base--0" / "started.json", {})
    calls = []

    async def invoke(c, v, folder, timeout):
        calls.append(v.id)
        raise TimeoutError

    monkeypatch.setattr(benchmark, "invoke", invoke)
    summary = benchmark.run(plan(), [case()], tmp_path, True)
    assert calls == ["candidate"]
    assert sum(g["errors"] for g in summary["groups"] if g["dimension"] == "all") == 2
    assert all(g["unmetered_calls"] == g["calls"] for g in summary["groups"])


def test_cost_and_gold_labels():
    assert benchmark.cost({}, [2, 0.2, 2.5, 12]) is None
    assert (
        benchmark.cost(
            {"input_tokens": 100, "output_tokens": 10, "cached_input_tokens": 101},
            [2, 0.2, 2.5, 12],
        )
        is None
    )
    assert benchmark.grade(case(), {"relevant": False}) == {"gold:relevant": False}
    assert (
        benchmark.cost(
            {
                "input_tokens": 1_000_000,
                "cached_input_tokens": 500_000,
                "output_tokens": 100_000,
                "reasoning_output_tokens": 80_000,
            },
            [2, 0.2, 2.5, 12],
        )
        == 2.3
    )


def test_gold_requires_provenance_and_dataset_ids_are_unique(tmp_path):
    invalid = case().model_dump(by_alias=True)
    invalid["label_provenance"] = None
    with pytest.raises(ValueError, match="provenance"):
        benchmark.Case.model_validate(invalid)
    benchmark.write(tmp_path / "cases.json", [case().model_dump(by_alias=True)] * 2)
    with pytest.raises(ValueError, match="unique"):
        benchmark.load_cases(tmp_path / "cases.json")


def test_transport_configuration_disables_tools():
    cmd = benchmark.command(plan().variants[0], Path("schema"), Path("output"))
    assert 'web_search="disabled"' in cmd
    assert "features.shell_tool=false" in cmd
    assert "features.apps=false" in cmd
    assert "--ignore-user-config" in cmd
    assert cmd[-1] == "-"
