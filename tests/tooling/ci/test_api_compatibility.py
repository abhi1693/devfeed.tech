"""Regression coverage for offline API contracts and compatibility probes."""

import copy
import importlib.util
import json
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
        assert "--err-ignore" not in command
        change = Path(command[3]).stem
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


def test_ci_contracts_share_the_local_severity_policy_without_finding_exceptions():
    config = yaml.safe_load((ROOT / ".oasdiff.yaml").read_text())
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
    for step in checks:
        assert step["uses"].startswith("oasdiff/oasdiff-action/breaking@")
        assert step["with"]["fail-on"] == "WARN"
        assert "err-ignore" not in step["with"]
        assert "warn-ignore" not in step["with"]


@pytest.mark.parametrize(
    "mode,revision", [("export", "head"), ("export", "base"), ("verify", None)]
)
def test_cli_returns_the_gate_result(mode, revision, tmp_path, monkeypatch):
    monkeypatch.setattr(contracts, "ROOT", tmp_path)
    directory = tmp_path / "reports/api-compatibility" / (revision or "probes")
    arguments = ["api_contracts", mode, *([revision] if revision else [])]
    monkeypatch.setattr("sys.argv", arguments)
    calls = []
    monkeypatch.setattr(contracts, "export_contracts", lambda output: calls.append(output))
    monkeypatch.setattr(contracts, "verify_diff_rules", lambda output: calls.append(output))
    assert contracts.main() == 0
    assert calls == [directory]


@pytest.mark.parametrize("mode", ["export", "verify"])
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


@pytest.mark.parametrize("name", ["head", "base", "probes"])
def test_fixed_report_destinations_reject_symlinks(name, tmp_path, monkeypatch):
    monkeypatch.setattr(contracts, "ROOT", tmp_path)
    root = tmp_path / "reports/api-compatibility"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    (root / name).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Output must be within"):
        contracts.report_directory(name)
    assert not outside.exists()
