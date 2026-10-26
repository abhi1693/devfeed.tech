"""Regression coverage for offline API contracts and compatibility probes."""

import copy
import importlib.util
import json
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError

from tests.support.paths import REPO_ROOT

ROOT = REPO_ROOT
spec = importlib.util.spec_from_file_location("api_contracts", ROOT / "scripts/ci/api_contracts.py")
contracts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contracts)


def test_mcp_projection_preserves_recursive_inputs_outputs_and_namespaces():
    tool = {
        "name": "get_article",
        "inputSchema": {
            "type": "object",
            "properties": {"node": {"$ref": "#/$defs/Node"}},
            "required": ["node"],
            "additionalProperties": False,
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "children": {"type": "array", "items": {"$ref": "#/$defs/Node"}},
                    },
                    "required": ["id"],
                }
            },
        },
        "outputSchema": {
            "type": "object",
            "properties": {"node": {"$ref": "#/$defs/Node"}},
            "$defs": {"Node": {"type": "integer"}},
        },
    }
    other = copy.deepcopy(tool)
    other["name"] = "other_article"
    other["inputSchema"]["$defs"]["Node"]["properties"]["id"]["type"] = "integer"
    tools = [other, tool]
    original = copy.deepcopy(tools)
    document = contracts.tool_contract(tools, "Fixture")
    assert tools == original, "The original tools/list contracts must remain intact"
    operation = document["paths"]["/tools/get_article"]["post"]
    request = operation["requestBody"]["content"]["application/json"]["schema"]
    validator = Draft202012Validator({**request, "components": document["components"]})
    validator.validate({"node": {"id": "one", "children": [{"id": "two"}]}})
    for invalid in (
        {},
        {"node": {"id": ""}},
        {"node": {"id": 1}},
        {"node": {"id": "one"}, "extra": 1},
    ):
        with pytest.raises(ValidationError):
            validator.validate(invalid)
    response = operation["responses"]["200"]["content"]["application/json"]["schema"]
    response_validator = Draft202012Validator({**response, "components": document["components"]})
    response_validator.validate({"node": 3})
    with pytest.raises(ValidationError):
        response_validator.validate({"node": "not an integer"})
    assert len(document["components"]["schemas"]) == 4


def test_untyped_tool_output_is_preserved_as_unspecified():
    document = contracts.tool_contract([{"name": "untyped", "inputSchema": {}}], "Fixture")
    operation = document["paths"]["/tools/untyped"]["post"]
    assert operation["description"] == ""
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {}


def test_exports_all_services_and_both_mcp_registries_without_network(tmp_path, monkeypatch):
    def offline(*args, **kwargs):
        pytest.fail("Schema export attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", offline)
    contracts.export_contracts(tmp_path)
    for name in contracts.CONTRACTS:
        document = json.loads((tmp_path / f"{name}.json").read_text())
        assert document["openapi"].startswith("3.")
        assert document["paths"]
    public = json.loads((tmp_path / "mcp-public.tools.json").read_text())
    account = json.loads((tmp_path / "mcp-account.tools.json").read_text())
    assert "set_bookmark" not in {tool["name"] for tool in public}
    assert "set_bookmark" in {tool["name"] for tool in account}
    assert all("inputSchema" in tool for tool in account)


@pytest.mark.parametrize(
    "error", [FileNotFoundError("missing tool"), subprocess.TimeoutExpired("oasdiff", 90)]
)
def test_subprocess_failures_are_preserved(error, monkeypatch):
    def run(*args, **kwargs):
        assert kwargs["timeout"] == 90
        raise error

    monkeypatch.setattr(subprocess, "run", run)
    result = contracts.run_diff(["oasdiff"])
    assert result.returncode == 2
    assert result.stderr


def test_classifier_probe_failure_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(
        contracts, "run_diff", lambda command: subprocess.CompletedProcess(command, 0, "[]", "")
    )
    with pytest.raises(ValueError, match="removed-tool regression probe"):
        contracts.verify_diff_rules(tmp_path)


@pytest.mark.parametrize("stdout", ["{}", "null", "invalid JSON"])
def test_classifier_probes_reject_invalid_output(tmp_path, monkeypatch, stdout):
    monkeypatch.setattr(
        contracts,
        "run_diff",
        lambda command: subprocess.CompletedProcess(command, 0, stdout, ""),
    )
    with pytest.raises(ValueError):
        contracts.verify_diff_rules(tmp_path)


