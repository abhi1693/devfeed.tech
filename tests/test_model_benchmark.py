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


def test_provider_requests_and_usage(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    variant = benchmark.Variant(
        id="free",
        model="nvidia/nemotron-3-ultra-550b-a55b:free",
        effort="medium",
        provider="openrouter",
        structured=False,
        rates=(0, 0, 0, 0),
    )
    url, headers, payload = benchmark.api_request(case(), variant)
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert payload["provider"]["allow_fallbacks"] is False
    assert "response_format" not in payload
    assert "schema" in payload["messages"][1]["content"]
    result = benchmark.api_response(
        {
            "choices": [{"finish_reason": "stop", "message": {"content": '{"relevant":true}'}}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 30,
                "completion_tokens_details": {"reasoning_tokens": 20},
            },
        },
        "openrouter",
    )
    assert result["output"] == {"relevant": True}
    assert result["usage"]["output_tokens"] == 30
    gemini = benchmark.api_response(
        {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [
                            {"text": "hidden", "thought": True},
                            {"text": '{"relevant":true}'},
                        ]
                    },
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 100,
                "candidatesTokenCount": 10,
                "thoughtsTokenCount": 20,
            },
        },
        "gemini",
    )
    assert gemini["usage"]["output_tokens"] == 30
    assert gemini["output"] == {"relevant": True}


def test_invalid_output_retains_usage_and_missing_key_stops_before_calls(tmp_path, monkeypatch):
    result = benchmark.api_response(
        {
            "choices": [{"finish_reason": "length", "message": {"content": "{broken"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30},
        },
        "openrouter",
    )
    assert result["error"] == "incomplete_output"
    assert result["usage"]["output_tokens"] == 30
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    value = plan()
    value.variants[0].provider = "openrouter"
    with pytest.raises(ValueError, match="Missing credentials"):
        benchmark.run(value, [case()], tmp_path, True)
    assert not list(tmp_path.iterdir())


def test_http_quota_failure_stops_campaign(tmp_path, monkeypatch):
    import httpx

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429, json={"error": {"message": "quota exhausted"}})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        benchmark.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    monkeypatch.setattr(benchmark.subprocess, "check_output", lambda *a, **kw: "test")
    value = plan()
    for variant in value.variants:
        variant.provider = "openrouter"
    benchmark.run(value, [case()], tmp_path, True)
    assert len(requests) == 1
    results = list(tmp_path.glob("*/result.json"))
    assert len(results) == 1
    assert benchmark.read(results[0])["error"] == "http_429"
    assert "test-only" not in (tmp_path / "manifest.json").read_text()


def test_gemini_and_openrouter_request_contracts(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    variant = (
        plan().variants[0].model_copy(update={"provider": "gemini", "model": "gemini-3.8-flash"})
    )
    url, headers, payload = benchmark.api_request(case(), variant)
    assert "?" not in url
    assert payload["generationConfig"]["responseJsonSchema"] == case().schema_
    assert payload["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "MEDIUM"
    variant.provider = "openrouter"
    url, headers, payload = benchmark.api_request(case(), variant)
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["reasoning"]["effort"] == "medium"
    assert "tools" not in payload


def test_api_has_total_deadline_even_if_provider_keeps_connection_alive(monkeypatch, tmp_path):
    import asyncio

    async def never_finishes(*args):
        await asyncio.sleep(10)

    monkeypatch.setattr(benchmark, "invoke_api", never_finishes)
    variant = plan().variants[0].model_copy(update={"provider": "openrouter"})
    with pytest.raises(TimeoutError):
        asyncio.run(benchmark.invoke(case(), variant, tmp_path, 0.01))


def test_http_200_provider_error_is_not_a_quality_failure():
    result = benchmark.api_response(
        {"error": {"code": 503, "message": "sensitive body"}}, "openrouter"
    )
    assert result["error"] == "provider_error"
    assert result["provider_error_code"] == 503
    assert "sensitive" not in str(result)
    assert benchmark.api_response({}, "openrouter")["error"] == "missing_completion"


def test_gemini_json_mode_keeps_complete_schema_in_prompt(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    variant = plan().variants[0].model_copy(update={"provider": "gemini", "structured": False})
    _, _, payload = benchmark.api_request(case(), variant)
    assert "responseJsonSchema" not in payload["generationConfig"]
    prompt = payload["contents"][0]["parts"][0]["text"]
    assert prompt.startswith(case().prompt)
    assert benchmark.json.loads(prompt.split("schema:\n", 1)[1]) == case().schema_
    assert payload["generationConfig"]["responseMimeType"] == "application/json"


def test_groq_is_not_an_available_provider():
    with pytest.raises(ValueError):
        benchmark.Variant(
            id="removed", model="removed", effort="medium", provider="groq", rates=(0, 0, 0, 0)
        )


def test_request_pacing_does_not_issue_back_to_back_calls(tmp_path, monkeypatch):
    delays = []
    monkeypatch.setattr(benchmark.time, "sleep", delays.append)
    monkeypatch.setattr(benchmark.subprocess, "check_output", lambda *a, **kw: "test")

    async def invoke(*args):
        return {"exit": 0, "output": {"relevant": True}, "usage": {}}

    monkeypatch.setattr(benchmark, "invoke", invoke)
    value = plan().model_copy(update={"min_interval_seconds": 6})
    benchmark.run(value, [case()], tmp_path, True)
    assert len(delays) == 1 and 0 < delays[0] <= 6
