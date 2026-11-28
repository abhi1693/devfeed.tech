"""Compare pure catalog construction against an explicit local baseline checkout."""

import argparse
import gc
import importlib.util
import json
import platform
import statistics
import sys
import time
import uuid
from contextlib import nullcontext
from pathlib import Path

from devfeed_core import analysis


def fixture(count, mode):
    items = []
    for index in range(count):
        tag = mode == "tag-heavy" and index >= 4450
        items.append(
            {
                "id": str(uuid.UUID(int=index)),
                "name": f"Package {index}",
                "slug": f"package-{index}",
                "aliases": [] if tag else [f"Library {index}", f"pkg_{index}"],
                "keywords": []
                if tag
                else (
                    [
                        "python",
                        "programming language",
                        "distributed systems",
                        "kubernetes",
                        "container runtime",
                    ]
                    if mode != "unique"
                    else [f"Keyword {index} {j}" for j in range(5)]
                ),
                "description": None
                if tag
                else f"Package {index} supports distributed systems and Python developers.",
                "ai_description": None,
            }
        )
    return (
        {"topics": items[:4450], "tags": items[4450:]}
        if mode == "tag-heavy"
        else {"topics": items, "tags": []}
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=60_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--mode", choices=("all", "shared", "unique", "tag-heavy"), default="all")
    parser.add_argument(
        "--memory", action="store_true", help="Trace peak Python allocation separately"
    )
    args = parser.parse_args()
    if not 100 <= args.rows <= 100_000 or not 1 <= args.repeats <= 10:
        parser.error("Use 100–100000 rows and 1–10 repeats")
    source = args.baseline_root / "packages/core/src/analysis.py"
    spec = importlib.util.spec_from_file_location("analysis_baseline", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load baseline analysis module")
    baseline = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = baseline
    spec.loader.exec_module(baseline)
    for mode in ("shared", "unique", "tag-heavy") if args.mode == "all" else (args.mode,):
        taxonomy = fixture(args.rows, mode)
        reports = {}
        for label, module in (("baseline", baseline), ("candidate", analysis)):
            samples = []
            for _ in range(args.repeats):
                gc.collect()
                started = time.process_time()
                prepared = module._PreparedCatalog(taxonomy)
                samples.append(time.process_time() - started)
                del prepared
            reports[label] = {"median_cpu_seconds": statistics.median(samples), "samples": samples}
        print(
            json.dumps(
                {
                    "architecture": platform.machine(),
                    "rows": args.rows,
                    "mode": mode,
                    "reports": reports,
                }
            ),
            flush=True,
        )
        reports = {}
        encoded = json.dumps(taxonomy)
        for retained in (False, True):
            samples, peaks, inference_bytes = [], [], []
            for _ in range(args.repeats):
                gc.collect()
                if args.memory:
                    import tracemalloc

                    tracemalloc.start()
                started = time.process_time()
                gap = job_preparation(encoded, retained, trace=args.memory)
                samples.append(time.process_time() - started)
                if args.memory:
                    peaks.append(tracemalloc.get_traced_memory()[1])
                    inference_bytes.append(gap)
                    tracemalloc.stop()
            reports["job_retained" if retained else "apply_only"] = {
                "median_cpu_seconds": statistics.median(samples),
                "samples": samples,
                "peak_python_bytes": max(peaks) if peaks else None,
                "inference_python_bytes": max(inference_bytes) if inference_bytes else None,
            }
        print(json.dumps({"rows": args.rows, "mode": mode, "job_preparation": reports}), flush=True)


def job_preparation(encoded, retained, *, trace=False):
    """Two catalog reads and specialist ID checks, excluding SQL, RPC and persistence."""
    snapshot = {"title": "Package 4449 distributed systems and Python"}
    result = analysis.Classifications(
        developer_relevance="relevant",
        language="en",
        content_type="article",
        content_format="article",
        topics=[],
        tags=[],
    )
    with analysis.reuse_candidates() if retained else nullcontext():
        taxonomy = json.loads(encoded)
        shortlist = analysis.analysis_candidates(taxonomy, snapshot)
        # Model the inference gap: default scopes release the complete catalog,
        # while the opted-in owner holds one revision-validated generation.
        if not retained:
            del taxonomy
        if trace:
            import tracemalloc

            inference_bytes = tracemalloc.get_traced_memory()[0]
        for _ in range(9):
            analysis.validate_evidence(result, snapshot, shortlist)
        with analysis.reuse_candidates(reuse_existing=True):
            if not retained:
                taxonomy = json.loads(encoded)
            analysis.analysis_candidates(taxonomy, snapshot)
            analysis.validate_evidence(result, snapshot, taxonomy)
    return inference_bytes if trace else None


if __name__ == "__main__":
    main()