def test_classifier_probes_cover_nested_and_required_changes(tmp_path, monkeypatch):
    calls = []

    def run(command):
        assert command[command.index("--config") + 1] == str(ROOT / ".oasdiff.yaml")
        change = Path(command[3]).stem
        approved = Path(command[command.index("--err-ignore") + 1]).read_text()
        assert bool(approved) == (
            change.startswith("tag-patch-")
            and change
            not in {
                "tag-patch-other-null",
                "tag-patch-other-type",
                "tag-patch-other-endpoint",
                "tag-patch-other-method",
                "tag-patch-future-nullable-name",
                "tag-patch-future-name-max-removed",
                "tag-patch-future-slug-pattern-removed",
                "tag-patch-unknown-revision",
                "tag-patch-current-unknown-revision",
                "tag-patch-altered-baseline",
            }
        )
        calls.append(change)
        code = contracts.PROBE_RESULTS[change]
        return subprocess.CompletedProcess(
            command, code, '[{"id":"breaking"}]' if code else "[]", ""
        )

    monkeypatch.setattr(contracts, "run_diff", run)
    contracts.verify_diff_rules(tmp_path)
    assert calls == list(contracts.PROBE_RESULTS)
    nested = json.loads((tmp_path / "nested-response-type.json").read_text())
    assert (
        nested["components"]["schemas"]["get_article__output__Article"]["properties"]["id"]["type"]
        == "integer"
    )


@pytest.mark.parametrize(
    "change",
    [
        "response-enum-added",
        "response-array-enum-added",
        "response-map-enum-added",
        "response-root-enum-added",
        "request-enum-removed",
        "request-min-length-increased",
        "required-input-with-default",
        "required-response-removed",
        "nested-response-type",
        "tag-patch-future-nullable-name",
        "tag-patch-future-name-max-removed",
        "tag-patch-future-slug-pattern-removed",
        "tag-patch-unknown-revision",
        "tag-patch-current-unknown-revision",
        "tag-patch-altered-baseline",
    ],
)
def test_classifier_probes_reject_incorrect_compatibility_classifications(
    change, tmp_path, monkeypatch
):
    def run(command):
        current = Path(command[3]).stem
        code = contracts.PROBE_RESULTS[current]
        if current == change:
            code = 1 - code
        return subprocess.CompletedProcess(
            command,
            code,
            '[{"id":"breaking"}]' if code else "[]",
            "",
        )

    monkeypatch.setattr(contracts, "run_diff", run)
    with pytest.raises(ValueError, match=f"{change} regression probe"):
        contracts.verify_diff_rules(tmp_path)


