"""Budgeted whole-workflow benchmarks. No inference without --execute; no DB writes."""

import argparse
import json
import math
import random
import re
import time
import uuid
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from devfeed_aggregator.codex_client import AnalysisError, CodexClient
from devfeed_core.analysis import snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.inference_routing import Route, route_for, total_tokens
from devfeed_core.models import TopicProposal
from devfeed_core.research_evidence import (
    VERIFICATION_VERSION,
    citation_key,
    citation_verified,
    normalized,
    verify_citations,
)
from devfeed_core.topic_decision_budget import initial_state, reserve, settle
from devfeed_core.topic_decisions import (
    VERSION,
    DecisionDeferred,
    fetch_bundle,
    run_decision,
)
from devfeed_core.topic_remediation import (
    TopicCorrectionResult,
    apply_topic_correction,
    correction_prompt,
)
from devfeed_core.topic_verification import (
    TopicVerificationResult,
    checked_verdict,
    topic_verified,
    verification_input,
    verification_prompt,
)
from model_benchmark import cost, digest, read, write


def frozen_checks(bundle, citations):
    # Legacy models quote substrings; do not require them to guess selector chunk boundaries.
    pages = {
        page["url"]: normalized(" ".join(s["text"] for s in page["sentences"]))
        for page in bundle["pages"]
    }
    return {
        "version": VERIFICATION_VERSION,
        "checks": {
            citation_key(url, quote): {
                "status": "verified"
                if normalized(quote) and normalized(quote) in pages.get(url, "")
                else "unverified"
            }
            for url, quote in citations
        },
    }


def legacy_decision(case, call, *, frozen):
    """Existing prompts/validators, up to three corrections, with no DB mutation.

    Frozen mode controls source differences. Live mode retains separate browsing
    and repeated HTTP citation verification. Neither counts weak evidence as rejection.
    """
    proposal = TopicProposal(
        id=uuid.UUID(case["proposal_id"]),
        proposed=deepcopy(case["topic"]),
        status="pending",
        evidence=[],
    )
    feedback = {}
    for attempt in range(3):
        snapshot = {
            "topic": deepcopy(proposal.proposed),
            "correction": {
                "proposal_id": str(proposal.id),
                "previous_job_id": None,
                "attempt": attempt,
                "feedback": feedback,
            },
        }
        job = SimpleNamespace(
            id=uuid.uuid4(),
            input_snapshot=snapshot,
            input_hash=snapshot_hash(proposal.proposed),
            result={},
        )
        try:
            result = TopicCorrectionResult.model_validate(
                call(
                    "legacy_research",
                    correction_prompt(snapshot),
                    TopicCorrectionResult.model_json_schema(),
                    web=True,
                )
            )
            if apply_topic_correction(proposal, job, result) != "enriched":
                feedback = {"reason": "Insufficient evidence"}
                continue
            citations = [(source.url, source.quote) for source in result.sources]
            evidence = (
                frozen_checks(case["evidence"], citations)
                if frozen
                else verify_citations(citations)
            )
            if not all(citation_verified(evidence, *source) for source in citations):
                feedback = {"reason": "Research quotations could not be verified"}
                continue
            item = verification_input(proposal)
            check = checked_verdict(
                call(
                    "legacy_verification",
                    verification_prompt(item),
                    TopicVerificationResult.model_json_schema(),
                    web=True,
                ),
                item,
            )
            cited = [(source["url"], source["quote"]) for source in check["check"]["sources"]]
            evidence = (
                frozen_checks(case["evidence"], [*citations, *cited])
                if frozen
                else verify_citations([*citations, *cited])
            )
            if topic_verified(check, proposal, evidence):
                return {"decision": "approved", "topic": proposal.proposed, "verification": check}
            verdict = check["check"]
            if (
                verdict["verdict"] == "unsupported"
                and verdict["relevance"]["verdict"] == "out_of_scope"
                and verdict["relevance"]["sources"]
                and all(
                    field["supported"]
                    for field in verdict["fields"]
                    if field["field"] in {"name", "slug"}
                )
                and all(citation_verified(evidence, *source) for source in cited)
            ):
                return {"decision": "rejected", "topic": proposal.proposed, "verification": check}
            feedback = {"verification": verdict}
        except ValueError:
            feedback = {"reason": "Schema or evidence validation failed"}
    raise DecisionDeferred("legacy_review_unresolved")


