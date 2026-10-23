"""Verify trusted master CI before publishing; wait for release images before packaging."""

import argparse
import json
import os
import re
import subprocess
import time
import tomllib
from pathlib import Path
from urllib.parse import quote, urlencode

NUMERIC = r"(?:0|[1-9][0-9]*)"
PRERELEASE_ID = rf"(?:{NUMERIC}|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
RELEASE_TAG = re.compile(
    rf"v{NUMERIC}\.{NUMERIC}\.{NUMERIC}(?:-{PRERELEASE_ID}(?:\.{PRERELEASE_ID})*)?"
)

WORKFLOWS = {
    "ci.yml": (
        "master",
        (
            "CI required",
            "Security checks / security / codeql (python)",
            "Security checks / security / codeql (actions)",
            "Security checks / JavaScript and TypeScript CodeQL",
        ),
    ),
    "release.yml": (None, ("Release required",)),
}


def api_pages(path):
    return json.loads(
        subprocess.check_output(["gh", "api", "--paginate", "--slurp", path], text=True, timeout=60)
    )


def api_object(path, api):
    pages = api(path)
    if len(pages) != 1 or not isinstance(pages[0], dict):
        raise ValueError(f"Expected one GitHub object for {path}")
    return pages[0]


def check_workflow_ci(repository, sha, branch, workflow, gates, api):
    definition = api_object(f"repos/{repository}/actions/workflows/{workflow}", api)
    if definition["path"] != f".github/workflows/{workflow}" or definition["state"] != "active":
        raise ValueError("Release proof must come from the active canonical workflow")
    query = urlencode({"head_sha": sha, "event": "push", "branch": branch, "per_page": 100})
    pages = api(f"repos/{repository}/actions/workflows/{workflow}/runs?{query}")
    runs = [
        run
        for page in pages
        for run in page["workflow_runs"]
        if run["head_sha"] == sha
        and run["event"] == "push"
        and run["head_branch"] == branch
        and run["head_repository"]["full_name"] == repository
        and run["workflow_id"] == definition["id"]
        and run["path"] == definition["path"]
    ]
    if not runs:
        return False
    # An old successful run must not hide a newer failed run or pending retry.
    run = max(runs, key=lambda run: run["id"])
    if run["status"] != "completed":
        return False
    if run["conclusion"] != "success":
        raise ValueError(f"{workflow} run {run['id']} ended with {run['conclusion']}")
    pages = api(
        f"repos/{repository}/actions/runs/{run['id']}/attempts/{run['run_attempt']}"
        "/jobs?per_page=100"
    )
    jobs = [job for page in pages for job in page["jobs"]]
    for name in gates:
        matched = [job for job in jobs if job["name"] == name]
        if len(matched) != 1 or any(
            job["head_sha"] != sha
            or job["run_id"] != run["id"]
            or job["status"] != "completed"
            or job["conclusion"] != "success"
            for job in matched
        ):
            raise ValueError(f"The exact release commit must have one successful {name} gate")
    return True


def check_master_ci(repository, sha, api=api_pages):
    branch, gates = WORKFLOWS["ci.yml"]
    return check_workflow_ci(repository, sha, branch, "ci.yml", gates, api)


def check_release_ci(repository, sha, tag, api=api_pages):
    _, gates = WORKFLOWS["release.yml"]
    return check_workflow_ci(repository, sha, tag, "release.yml", gates, api)


def verify_release_source(repository, sha, tag, api=api_pages, root=Path("."), env=os.environ):
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    if not RELEASE_TAG.fullmatch(tag):
        raise ValueError("A release requires a canonical vMAJOR.MINOR.PATCH[-PRERELEASE] tag")
    if tag != f"v{version}" or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("The release tag must match the checked-out application version")
    expected = {
        "GITHUB_REPOSITORY": repository,
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF_TYPE": "tag",
        "GITHUB_REF": f"refs/tags/{tag}",
        "GITHUB_SHA": sha,
    }
    if any(env.get(key) != value for key, value in expected.items()):
        raise ValueError("Release publication requires the repository's exact tag push event")
    checked_out = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, timeout=10
    ).strip()
    if checked_out != sha:
        raise ValueError("The checkout does not match the release commit")
    ref = api_object(f"repos/{repository}/git/ref/tags/{quote(tag, safe='')}", api)
    if ref["ref"] != f"refs/tags/{tag}":
        raise ValueError("The remote release reference must be a tag")
    target = ref["object"]
    # Support lightweight and annotated tags; reject cycles or unexpected object types.
    for _ in range(5):
        if target["type"] != "tag":
            break
        target = api_object(f"repos/{repository}/git/tags/{target['sha']}", api)["object"]
    if target["type"] != "commit" or target["sha"] != sha:
        raise ValueError("The remote release tag no longer matches the checked-out commit")
    comparison = api_object(f"repos/{repository}/compare/{sha}...master", api)
    if (
        comparison["status"] not in {"ahead", "identical"}
        or comparison["base_commit"]["sha"] != sha
    ):
        raise ValueError("The release commit must belong to master history")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source", action="store_true", help="Verify master CI before publishing")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.source:
        verify_release_source(args.repository, args.sha, args.tag)
    deadline = time.monotonic() + args.timeout
    while True:
        ready = (
            check_master_ci(args.repository, args.sha)
            if args.source
            else check_release_ci(args.repository, args.sha, args.tag)
        )
        if ready:
            if args.source:
                # Do not publish if the tag changed while master checks were running.
                verify_release_source(args.repository, args.sha, args.tag)
            proof = "master CI" if args.source else "release images"
            print(f"Verified {proof} for {args.tag} at {args.sha}")
            return
        if time.monotonic() >= deadline:
            parser.exit(1, "Timed out waiting for successful checks on the exact release commit\n")
        print(
            "Waiting for trusted master CI..." if args.source else "Waiting for release images...",
            flush=True,
        )
        time.sleep(min(15, max(0, deadline - time.monotonic())))


if __name__ == "__main__":
    main()