def test_ci_contracts_share_the_local_policy_and_keep_warn_as_the_gate():
    config = yaml.safe_load((ROOT / ".oasdiff.yaml").read_text())
    assert set(config) == {"severity-levels"}
    levels = [
        line.strip()
        for line in (ROOT / config["severity-levels"]).read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert levels == [
        "response-property-enum-value-added info",
        "response-property-max-unset info",
    ]
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["api-compat"]
    assert job["env"]["OASDIFF_CONFIG"] == ".oasdiff.yaml"
    checks = [
        step
        for step in job["steps"]
        if step.get("id") in {"public", "user", "admin", "mcp_public", "mcp_account"}
    ]
    assert len(checks) == 5
    policy = next(step for step in job["steps"] if step.get("id") == "policy")
    assert policy["run"].endswith("python scripts/ci/api_contracts.py prepare-policy")
    for step in checks:
        assert step["uses"].startswith("oasdiff/oasdiff-action/breaking@")
        assert step["with"]["fail-on"] == "WARN"
        assert step["with"]["err-ignore"] == "reports/api-compatibility/policy/approved-errors.md"
        assert "steps.policy.outcome == 'success'" in step["if"]
        assert "steps.probes.outcome == 'success'" in step["if"]
        assert "warn-ignore" not in step["with"]


@pytest.fixture
def policy_checkout(tmp_path, monkeypatch):
    source = ROOT / ".github/api-compatibility"
    shutil.copytree(source, tmp_path / ".github/api-compatibility")
    document, _ = contracts.tag_patch_probe("tag-patch-schema-correction")
    schema = document["paths"]["/v1/admin/tags/{tag_id}"]["patch"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    document["components"] = {"schemas": {"TagPatch": schema}}
    document["paths"]["/v1/admin/tags/{tag_id}"]["patch"]["requestBody"]["content"][
        "application/json"
    ]["schema"] = {"$ref": "#/components/schemas/TagPatch"}
    monkeypatch.setattr(contracts, "ROOT", tmp_path)
    base = contracts.report_directory("base")
    base.mkdir(parents=True)
    contracts.write_json(base / "admin.json", document)
    output = contracts.report_directory("policy")
    output.mkdir(parents=True)
    (output / "approved-errors.md").write_text("stale previously granted corrections")
    contracts.write_json(output / "decision.json", {"corrections_approved": True})
    return tmp_path, output, document


def checked_out_revision(monkeypatch, root, revision):
    def run(command, **kwargs):
        assert command == ["git", "-C", str(root / ".api-base"), "rev-parse", "--verify", "HEAD"]
        assert kwargs == {"capture_output": True, "text": True, "check": True, "timeout": 10}
        return subprocess.CompletedProcess(command, 0, f"{revision}\n", "")

    monkeypatch.setattr(contracts.subprocess, "run", run)


@pytest.mark.parametrize(
    "revision",
    [
        "ff71700d275c2a8b69a77642b543b1a87955404c",
        "a5541d6242aea01b486a372c9372a5822b65cc6b",
    ],
)
def test_policy_requires_the_actual_known_checkout_and_exported_request_schema(
    revision, policy_checkout, monkeypatch
):
    root, output, _ = policy_checkout
    checked_out_revision(monkeypatch, root, revision)
    # An environment claim cannot substitute for the actual Git checkout.
    monkeypatch.setenv("BASE_SHA", "f" * 40)
    contracts.prepare_policy(output)
    assert (output / "approved-errors.md").read_text() == (
        root / ".github/api-compatibility/tag-patch-corrections.md"
    ).read_text()
    assert json.loads((output / "decision.json").read_text()) == {
        "base_revision": revision,
        "corrections_approved": True,
        "retired_operations": [],
        "snapshot_replacements": [],
    }


@pytest.mark.parametrize(
    "mismatch", ["revision", "schema", "missing-operation", "different-ref", "ref-sibling"]
)
def test_policy_clears_stale_allowances_for_unproven_bases(mismatch, policy_checkout, monkeypatch):
    root, output, document = policy_checkout
    known = "ff71700d275c2a8b69a77642b543b1a87955404c"
    revision = "f" * 40 if mismatch == "revision" else known
    checked_out_revision(monkeypatch, root, revision)
    monkeypatch.setenv("BASE_SHA", known)
    if mismatch == "schema":
        document["components"]["schemas"]["TagPatch"]["properties"]["name"]["title"] = "Changed"
    elif mismatch == "missing-operation":
        document["paths"] = {}
    elif mismatch == "different-ref":
        document["paths"]["/v1/admin/tags/{tag_id}"]["patch"]["requestBody"]["content"][
            "application/json"
        ]["schema"] = {"$ref": "#/components/schemas/OtherPatch"}
    elif mismatch == "ref-sibling":
        document["paths"]["/v1/admin/tags/{tag_id}"]["patch"]["requestBody"]["content"][
            "application/json"
        ]["schema"]["required"] = ["name"]
    contracts.write_json(contracts.report_directory("base") / "admin.json", document)
    contracts.prepare_policy(output)
    assert (output / "approved-errors.md").read_text() == ""
    assert json.loads((output / "decision.json").read_text()) == {
        "base_revision": revision,
        "corrections_approved": False,
        "retired_operations": [],
        "snapshot_replacements": [],
    }


@pytest.mark.parametrize(
    "failure", ["git-error", "git-timeout", "missing-export", "invalid-export", "invalid-baseline"]
)
def test_policy_preparation_errors_discard_stale_allowances(failure, policy_checkout, monkeypatch):
    root, output, _ = policy_checkout
    checked_out_revision(monkeypatch, root, "ff71700d275c2a8b69a77642b543b1a87955404c")
    if failure.startswith("git-"):

        def fail(*args, **kwargs):
            if failure == "git-timeout":
                raise subprocess.TimeoutExpired("git", 10)
            raise subprocess.CalledProcessError(128, "git")

        monkeypatch.setattr(contracts.subprocess, "run", fail)
    elif failure == "missing-export":
        (contracts.report_directory("base") / "admin.json").unlink()
    elif failure == "invalid-export":
        (contracts.report_directory("base") / "admin.json").write_text("invalid JSON")
    else:
        (root / ".github/api-compatibility/tag-patch-baseline.json").write_text("invalid JSON")
    with pytest.raises((subprocess.SubprocessError, OSError, ValueError)):
        contracts.prepare_policy(output)
    assert (output / "approved-errors.md").read_text() == ""
    assert not (output / "decision.json").exists()


def test_policy_does_not_follow_a_stale_allowance_symlink(policy_checkout, monkeypatch):
    root, output, _ = policy_checkout
    checked_out_revision(monkeypatch, root, "f" * 40)
    outside = root / "untouched.md"
    outside.write_text("outside data")
    approved = output / "approved-errors.md"
    approved.unlink()
    approved.symlink_to(outside)
    contracts.prepare_policy(output)
    assert approved.read_text() == ""
    assert not approved.is_symlink()
    assert outside.read_text() == "outside data"


@pytest.fixture
def retirement_checkout(policy_checkout):
    root, output, _ = policy_checkout
    policy_path = root / ".github/api-compatibility/admin-overview-retirement.json"
    policy = json.loads(policy_path.read_text())
    operation = {
        "responses": {
            "200": {
                "description": "Overview",
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/Report"}}
                },
            }
        }
    }
    document = {
        "openapi": "3.1.0",
        "info": {"title": "Retirement fixture", "version": "1"},
        "paths": {row["path"]: {"get": copy.deepcopy(operation)} for row in policy["operations"]},
        "components": {
            "schemas": {
                "Report": {
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                    "required": ["count"],
                }
            }
        },
    }
    for row in policy["operations"]:
        row["fingerprint"] = contracts.operation_fingerprint(document, row["path"], row["method"])
    contracts.write_json(policy_path, policy)
    contracts.write_json(contracts.report_directory("base") / "admin.json", document)
    candidate = copy.deepcopy(document)
    candidate["paths"] = {policy["replacement"]["path"]: {"get": copy.deepcopy(operation)}}
    contracts.report_directory("head").mkdir()
    contracts.write_json(contracts.report_directory("head") / "admin.json", candidate)
    return root, output, document, candidate, policy


def test_policy_allows_only_reviewed_overview_removals(retirement_checkout, monkeypatch):
    root, output, _, _, policy = retirement_checkout
    revision = policy["base_revisions"][0]
    checked_out_revision(monkeypatch, root, revision)
    contracts.prepare_policy(output)
    expected = [
        f"- {row['method']} {row['path']} api path removed without deprecation"
        for row in policy["operations"]
    ]
    assert (output / "approved-errors.md").read_text().splitlines() == expected
    decision = json.loads((output / "decision.json").read_text())
    assert decision["retired_operations"] == expected
    assert decision["corrections_approved"] is False


@pytest.mark.parametrize(
    "mismatch",
    [
        "revision",
        "operation",
        "response",
        "missing-base",
        "missing-ref",
        "external-ref",
        "still-present",
        "replacement",
    ],
)
def test_retirement_requires_frozen_base_and_completed_replacement(
    mismatch, retirement_checkout, monkeypatch
):
    root, output, document, candidate, policy = retirement_checkout
    revision = "f" * 40 if mismatch == "revision" else policy["base_revisions"][0]
    checked_out_revision(monkeypatch, root, revision)
    old_path = policy["operations"][0]["path"]
    if mismatch == "operation":
        document["paths"][old_path]["get"]["summary"] = "Changed operation"
    elif mismatch == "response":
        document["components"]["schemas"]["Report"]["properties"]["count"]["type"] = "string"
    elif mismatch == "missing-base":
        del document["paths"][old_path]
    elif mismatch == "missing-ref":
        document["components"]["schemas"] = {}
    elif mismatch == "external-ref":
        document["components"]["schemas"]["Report"] = {"$ref": "https://untrusted.test/schema"}
    elif mismatch == "still-present":
        candidate["paths"][old_path] = document["paths"][old_path]
    elif mismatch == "replacement":
        candidate["paths"] = {}
    contracts.write_json(contracts.report_directory("base") / "admin.json", document)
    contracts.write_json(contracts.report_directory("head") / "admin.json", candidate)
    contracts.prepare_policy(output)
    assert (output / "approved-errors.md").read_text() == ""
    assert json.loads((output / "decision.json").read_text())["retired_operations"] == []


def test_retirement_does_not_allow_an_unrelated_removal(retirement_checkout, monkeypatch):
    root, output, document, candidate, policy = retirement_checkout
    document["paths"]["/v1/admin/other"] = copy.deepcopy(next(iter(document["paths"].values())))
    contracts.write_json(contracts.report_directory("base") / "admin.json", document)
    checked_out_revision(monkeypatch, root, policy["base_revisions"][0])
    contracts.prepare_policy(output)
    allowed = (output / "approved-errors.md").read_text()
    assert "/v1/admin/other" not in allowed
    assert len(allowed.splitlines()) == len(policy["operations"])


def test_missing_candidate_contract_clears_retirement_allowances(retirement_checkout, monkeypatch):
    root, output, _, _, policy = retirement_checkout
    checked_out_revision(monkeypatch, root, policy["base_revisions"][0])
    (contracts.report_directory("head") / "admin.json").unlink()
    with pytest.raises(FileNotFoundError):
        contracts.prepare_policy(output)
    assert (output / "approved-errors.md").read_text() == ""
    assert not (output / "decision.json").exists()


@pytest.fixture
def snapshot_replacement_checkout(retirement_checkout):
    root, output, document, candidate, policy = retirement_checkout
    operation = copy.deepcopy(next(iter(document["paths"].values())))
    candidate["components"]["schemas"]["Report"]["properties"]["count"]["type"] = "string"
    for row in policy["snapshot_replacements"]:
        document["paths"][row["path"]] = copy.deepcopy(operation)
        candidate["paths"][row["path"]] = copy.deepcopy(operation)
        row["base_fingerprint"] = contracts.operation_fingerprint(
            document, row["path"], row["method"]
        )
        row["head_fingerprint"] = contracts.operation_fingerprint(
            candidate, row["path"], row["method"]
        )
    contracts.write_json(root / ".github/api-compatibility/admin-overview-retirement.json", policy)
    contracts.write_json(contracts.report_directory("base") / "admin.json", document)
    contracts.write_json(contracts.report_directory("head") / "admin.json", candidate)
    return root, output, document, candidate, policy


def test_snapshot_replacement_requires_both_exact_operation_graphs(
    snapshot_replacement_checkout, monkeypatch
):
    root, output, _, _, policy = snapshot_replacement_checkout
    checked_out_revision(monkeypatch, root, policy["base_revisions"][0])
    contracts.prepare_policy(output)
    expected = [
        f"- {row['method']} {row['path']} {row['finding']}"
        for row in policy["snapshot_replacements"]
    ]
    decision = json.loads((output / "decision.json").read_text())
    assert decision["snapshot_replacements"] == expected
    assert (output / "approved-errors.md").read_text().splitlines() == (
        decision["retired_operations"] + expected
    )


@pytest.mark.parametrize("side", ["base", "head"])
@pytest.mark.parametrize("change", ["operation", "type", "enum", "union", "required", "missing"])
def test_future_snapshot_contract_edits_cannot_reuse_the_replacement_allowance(
    side, change, snapshot_replacement_checkout, monkeypatch
):
    root, output, document, candidate, policy = snapshot_replacement_checkout
    checked_out_revision(monkeypatch, root, policy["base_revisions"][0])
    edited = document if side == "base" else candidate
    operation = policy["snapshot_replacements"][0]
    schema = edited["components"]["schemas"]["Report"]
    if change == "operation":
        edited["paths"][operation["path"]]["get"]["summary"] = "Future edit"
    elif change == "type":
        schema["properties"]["count"]["type"] = "boolean"
    elif change == "enum":
        schema["properties"]["count"]["enum"] = ["new"]
    elif change == "union":
        schema["properties"]["count"] = {"anyOf": [{"type": "string"}, {"type": "object"}]}
    elif change == "required":
        schema["required"] = []
    else:
        del edited["paths"][operation["path"]]
    contracts.write_json(contracts.report_directory(side) / "admin.json", edited)
    contracts.prepare_policy(output)
    assert json.loads((output / "decision.json").read_text())["snapshot_replacements"] == []
    assert (
        "added `#/components/schemas/AnalyticsSnapshot`"
        not in (output / "approved-errors.md").read_text()
    )


@pytest.mark.parametrize("findings", [[], [{"id": "request-property-pattern-added"}]])
def test_raw_reports_preserve_findings_before_corrections(findings, tmp_path, monkeypatch):
    calls = []

    def run(command):
        assert command[-4:] == ["--err-ignore", "/dev/null", "--warn-ignore", "/dev/null"]
        assert command[command.index("--fail-on") + 1] == "WARN"
        calls.append(Path(command[2]).stem)
        return subprocess.CompletedProcess(command, int(bool(findings)), json.dumps(findings), "")

    monkeypatch.setattr(contracts, "run_diff", run)
    contracts.report_breaking_changes(tmp_path)
    assert calls == list(contracts.CONTRACTS)
    assert {path.stem for path in tmp_path.glob("*.json")} == set(contracts.CONTRACTS)
    for name in contracts.CONTRACTS:
        assert json.loads((tmp_path / f"{name}.json").read_text()) == findings


@pytest.mark.parametrize(
    "code,output",
    [(2, "tool failed"), (0, "invalid JSON"), (0, "{}"), (1, "[]"), (0, '[{"id":"breaking"}]')],
)
def test_raw_reports_reject_tool_errors_and_inconsistent_results(
    code, output, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        contracts,
        "run_diff",
        lambda command: subprocess.CompletedProcess(command, code, output, "failure details"),
    )
    with pytest.raises(ValueError):
        contracts.report_breaking_changes(tmp_path)
    assert (tmp_path / "public.json").read_text() == output


@pytest.mark.parametrize(
    "mode,revision",
    [
        ("export", "head"),
        ("export", "base"),
        ("verify", None),
        ("report", None),
        ("prepare-policy", None),
    ],
)
def test_cli_returns_the_gate_result(mode, revision, tmp_path, monkeypatch):
    monkeypatch.setattr(contracts, "ROOT", tmp_path)
    directory = (
        tmp_path
        / "reports/api-compatibility"
        / (revision or {"report": "raw", "verify": "probes", "prepare-policy": "policy"}[mode])
    )
    arguments = ["api_contracts", mode, *([revision] if revision else [])]
    monkeypatch.setattr("sys.argv", arguments)
    calls = []
    monkeypatch.setattr(contracts, "export_contracts", lambda output: calls.append(output))
    monkeypatch.setattr(contracts, "verify_diff_rules", lambda output: calls.append(output))
    monkeypatch.setattr(contracts, "report_breaking_changes", lambda output: calls.append(output))
    monkeypatch.setattr(contracts, "prepare_policy", lambda output: calls.append(output))
    assert contracts.main() == 0
    assert calls == [directory]


@pytest.mark.parametrize("mode", ["export", "verify", "report", "prepare-policy"])
@pytest.mark.parametrize("escape", ["absolute", "traversal", "symlink"])
def test_cli_rejects_output_escapes_before_work(mode, escape, tmp_path, monkeypatch):
    monkeypatch.setattr(contracts, "ROOT", tmp_path)
    root = tmp_path / "reports/api-compatibility"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    if escape == "symlink":
        (root / "link").symlink_to(outside, target_is_directory=True)
        output = root / "link/head"
    elif escape == "traversal":
        output = root / "../../outside"
    else:
        output = outside
    monkeypatch.setattr("sys.argv", ["api_contracts", mode, str(output)])
    monkeypatch.setattr(contracts, "run_diff", lambda command: pytest.fail("Executed a tool"))
    with pytest.raises(SystemExit) as error:
        contracts.main()
    assert error.value.code == 2
    assert not outside.exists()


def test_cli_rejects_executable_override(tmp_path, monkeypatch):
    monkeypatch.setattr(contracts, "ROOT", tmp_path)
    output = tmp_path / "reports/api-compatibility/probes"
    monkeypatch.setattr("sys.argv", ["api_contracts", "verify", "--oasdiff", "sh"])
    monkeypatch.setattr(contracts, "run_diff", lambda command: pytest.fail("Executed a tool"))
    with pytest.raises(SystemExit) as error:
        contracts.main()
    assert error.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize("name", ["head", "base", "probes", "raw", "policy"])
def test_fixed_report_destinations_reject_symlinks(name, tmp_path, monkeypatch):
    monkeypatch.setattr(contracts, "ROOT", tmp_path)
    root = tmp_path / "reports/api-compatibility"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    (root / name).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Output must be within"):
        contracts.report_directory(name)
    assert not outside.exists()