def summarize(rows, reviews=()):
    """Human reviews bind to exact outputs; model agreement is not ground truth."""
    indexed = {
        (r["id"], r["variant"], r["repeat"], r["output_hash"]): r
        for r in reviews
        if r.get("reviewer")
        and isinstance(r.get("acceptable"), bool)
        and type(r.get("factual_errors")) is int
        and r["factual_errors"] >= 0
    }
    groups = defaultdict(list)
    for row in rows:
        groups[(row["variant"], row["domain"])].append(row)
        groups[(row["variant"], "all")].append(row)
    output = []
    for (variant, domain), items in sorted(groups.items()):
        completed = sum(r["decision"] in {"approved", "rejected"} for r in items)
        seconds, tokens = sum(r["seconds"] for r in items), sum(r["tokens"] for r in items)
        reviewed = [
            indexed[key]
            for r in items
            if (key := (r["id"], r["variant"], r["repeat"], r["output_hash"])) in indexed
        ]
        unique_reviewed = len({r["id"] for r in reviewed})
        costs = [r["api_equivalent_usd"] for r in items]
        output.append(
            {
                "variant": variant,
                "domain": domain,
                "cases": len(items),
                "completed": completed,
                "deferred": len(items) - completed,
                "decisions_per_hour": completed * 3600 / seconds if seconds else None,
                "tokens_including_unfinished": tokens,
                "tokens_per_decision_including_unfinished": tokens / completed
                if completed
                else None,
                "calls": sum(r["calls"] for r in items),
                "escalations": sum(r["escalations"] for r in items),
                "api_equivalent_usd": sum(costs) if all(v is not None for v in costs) else None,
                "unreported_calls": sum(r["unreported_calls"] for r in items),
                "reviewed_cases": unique_reviewed,
                "acceptable_fraction": sum(r["acceptable"] for r in reviewed) / len(reviewed)
                if reviewed
                else None,
                "factual_errors": sum(r["factual_errors"] for r in reviewed) if reviewed else None,
                "quality_gate": "needs_review"
                if unique_reviewed < (30 if domain == "all" else 10)
                else "fail"
                if any(not r["acceptable"] or r["factual_errors"] for r in reviewed)
                else "pass",
            }
        )
    return output


