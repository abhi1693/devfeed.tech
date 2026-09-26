"""Wait for successful tag CI and its required gate before publishing release assets."""

import argparse
import json
import subprocess
import time
from urllib.parse import urlencode


def api_pages(path):
    return json.loads(
        subprocess.check_output(["gh", "api", "--paginate", "--slurp", path], text=True, timeout=60)
    )


def check_release_ci(repository, sha, tag, api=api_pages):
    query = urlencode({"head_sha": sha, "event": "push", "per_page": 100})
    pages = api(f"repos/{repository}/actions/workflows/ci.yml/runs?{query}")
    runs = [
        run
        for page in pages
        for run in page["workflow_runs"]
        if run["head_sha"] == sha
        and run["event"] == "push"
        and run["head_branch"] == tag
        and run["head_repository"]["full_name"] == repository
    ]
    if not runs:
        return False
    # An old successful run must not hide a newer failed run or pending retry.
    run = max(runs, key=lambda run: run["id"])
    if run["status"] != "completed":
        return False
    if run["conclusion"] != "success":
        raise ValueError(f"Tag CI run {run['id']} ended with {run['conclusion']}")
    pages = api(
        f"repos/{repository}/actions/runs/{run['id']}/attempts/{run['run_attempt']}"
        "/jobs?per_page=100"
    )
    gates = [job for page in pages for job in page["jobs"] if job["name"] == "CI required"]
    if len(gates) != 1 or any(
        job["head_sha"] != sha or job["status"] != "completed" or job["conclusion"] != "success"
        for job in gates
    ):
        raise ValueError("The exact release commit must have one successful CI required gate")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    deadline = time.monotonic() + args.timeout
    while True:
        if check_release_ci(args.repository, args.sha, args.tag):
            print(f"Verified tag CI and CI required for {args.tag} at {args.sha}")
            return
        if time.monotonic() >= deadline:
            parser.exit(1, "Timed out waiting for successful CI on the exact release tag commit\n")
        print("Waiting for release-tag CI to finish...", flush=True)
        time.sleep(min(15, max(0, deadline - time.monotonic())))


if __name__ == "__main__":
    main()
