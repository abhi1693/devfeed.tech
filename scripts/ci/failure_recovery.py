"""Run disposable fault tests and reject incomplete reports before publication."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from scripts.ci.check_reports import check_junit
from scripts.ci.failure_services import disposable_failure_services

ROOT = Path(__file__).resolve().parents[2]
CASES = {
    "test_postgres_latency_preserves_reads": 2,
    "test_postgres_fault_returns_useful_errors_and_recovers": 4,
    "test_interrupted_transaction_preserves_committed_state": 2,
    "test_redis_latency_preserves_state": 1,
    "test_redis_connection_retries_are_bounded": 1,
    "test_redis_readiness_recovers": 2,
    "test_cache_outage_preserves_writes_and_recovers": 2,
    "test_failed_redis_dispatch_preserves_durable_job": 1,
    "test_lost_database_commit_does_not_duplicate_rq_delivery": 1,
}


def check_recovery_report(path):
    check_junit(path)
    tests = list(ET.parse(path).getroot().iter("testcase"))
    identities = {(test.get("classname"), test.get("name")) for test in tests}
    counts = Counter(test.get("name", "").split("[", 1)[0] for test in tests)
    if len(identities) != sum(CASES.values()) or counts != CASES:
        raise ValueError("Recovery report is missing, duplicating or adding unexpected cases")


def run_bounded(command, env, log, *, timeout=480):
    with log.open("w") as output:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            raise RuntimeError("Recovery suite exceeded its eight-minute budget") from None


def main():
    directory = ROOT / "reports/failure-recovery"
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)
    report = directory / "junit.xml"
    started = time.monotonic()
    passed = False
    try:
        with disposable_failure_services(directory) as env:
            status = run_bounded(
                [
                    "uv",
                    "run",
                    "--locked",
                    "--no-build",
                    "pytest",
                    "tests/test_failure_recovery.py",
                    "-m",
                    "integration and failure_recovery",
                    "-q",
                    "-s",
                    f"--junitxml={report}",
                ],
                env,
                directory / "pytest.log",
            )
            if status:
                print((directory / "pytest.log").read_text()[-12_000:], file=sys.stderr)
                raise RuntimeError(f"Recovery pytest exited with {status}; see pytest.log")
            check_recovery_report(report)
        passed = True
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError, ET.ParseError) as error:
        print(str(error), file=sys.stderr)
    finally:
        result = {
            "passed": passed,
            "expected_cases": sum(CASES.values()),
            "elapsed_seconds": round(time.monotonic() - started, 2),
        }
        (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        summary = (
            f"## Recovery tests\n\n**{'Passed' if passed else 'Failed'}** · "
            f"{sum(CASES.values())} required cases · {result['elapsed_seconds']} seconds.\n\n"
            "PostgreSQL/Redis latency, disconnects and bounded stalls; API errors/readiness, "
            "cache fallback, interrupted writes and durable RQ dispatch recovery.\n\n"
            "The artifact contains JUnit, pytest and service logs, pinned image references, "
            "proxy state and the result.\n\n"
            "Reproduce: `uv run --locked python -m scripts.ci.failure_recovery`.\n"
        )
        (directory / "summary.md").write_text(summary)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
                stream.write(summary)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