def execute(cases, plan, destination, *, frozen=True, client_factory=CodexClient):
    validate_plan(cases, plan)
    settings = get_settings().model_copy(
        update={
            "ai_enabled": True,
            "ai_bounded_topics_enabled": False,
            "ai_tiered_routing_enabled": True,
        }
    )
    rates = {v["model"]: v["rates"] for v in plan["models"]}
    jobs = [
        (case, variant, repeat)
        for case in cases
        for variant in plan["variants"]
        for repeat in range(plan["repeats"])
    ]
    random.Random(plan["seed"]).shuffle(jobs)
    results, spent_calls = [], 0
    write(
        destination / "manifest.json",
        {
            "workflow": VERSION,
            "cases_hash": digest(cases),
            "plan": plan,
            "implementation_hash": digest(
                {
                    path: (Path(__file__).parents[1] / path).read_text()
                    for path in (
                        "scripts/topic_workflow_benchmark.py",
                        "packages/core/src/topic_decisions.py",
                        "packages/core/src/topic_decision_budget.py",
                        "packages/core/src/inference_routing.py",
                    )
                }
            ),
            "evidence_mode": "frozen" if frozen else "live",
        },
    )
    for case, variant, repeat in jobs:
        if spent_calls >= plan["max_calls"]:
            break
        state = initial_state(settings)
        if variant["workflow"] == "legacy":
            state["limits"].update(calls=6, tokens=96000, searches=12, seconds=540)
        row_path = destination / f"{case['id']}-{variant['id']}-{repeat}.json"
        if row_path.exists():
            raise ValueError("Use a new report directory")
        started, actual_costs = time.perf_counter(), []

        def save(key, value, state=state, row_path=row_path):
            state[key] = value
            write(row_path.with_suffix(".state.json"), state)

        def call(
            stage,
            prompt,
            schema,
            *,
            web=False,
            escalated=False,
            state=state,
            variant=variant,
            case=case,
            actual_costs=actual_costs,
            save=save,
        ):
            nonlocal spent_calls
            if spent_calls >= plan["max_calls"]:
                raise DecisionDeferred("benchmark_call_budget_exhausted")
            state.update(reserve(state, stage, web=web and not frozen, escalated=escalated))
            reservation = state["calls"][-1]
            spent_calls += 1
            route = (
                Route(variant["model"], variant["effort"])
                if variant.get("model")
                else route_for(
                    settings.model_copy(
                        update={
                            "ai_fast_model": variant["fast_model"],
                            "ai_research_model": variant["research_model"],
                            "ai_escalation_model": variant["escalation_model"],
                        }
                    ),
                    {"discovery": "topic_discovery", "verification": "topic_verification"}.get(
                        stage, "topic_draft"
                    ),
                    quality_failure=escalated,
                )
            )
            if route.model not in rates:
                raise ValueError("Every route needs a reviewed price entry")
            client = client_factory(settings, usage_recorder=lambda _: None)
            client.route_override = route
            client.operation, client.reason = "benchmark_" + stage, VERSION
            client.token_limit = reservation["reserved_tokens"]
            client.search_limit = reservation["charged_searches"] if web else 0
            begin, succeeded = time.perf_counter(), False
            try:
                if frozen and (web or stage.startswith("legacy")):
                    prompt += "\nFrozen primary-source evidence (no tools):\n" + json.dumps(
                        case["evidence"]
                    )
                output = client.complete(prompt, schema, allow_web_search=web and not frozen)
                succeeded = True
                return output
            finally:
                usage = dict(client.usage)
                state.update(
                    settle(
                        state,
                        len(state["calls"]) - 1,
                        usage=usage,
                        searches=client.web_search_count,
                        seconds=time.perf_counter() - begin,
                        model=route.model,
                        effort=route.effort,
                        succeeded=succeeded,
                    )
                )
                normalized = {
                    target: usage[key]
                    for key, target in {
                        "inputTokens": "input_tokens",
                        "cachedInputTokens": "cached_input_tokens",
                        "outputTokens": "output_tokens",
                        "cacheWriteInputTokens": "cache_write_input_tokens",
                    }.items()
                    if key in usage
                }
                actual_costs.append(cost(normalized, rates[route.model]))
                save("calls", state["calls"])

        try:
            output = (
                legacy_decision(case, call, frozen=frozen)
                if variant["workflow"] == "legacy"
                else run_decision(
                    case["proposal_id"],
                    case["topic"],
                    state,
                    call=call,
                    save=save,
                    fetch=(lambda _, case=case: case["evidence"]) if frozen else fetch_bundle,
                    evidence_time=max(
                        datetime.fromisoformat(page["fetched_at"])
                        for page in case["evidence"]["pages"]
                    )
                    if frozen
                    else None,
                )
            )
        except Exception as exc:
            output = {
                "decision": "deferred",
                "reason": str(exc)
                if isinstance(exc, (DecisionDeferred, AnalysisError))
                else type(exc).__name__,
            }
        row = {
            "id": case["id"],
            "domain": case["domain"],
            "variant": variant["id"],
            "repeat": repeat,
            "decision": output["decision"],
            "output": output,
            "output_hash": digest(output),
            "seconds": time.perf_counter() - started,
            "tokens": sum(total_tokens(c.get("tokens", {})) for c in state["calls"]),
            "calls": len(state["calls"]),
            "escalations": sum(c["escalated"] for c in state["calls"]),
            "unreported_calls": sum(not c.get("usage_known", False) for c in state["calls"]),
            "api_equivalent_usd": sum(actual_costs)
            if all(c is not None for c in actual_costs)
            else None,
        }
        write(row_path, row)
        results.append(row)
        write(destination / "results.json", results)
    write(destination / "summary.json", summarize(results))
    write(
        destination / "completion.json",
        {
            "planned_workflows": len(jobs),
            "attempted_workflows": len(results),
            "unstarted_workflows": len(jobs) - len(results),
            "calls": spent_calls,
        },
    )
    write(
        destination / "review-template.json",
        [
            {
                "id": r["id"],
                "variant": r["variant"],
                "repeat": r["repeat"],
                "output_hash": r["output_hash"],
                "reviewer": "",
                "acceptable": None,
                "factual_errors": None,
                "notes": "",
            }
            for r in results
        ],
    )
    return results


