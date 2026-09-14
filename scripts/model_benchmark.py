"""Repeatable local model benchmarks. Never writes application data or changes routing."""

import argparse
import asyncio
import hashlib
import json
import os
import random
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from devfeed_aggregator.codex_client import INSTRUCTIONS
from devfeed_core.analysis import (
    AnalysisResult,
    analysis_output_schema,
    analysis_prompt,
    validate_evidence,
)
from devfeed_core.analysis_wire import compact_request, restore_identities
from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION = 1
DISABLED = [
    "shell_tool",
    "unified_exec",
    "shell_snapshot",
    "apps",
    "hooks",
    "plugins",
    "remote_plugin",
    "multi_agent",
    "multi_agent_v2",
    "code_mode",
    "code_mode_host",
    "browser_use",
    "browser_use_external",
    "in_app_browser",
    "computer_use",
    "image_generation",
    "view_image",
    "memories",
    "skill_search",
    "skill_mcp_dependency_install",
    "goals",
    "sleep_tool",
    "request_permissions_tool",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Case(Strict):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    task: str
    domain: str
    language: str = "unknown"
    difficulty: str = "unknown"
    split: str = "holdout"
    prompt: str
    schema_: dict = Field(alias="schema")
    validator: str = "json"
    context: dict = Field(default_factory=dict)
    expected: dict = Field(default_factory=dict)
    label_provenance: str | None = None

    @model_validator(mode="after")
    def labels(self):
        if self.expected and not self.label_provenance:
            raise ValueError("Expected labels require human label provenance")
        if self.validator not in {"json", "article"}:
            raise ValueError("Unsupported validator")
        return self


class Variant(Strict):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    model: str
    effort: str
    provider: Literal["codex", "openrouter", "gemini"] = "codex"
    structured: bool = True
    max_output_tokens: int = Field(default=4096, ge=1, le=32768)
    # USD per million input, cached input, cache-write input, output tokens.
    rates: tuple[float, float, float, float]


class Plan(Strict):
    version: int = VERSION
    baseline: str
    variants: list[Variant] = Field(min_length=1)
    seed: int = 42
    repeats: int = Field(default=2, ge=1, le=10)
    timeout: int = Field(default=180, ge=1, le=600)
    max_calls: int = Field(default=100, ge=1)
    min_interval_seconds: float = Field(default=0, ge=0, le=60)
    price_ceiling: tuple[float, float, float, float] = (2, 0.2, 2.5, 12)
    pricing_checked: str
    pricing_source: str
    min_reviewed_cases: int = Field(default=30, ge=1)

    @model_validator(mode="after")
    def safe_plan(self):
        ids = [v.id for v in self.variants]
        if self.version != VERSION or len(ids) != len(set(ids)) or self.baseline not in ids:
            raise ValueError("Invalid version, duplicate variants or missing baseline")
        for v in self.variants:
            if any(r < 0 or r > cap for r, cap in zip(v.rates, self.price_ceiling, strict=True)):
                raise ValueError(f"{v.id} exceeds configured price ceiling")
        return self


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def load_cases(path):
    cases = [Case.model_validate(row) for row in read(path)]
    if not cases or len({c.id for c in cases}) != len(cases):
        raise ValueError("Dataset must have unique case IDs and at least one case")
    return cases


def grade(case, output):
    checks = {}
    if case.validator == "article":
        if "wire_identities" in case.context:
            output = restore_identities(output, case.context["wire_identities"])
        parsed = AnalysisResult.model_validate(output)
        validate_evidence(parsed, case.context["input_snapshot"], case.context["catalog_snapshot"])
        checks["article_schema_and_quotes"] = True
    # Generic JSON tasks require a dedicated validator or human review for schema/semantics.
    for path, expected in case.expected.items():
        actual = output
        for part in path.split("."):
            actual = actual.get(part) if isinstance(actual, dict) else None
        checks[f"gold:{path}"] = actual == expected
    return checks


def cost(usage, rates):
    required = {"input_tokens", "output_tokens", "cached_input_tokens"}
    if not required <= usage.keys():
        return None
    inputs, output, cached = (
        usage[k] for k in ("input_tokens", "output_tokens", "cached_input_tokens")
    )
    writes = usage.get("cache_write_input_tokens", 0)
    if min(inputs, output, cached, writes) < 0 or cached + writes > inputs:
        return None
    return (
        (inputs - cached - writes) * rates[0]
        + cached * rates[1]
        + writes * rates[2]
        + output * rates[3]
    ) / 1_000_000


def command(variant, schema, output):
    cmd = [
        "codex",
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--skip-git-repo-check",
        "-s",
        "read-only",
        "-m",
        variant.model,
        "--json",
        "--output-schema",
        str(schema),
        "-o",
        str(output),
    ]
    config = {
        "model_reasoning_effort": variant.effort,
        "project_doc_max_bytes": 0,
        "web_search": "disabled",
        "service_tier": "default",
        "developer_instructions": INSTRUCTIONS,
    }
    config.update({f"features.{feature}": False for feature in DISABLED})
    for key, value in config.items():
        cmd.extend(["-c", f"{key}={json.dumps(value)}"])
    return [*cmd, "-"]


KEY_NAMES = {"openrouter": "OPENROUTER_API_KEY", "gemini": "GEMINI_API_KEY"}


def api_request(case, variant):
    key = os.environ[KEY_NAMES[variant.provider]]
    if variant.provider == "gemini":
        prompt = case.prompt
        config = {
            "responseMimeType": "application/json",
            "maxOutputTokens": variant.max_output_tokens,
            "thinkingConfig": {"thinkingLevel": variant.effort.upper()},
        }
        if variant.structured:
            config["responseJsonSchema"] = case.schema_
        else:
            prompt += "\nReturn only JSON matching this schema:\n" + json.dumps(case.schema_)
        return (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            + variant.model
            + ":generateContent",
            {"x-goog-api-key": key},
            {
                "systemInstruction": {"parts": [{"text": INSTRUCTIONS}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": config,
            },
        )
    endpoint = "https://openrouter.ai/api/v1/chat/completions"
    prompt = case.prompt
    if not variant.structured:
        prompt += "\nReturn only JSON matching this schema:\n" + json.dumps(case.schema_)
    payload = {
        "model": variant.model,
        "messages": [
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": variant.max_output_tokens,
    }
    if variant.structured:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "devfeed_analysis", "strict": True, "schema": case.schema_},
        }
    payload["provider"] = {"allow_fallbacks": False, "require_parameters": True}
    payload["reasoning"] = {"effort": variant.effort, "exclude": True}
    return endpoint, {"Authorization": "Bearer " + key}, payload


def api_response(data, provider):
    # Providers can return an error envelope after sending HTTP 200 keep-alives.
    # Preserve only the numeric code, never arbitrary error text that could echo inputs.
    if data.get("error"):
        error = data["error"]
        code = error.get("code") if isinstance(error, dict) else None
        return {
            "usage": {},
            "output": None,
            "error": "provider_error",
            "provider_error_code": code if isinstance(code, int) else None,
        }
    if provider == "gemini":
        usage = data.get("usageMetadata", {})
        tokens = {}
        if "promptTokenCount" in usage and "candidatesTokenCount" in usage:
            tokens = {
                "input_tokens": usage["promptTokenCount"],
                "cached_input_tokens": usage.get("cachedContentTokenCount", 0),
                "output_tokens": usage["candidatesTokenCount"] + usage.get("thoughtsTokenCount", 0),
                "reasoning_output_tokens": usage.get("thoughtsTokenCount", 0),
            }
        choice = (data.get("candidates") or [{}])[0]
        content = "".join(
            p.get("text", "")
            for p in choice.get("content", {}).get("parts", [])
            if not p.get("thought")
        )
        finish = choice.get("finishReason")
        model = data.get("modelVersion")
    else:
        usage = data.get("usage", {})
        tokens = {}
        if "prompt_tokens" in usage and "completion_tokens" in usage:
            tokens = {
                "input_tokens": usage["prompt_tokens"],
                "cached_input_tokens": (usage.get("prompt_tokens_details") or {}).get(
                    "cached_tokens", 0
                ),
                "output_tokens": usage["completion_tokens"],
                "reasoning_output_tokens": (usage.get("completion_tokens_details") or {}).get(
                    "reasoning_tokens", 0
                ),
            }
        choice = (data.get("choices") or [{}])[0]
        content = choice.get("message", {}).get("content") or ""
        finish = choice.get("finish_reason")
        model = data.get("model")
    result = {
        "usage": tokens,
        "output": None,
        "finish_reason": finish,
        "returned_model": model,
        "error": None,
        "raw_output": content,
    }
    try:
        result["output"] = json.loads(content)
    except (ValueError, TypeError):
        result["error"] = "invalid_json"
    if finish is None and not content:
        result["error"] = "missing_completion"
    elif finish not in {"stop", "STOP"}:
        result["error"] = "incomplete_output"
    return result


async def invoke_api(case, variant, timeout):
    url, headers, payload = api_request(case, variant)
    start = time.monotonic()
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.post(url, headers=headers, json=payload)
    result = {
        "exit": 0 if response.is_success else 1,
        "http_status": response.status_code,
        "seconds": time.monotonic() - start,
        "usage": {},
        "output": None,
        "error": None,
    }
    if response.is_success:
        result.update(api_response(response.json(), variant.provider))
    else:
        result["error"] = f"http_{response.status_code}"
    return result


async def invoke(case, variant, folder, timeout):
    if variant.provider != "codex":
        return await asyncio.wait_for(invoke_api(case, variant, timeout), timeout)
    schema, output = folder / "schema.json", folder / "output.json"
    write(schema, case.schema_)
    start = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *command(variant, schema, output),
        cwd=folder,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(case.prompt.encode()), timeout)
    except (TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.communicate()
        raise
    usage = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
            if "usage" in event:
                usage = event["usage"]
        except (ValueError, TypeError):
            continue
    return {
        "exit": process.returncode,
        "seconds": time.monotonic() - start,
        "usage": usage,
        "output": read(output) if output.exists() else None,
    }


def run(plan, cases, destination, execute=False):
    count = len(cases) * len(plan.variants) * plan.repeats
    if count > plan.max_calls:
        raise ValueError(f"Planned {count} calls exceeds max_calls={plan.max_calls}")
    manifest = {
        "harness_version": VERSION,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "plan": plan.model_dump(),
        "cases": [c.model_dump(by_alias=True) for c in cases],
    }
    manifest["fingerprint"] = digest(manifest)
    if not execute:
        return {"calls": count, "fingerprint": manifest["fingerprint"], "executed": False}
    missing = sorted(
        {
            KEY_NAMES[v.provider]
            for v in plan.variants
            if v.provider != "codex" and not os.environ.get(KEY_NAMES[v.provider])
        }
    )
    if missing:
        raise ValueError("Missing credentials: " + ", ".join(missing))
    destination = Path(destination).resolve()
    if (destination / "manifest.json").exists():
        if read(destination / "manifest.json")["fingerprint"] != manifest["fingerprint"]:
            raise ValueError("Resume rejected: dataset, prompt, schema or plan changed")
    else:
        manifest["created_at"] = datetime.now(UTC).isoformat()
        manifest["git_sha"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
        manifest["git_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"]))
        manifest["codex_version"] = subprocess.check_output(
            ["codex", "--version"], text=True
        ).strip()
        write(destination / "manifest.json", manifest)
    work = [(c, v, r) for c in cases for v in plan.variants for r in range(plan.repeats)]
    random.Random(plan.seed).shuffle(work)
    last_call_started = 0.0
    for case, variant, repeat in work:
        key = f"{case.id}--{variant.id}--{repeat}"
        folder = destination / key
        result_path = folder / "result.json"
        if result_path.exists():
            continue
        # Never silently rerun interrupted calls: they may have incurred cost.
        if (folder / "started.json").exists():
            continue
        remaining = plan.min_interval_seconds - (time.monotonic() - last_call_started)
        if remaining > 0:
            time.sleep(remaining)
        last_call_started = time.monotonic()
        write(folder / "started.json", {"at": datetime.now(UTC).isoformat()})
        result = {
            "case": case.id,
            "variant": variant.id,
            "repeat": repeat,
            "checks": {},
            "error": None,
            "usage": {},
            "output": None,
        }
        attempt_started = time.monotonic()
        try:
            result.update(asyncio.run(invoke(case, variant, folder, plan.timeout)))
            if result.get("error"):
                raise ValueError("provider_failure")
            if result["exit"] != 0 or result["output"] is None:
                raise ValueError("transport_failed")
            result["checks"] = grade(case, result["output"])
        except Exception as exc:
            result["error"] = result.get("error") or type(exc).__name__
        result.setdefault("seconds", time.monotonic() - attempt_started)
        result["api_equivalent_usd"] = cost(result["usage"], variant.rates)
        write(result_path, result)
        if result.get("http_status", 0) >= 400 or result.get("error") in {
            "TimeoutError",
            "ReadTimeout",
            "provider_error",
            "missing_completion",
        }:
            # Stop on provider failures, preserving unattempted work and quota.
            break
    return report(destination)


def report(destination):
    destination = Path(destination)
    manifest = read(destination / "manifest.json")
    plan = Plan.model_validate(manifest["plan"])
    cases = [Case.model_validate(c) for c in manifest["cases"]]
    rows, review = [], []
    for case in cases:
        for variant in plan.variants:
            for repeat in range(plan.repeats):
                key = f"{case.id}--{variant.id}--{repeat}"
                path = destination / key / "result.json"
                result = read(path) if path.exists() else {"error": "unfinished", "usage": {}}
                review_id = digest([manifest["fingerprint"], key])[:24]
                review_path = destination / "reviews" / f"{review_id}.json"
                human = read(review_path) if review_path.exists() else {}
                if human and (not human.get("reviewer") or type(human.get("pass")) is not bool):
                    raise ValueError("Review requires reviewer and boolean pass")
                rows.append(
                    {
                        **result,
                        "case": case.id,
                        "variant": variant.id,
                        "task": case.task,
                        "domain": case.domain,
                        "language": case.language,
                        "difficulty": case.difficulty,
                        "split": case.split,
                        "human": human,
                        "repeat": repeat,
                    }
                )
                review.append(
                    {
                        "review_id": review_id,
                        "case": case.id,
                        "task": case.task,
                        "domain": case.domain,
                        "prompt": case.prompt,
                        "output": result.get("output"),
                        "rubric": (
                            "Check factuality, supported claims, correct identities, relevance, "
                            "completeness and appropriate abstention. Any material error fails."
                        ),
                    }
                )
    groups = []
    for dimension in ("all", "task", "domain", "language", "difficulty", "split", "task_domain"):
        values = (
            {"all"}
            if dimension == "all"
            else {
                f"{r['task']}/{r['domain']}" if dimension == "task_domain" else r[dimension]
                for r in rows
            }
        )
        for value in sorted(values):
            for variant in plan.variants:
                subset = [
                    r
                    for r in rows
                    if r["variant"] == variant.id
                    and (
                        dimension == "all"
                        or (
                            f"{r['task']}/{r['domain']}"
                            if dimension == "task_domain"
                            else r[dimension]
                        )
                        == value
                    )
                ]
                reviewed = [r for r in subset if r["human"]]
                costs = [
                    r["api_equivalent_usd"]
                    for r in subset
                    if r.get("api_equivalent_usd") is not None
                ]
                latencies = [r["seconds"] for r in subset if "seconds" in r]
                groups.append(
                    {
                        "dimension": dimension,
                        "value": value,
                        "variant": variant.id,
                        "calls": len(subset),
                        "errors": sum(bool(r.get("error")) for r in subset),
                        "automatic_failures": sum(
                            not all(r.get("checks", {}).values()) for r in subset
                        ),
                        "reviewed_calls": len(reviewed),
                        "reviewed_cases": len({r["case"] for r in reviewed}),
                        "human_failures": sum(not r["human"]["pass"] for r in reviewed),
                        "known_api_equivalent_usd": sum(costs),
                        "unmetered_calls": len(subset) - len(costs),
                        "median_seconds": statistics.median(latencies) if latencies else None,
                        "tokens": {
                            key: sum(r.get("usage", {}).get(key, 0) for r in subset)
                            for key in (
                                "input_tokens",
                                "cached_input_tokens",
                                "cache_write_input_tokens",
                                "output_tokens",
                                "reasoning_output_tokens",
                            )
                        },
                        "decision": "human_review_required",
                    }
                )
    comparisons = []
    for group in groups:
        if group["dimension"] != "task_domain" or group["variant"] == plan.baseline:
            continue
        selected = [r for r in rows if f"{r['task']}/{r['domain']}" == group["value"]]
        baseline = {(r["case"], r["repeat"]): r for r in selected if r["variant"] == plan.baseline}
        pairs = [
            (baseline[(r["case"], r["repeat"])], r)
            for r in selected
            if r["variant"] == group["variant"]
            and r["human"]
            and baseline[(r["case"], r["repeat"])]["human"]
        ]
        regressions = sum(a["human"]["pass"] and not b["human"]["pass"] for a, b in pairs)
        improvements = sum(not a["human"]["pass"] and b["human"]["pass"] for a, b in pairs)
        distinct = len({a["case"] for a, _ in pairs})
        decision = "insufficient_evidence"
        if regressions:
            decision = "observed_regression"
        elif group["errors"]:
            decision = "operational_failures"
        elif group["human_failures"] or group["automatic_failures"]:
            decision = "quality_failures"
        elif (
            distinct >= plan.min_reviewed_cases
            and len(pairs) == group["calls"]
            and not group["errors"]
            and not group["automatic_failures"]
            and all(b["split"] == "holdout" for _, b in pairs)
        ):
            decision = "eligible_for_further_validation"
        comparisons.append(
            {
                "task_domain": group["value"],
                "variant": group["variant"],
                "paired_cases": distinct,
                "paired_calls": len(pairs),
                "regressions": regressions,
                "improvements": improvements,
                "decision": decision,
            }
        )
    random.Random(plan.seed).shuffle(review)
    write(destination / "review-queue.json", review)
    summary = {
        "fingerprint": manifest["fingerprint"],
        "baseline": plan.baseline,
        "groups": groups,
        "comparisons": comparisons,
        "promotion": "No automatic promotion; paired human review required",
    }
    write(destination / "summary.json", summary)
    lines = [
        "# Model benchmark",
        "",
        f"Dataset/run: `{manifest['fingerprint']}`",
        "",
        "Costs are known API equivalents, not bills. Missing usage is not zero cost.",
        "Automatic validity is not semantic quality. Unreviewed slices remain inconclusive.",
        "",
        "| Slice | Variant | Calls | Errors | Check failures | Human reviewed / failed | USD |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for g in groups:
        lines.append(
            f"| {g['dimension']}:{g['value']} | {g['variant']} | {g['calls']} | "
            f"{g['errors']} | {g['automatic_failures']} | {g['reviewed_calls']} / "
            f"{g['human_failures']} | {g['known_api_equivalent_usd']:.4f} |"
        )
    lines.extend(["", "## Paired human comparisons", ""])
    for comparison in comparisons:
        lines.append(
            f"- {comparison['task_domain']} / {comparison['variant']}: "
            f"{comparison['decision']}; {comparison['paired_cases']} paired cases, "
            f"{comparison['regressions']} regressions."
        )
    (destination / "REPORT.md").write_text("\n".join(lines) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    runner = sub.add_parser("run")
    runner.add_argument("--plan", required=True)
    runner.add_argument("--cases", required=True)
    runner.add_argument("--out", required=True)
    runner.add_argument("--execute", action="store_true")
    reporter = sub.add_parser("report")
    reporter.add_argument("--out", required=True)
    prepare = sub.add_parser("prepare-articles")
    prepare.add_argument("--samples", required=True)
    prepare.add_argument("--out", required=True)
    prepare.add_argument("--evidence-refs", action="store_true")
    args = parser.parse_args()
    if args.action == "prepare-articles":
        cases = []
        for index, sample in enumerate(read(args.samples)):
            snapshot, catalog = sample["input_snapshot"], sample["catalog_snapshot"]
            context = {"input_snapshot": snapshot, "catalog_snapshot": catalog}
            if args.evidence_refs:
                prompt, schema, identities = compact_request(snapshot, catalog, evidence_refs=True)
                context["wire_identities"] = identities
            else:
                prompt, schema = analysis_prompt(snapshot, catalog), analysis_output_schema(catalog)
            cases.append(
                {
                    "id": f"article-{index}",
                    "task": "article_analysis",
                    "domain": "unclassified",
                    "prompt": prompt,
                    "schema": schema,
                    "validator": "article",
                    "context": context,
                }
            )
        write(args.out, cases)
        print(f"Prepared {len(cases)} cases; curate domain, difficulty, split and human labels.")
    elif args.action == "report":
        report(args.out)
        print(Path(args.out) / "REPORT.md")
    else:
        result = run(
            Plan.model_validate(read(args.plan)), load_cases(args.cases), args.out, args.execute
        )
        print(json.dumps(result if not args.execute else {"report": args.out}, indent=2))


if __name__ == "__main__":
    main()
