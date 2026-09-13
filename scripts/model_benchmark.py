"""Repeatable local model benchmarks. Never writes application data or changes routing."""

import argparse
import asyncio
import hashlib
import json
import random
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from devfeed_aggregator.codex_client import INSTRUCTIONS
from devfeed_core.analysis import (
    AnalysisResult,
    analysis_output_schema,
    analysis_prompt,
    validate_evidence,
)
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


async def invoke(case, variant, folder, timeout):
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
    for case, variant, repeat in work:
        key = f"{case.id}--{variant.id}--{repeat}"
        folder = destination / key
        result_path = folder / "result.json"
        if result_path.exists():
            continue
        # Never silently rerun interrupted calls: they may have incurred cost.
        if (folder / "started.json").exists():
            continue
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
        try:
            result.update(asyncio.run(invoke(case, variant, folder, plan.timeout)))
            if result["exit"] != 0 or result["output"] is None:
                raise ValueError("transport_failed")
            result["checks"] = grade(case, result["output"])
        except Exception as exc:
            result["error"] = type(exc).__name__
        result["api_equivalent_usd"] = cost(result["usage"], variant.rates)
        write(result_path, result)
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
        elif group["human_failures"] or group["errors"] or group["automatic_failures"]:
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
    args = parser.parse_args()
    if args.action == "prepare-articles":
        cases = []
        for index, sample in enumerate(read(args.samples)):
            snapshot, catalog = sample["input_snapshot"], sample["catalog_snapshot"]
            cases.append(
                {
                    "id": f"article-{index}",
                    "task": "article_analysis",
                    "domain": "unclassified",
                    "prompt": analysis_prompt(snapshot, catalog),
                    "schema": analysis_output_schema(catalog),
                    "validator": "article",
                    "context": {"input_snapshot": snapshot, "catalog_snapshot": catalog},
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