def validate_plan(cases, plan):
    for entries in (cases, plan["variants"]):
        ids = [entry["id"] for entry in entries]
        if (
            not ids
            or len(set(ids)) != len(ids)
            or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", value) for value in ids)
        ):
            raise ValueError("Cases and variants require unique safe IDs")
    if not 1 <= plan["max_calls"] <= 10000 or not 1 <= plan["repeats"] <= 20:
        raise ValueError("Explicit positive call and repeat limits are required")
    ceiling = plan["price_ceiling"]
    if len(ceiling) != 4 or any(not math.isfinite(v) or v < 0 for v in ceiling):
        raise ValueError("Four finite nonnegative price ceilings are required")
    models = {entry["model"] for entry in plan["models"]}
    for entry in plan["models"]:
        if len(entry["rates"]) != 4 or any(
            not math.isfinite(rate) or not 0 <= rate <= cap
            for rate, cap in zip(entry["rates"], ceiling, strict=True)
        ):
            raise ValueError("A model exceeds the configured price ceiling")
    for variant in plan["variants"]:
        if variant["workflow"] not in {"legacy", "bounded"}:
            raise ValueError("Unknown workflow")
        routes = (
            [variant["model"]]
            if variant.get("model")
            else [variant[key] for key in ("fast_model", "research_model", "escalation_model")]
        )
        if any(route not in models for route in routes):
            raise ValueError("Every route needs a reviewed price entry")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=Path("benchmarks/topic-workflows.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--live-evidence", action="store_true")
    parser.add_argument("--reviews", type=Path)
    args = parser.parse_args()
    if args.reviews:
        write(
            args.output / "summary.json",
            summarize(read(args.output / "results.json"), read(args.reviews)),
        )
        return
    cases, plan = read(args.cases), read(args.plan)
    try:
        validate_plan(cases, plan)
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    if not cases or len({case["id"] for case in cases}) != len(cases):
        parser.error("Cases must have unique IDs")
    if any(not case.get("domain") or not case.get("provenance") for case in cases):
        parser.error("Every case requires domain and source provenance")
    if not args.live_evidence and any(not case.get("evidence", {}).get("pages") for case in cases):
        parser.error("Frozen benchmarks require saved public evidence pages")
    if any(
        any(
            rate > cap or rate < 0
            for rate, cap in zip(model["rates"], plan["price_ceiling"], strict=True)
        )
        for model in plan["models"]
    ):
        parser.error("A model exceeds the configured price ceiling")
    if not args.execute:
        print(
            json.dumps(
                {
                    "cases": len(cases),
                    "variants": len(plan["variants"]),
                    "max_calls": plan["max_calls"],
                    "inference_started": False,
                }
            )
        )
        return
    if args.output.exists():
        parser.error("Choose a new report directory")
    execute(cases, plan, args.output, frozen=not args.live_evidence)


if __name__ == "__main__":
    main()
