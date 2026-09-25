"""Fail CodeQL reports except the reviewed Chrome Web Store package upload."""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXPECTED_RULE = "js/file-access-to-http"
EXPECTED_SOURCE = "scripts/publish_extension_stores.mjs"


def is_expected_extension_upload(result: dict, source: str) -> bool:
    """Accept only the suppressed, fixed-destination Chrome package upload."""
    locations = result.get("locations", [])
    if result.get("ruleId") != EXPECTED_RULE or len(locations) != 1:
        return False

    physical = locations[0].get("physicalLocation", {})
    artifact = physical.get("artifactLocation", {}).get("uri")
    region = physical.get("region", {})
    lines = source.splitlines()
    line_number = region.get("startLine")
    if artifact != EXPECTED_SOURCE or not isinstance(line_number, int):
        return False
    if not 1 <= line_number <= len(lines) or lines[line_number - 1].strip() != "body: archive,":
        return False

    # Keep the suppression tied to an explicit package path and fixed store URL.
    required_source = (
        'const CWS_API = "https://chromewebstore.googleapis.com";',
        "apps/extensions/dist/devfeed-chrome-extension-${version}.zip",
        "`${CWS_API}/upload/v2/${itemPath}:upload`",
        "// codeql[js/file-access-to-http]",
    )
    if any(fragment not in source for fragment in required_source):
        return False
    if not any(
        suppression.get("kind", suppression.get("@kind", "")).lower() == "insource"
        for suppression in result.get("suppressions", [])
    ):
        return False

    # Verify CodeQL's reported taint path starts at the versioned package read.
    read_line = next(
        (
            index
            for index, line in enumerate(lines, start=1)
            if "const chromeArchive = await readFile(" in line
        ),
        None,
    )
    if read_line is None:
        return False
    flows = result.get("codeFlows", [])
    return any(
        any(
            location.get("location", {})
            .get("physicalLocation", {})
            .get("region", {})
            .get("startLine")
            == read_line
            for location in thread_flow.get("locations", [])
        )
        for flow in flows
        for thread_flow in flow.get("threadFlows", [])
    )


def check_reports(directory: Path, source_path: Path) -> list[str]:
    reports = sorted(directory.rglob("*.sarif"))
    if not reports:
        return ["CodeQL did not produce SARIF reports"]

    source = source_path.read_text()
    errors: list[str] = []
    accepted = 0
    for report in reports:
        data = json.loads(report.read_text())
        runs = data.get("runs")
        if data.get("version") != "2.1.0" or not isinstance(runs, list) or not runs:
            errors.append(f"{report}: invalid SARIF report")
            continue
        for run in runs:
            if any(
                invocation.get("executionSuccessful") is False
                for invocation in run.get("invocations", [])
            ):
                errors.append(f"{report}: CodeQL analysis was unsuccessful")
            for result in run.get("results", []):
                if is_expected_extension_upload(result, source):
                    accepted += 1
                    continue
                location = result.get("locations", [{}])[0].get("physicalLocation", {})
                path = location.get("artifactLocation", {}).get("uri", "unknown path")
                line = location.get("region", {}).get("startLine", "?")
                errors.append(f"{report}: {result.get('ruleId', 'unknown rule')} at {path}:{line}")

    if accepted > 1:
        errors.append(f"CodeQL reported {accepted} copies of the approved extension upload flow")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("Usage: check_codeql_sarif.py <sarif-directory>", file=sys.stderr)
        return 2
    errors = check_reports(Path(args[0]), Path(EXPECTED_SOURCE))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("CodeQL findings passed the reviewed extension-upload exception")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
