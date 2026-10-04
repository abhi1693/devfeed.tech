"""Run API fuzzing only against containers provisioned by this invocation."""

import argparse
import json
import os
import secrets
import subprocess
import sys
import xml.etree.ElementTree as ET
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    from check_reports import check_junit
    from services import disposable_services

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("pr", "nightly"), default="pr")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    seed = (
        args.seed
        if args.seed is not None
        else (20261005 if args.profile == "pr" else secrets.randbits(32))
    )
    reports = ROOT / "reports/api-fuzz" / args.profile
    reports.mkdir(parents=True, exist_ok=True)
    junit = reports / "junit.xml"
    junit.unlink(missing_ok=True)
    (reports / "run.json").write_text(
        json.dumps(
            {"profile": args.profile, "seed": seed, "schemathesis": version("schemathesis")},
            indent=2,
        )
    )
    print(f"API fuzzing: profile={args.profile}, seed={seed}", flush=True)
    with disposable_services() as env, TemporaryDirectory(prefix="devfeed-fuzz-") as directory:
        env.update(
            DEVFEED_TEST_DATABASE_URL=env["DEVFEED_DATABASE_URL"],
            DEVFEED_TEST_REDIS_URL=env["DEVFEED_REDIS_URL"],
            DEVFEED_FUZZ_PROFILE=args.profile,
            DEVFEED_FUZZ_REPORTS=str(reports),
            OTEL_SDK_DISABLED="true",
        )
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(ROOT / "tests/fuzz"),
            str(ROOT / "tests/test_api_security_boundaries.py"),
            "-m",
            "integration",
            f"--hypothesis-seed={seed}",
            "--hypothesis-show-statistics",
            f"--junitxml={junit}",
        ]
        log = reports / "pytest.log"
        with log.open("w") as output:
            try:
                result = subprocess.run(
                    command,
                    cwd=directory,
                    env=env,
                    check=False,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=1800 if args.profile == "nightly" else 600,
                )
                code = result.returncode
            except subprocess.TimeoutExpired:
                output.write("\nAPI fuzzing exceeded its time budget.\n")
                code = 124
        print(log.read_text(), flush=True)
    if code == 0:
        try:
            check_junit(junit)
        except (ValueError, OSError, ET.ParseError) as exc:
            print(f"API fuzz report rejected: {exc}", file=sys.stderr)
            code = 1
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(step_summary).open("a") as summary:
            summary.write(
                f"### API fuzzing\n\nProfile: `{args.profile}`. Seed: `{seed}`. "
                f"Result: {'passed' if code == 0 else 'failed'}.\n\n"
                "Schemas, selected paths, seed, test statistics and JUnit reproductions "
                "are in the API fuzz artifact.\n"
            )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
