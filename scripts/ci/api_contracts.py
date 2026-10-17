"""Generate offline API contracts and verify oasdiff's compatibility rules."""

import argparse
import asyncio
import copy
import importlib
import json
import subprocess
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote

CONTRACTS = ("public", "user", "admin", "mcp-public", "mcp-account")
ROOT = Path(__file__).resolve().parents[2]


def report_directory(name: str) -> Path:
    """Use fixed checkout-owned destinations rather than CLI-supplied paths."""
    root = ROOT.resolve() / "reports/api-compatibility"
    directory = {
        "head": root / "head",
        "base": root / "base",
        "probes": root / "probes",
    }[name].resolve()
    if not directory.is_relative_to(root):
        raise ValueError("Output must be within reports/api-compatibility")
    return directory


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def schema_components(schema: dict, prefix: str, components: dict) -> dict:
    """Move Pydantic's root definitions into namespaced OpenAPI components."""
    schema = copy.deepcopy(schema)
    definitions = schema.pop("$defs", {})

    def rewrite(value):
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str) and item.startswith("#/$defs/"):
                item = f"#/components/schemas/{prefix}__{item.removeprefix('#/$defs/')}"
            result[key] = rewrite(item)
        return result

    for name, definition in definitions.items():
        components[f"{prefix}__{name}"] = rewrite(definition)
    return rewrite(schema)


def tool_contract(tools: list[dict], title: str) -> dict:
    """Project tools/list schemas into request/response operations for oasdiff."""
    components: dict = {}
    paths = {}
    for tool in sorted(tools, key=lambda item: item["name"]):
        name = tool["name"]
        request = schema_components(tool["inputSchema"], f"{name}__input", components)
        response = schema_components(tool.get("outputSchema", {}), f"{name}__output", components)
        paths[f"/tools/{quote(name, safe='')}"] = {
            "post": {
                "operationId": name,
                "description": tool.get("description", ""),
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": request}},
                },
                "responses": {
                    "200": {
                        "description": "MCP structuredContent",
                        "content": {"application/json": {"schema": response}},
                    }
                },
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {"title": title, "version": "1"},
        "paths": paths,
        "components": {"schemas": components},
    }


async def export_mcp(directory: Path) -> None:
    import httpx
    from devfeed_mcp.api import PublicAPI
    from devfeed_mcp.config import Settings
    from devfeed_mcp.personal import UserAPI, register_personal_tools
    from devfeed_mcp.server import create_server

    def offline(request):
        raise RuntimeError("Schema generation must not make upstream requests")

    async with httpx.AsyncClient(transport=httpx.MockTransport(offline)) as client:
        settings = Settings()
        server = create_server(PublicAPI(client, settings))
        for scope in ("public", "account"):
            if scope == "account":
                register_personal_tools(server, UserAPI(client, settings))
            tools = [
                tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                for tool in await server.list_tools()
            ]
            write_json(directory / f"mcp-{scope}.tools.json", tools)
            write_json(directory / f"mcp-{scope}.json", tool_contract(tools, f"MCP {scope}"))


def export_contracts(directory: Path) -> None:
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # Avoid loading a developer's .env; factories are inspected without running lifespans.
    with TemporaryDirectory(prefix="devfeed-schema-") as working, chdir(working):
        for service, module in (
            ("public", "devfeed_api.main"),
            ("user", "devfeed_user_api.main"),
            ("admin", "devfeed_admin_api.main"),
        ):
            document = importlib.import_module(module).create_app().openapi()
            write_json(directory / f"{service}.json", document)
        asyncio.run(export_mcp(directory))


def breaking_command(binary: str, config: Path, base: Path, head: Path, format: str) -> list[str]:
    return [
        binary,
        "breaking",
        str(base),
        str(head),
        "--config",
        str(config),
        "--allow-external-refs=false",
        "--flatten-allof",
        "--fail-on",
        "WARN",
        "--format",
        format,
    ]


def run_diff(command: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(command, 2, stdout="", stderr=str(error))


PROBE_RESULTS = {
    "optional-field": 0,
    "response-enum-added": 0,
    "response-array-enum-added": 0,
    "response-map-enum-added": 0,
    "response-root-enum-added": 0,
    "response-enum-removed": 0,
    "removed-tool": 1,
    "required-input": 1,
    "required-input-with-default": 1,
    "request-enum-removed": 1,
    "request-min-length-increased": 1,
    "response-type": 1,
    "nested-response-type": 1,
    "required-response-removed": 1,
}


def verify_diff_rules(output: Path, binary: str = "oasdiff") -> None:
    """Exercise the shared compatibility policy with the pinned classifier."""
    output.mkdir(parents=True, exist_ok=True)
    config = ROOT / ".oasdiff.yaml"
    enum = {"type": "string", "enum": ["first", "second"]}
    base = tool_contract(
        [
            {
                "name": "get_article",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "mode": copy.deepcopy(enum),
                    },
                    "required": ["id"],
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {
                        "article": {"$ref": "#/$defs/Article"},
                        "states": {"type": "array", "items": copy.deepcopy(enum)},
                        "by_name": {"type": "object", "additionalProperties": copy.deepcopy(enum)},
                    },
                    "required": ["article"],
                    "$defs": {
                        "Article": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "state": copy.deepcopy(enum),
                            },
                            "required": ["id"],
                        }
                    },
                },
            }
        ],
        "Compatibility probes",
    )
    for change, expected in PROBE_RESULTS.items():
        original = copy.deepcopy(base)
        if change == "response-root-enum-added":
            original["paths"]["/tools/get_article"]["post"]["responses"]["200"]["content"][
                "application/json"
            ]["schema"] = copy.deepcopy(enum)
        head = copy.deepcopy(original)
        operation = head["paths"]["/tools/get_article"]["post"]
        request = operation["requestBody"]["content"]["application/json"]["schema"]
        response = operation["responses"]["200"]["content"]["application/json"]["schema"]
        article = head["components"]["schemas"]["get_article__output__Article"]
        if change == "optional-field":
            request["properties"]["language"] = {"type": "string"}
        elif change == "response-enum-added":
            article["properties"]["state"]["enum"].append("third")
        elif change == "response-array-enum-added":
            response["properties"]["states"]["items"]["enum"].append("third")
        elif change == "response-map-enum-added":
            response["properties"]["by_name"]["additionalProperties"]["enum"].append("third")
        elif change == "response-root-enum-added":
            response["enum"].append("third")
        elif change == "response-enum-removed":
            article["properties"]["state"]["enum"].remove("second")
        elif change == "removed-tool":
            head["paths"] = {}
        elif change in {"required-input", "required-input-with-default"}:
            request["properties"]["language"] = {"type": "string"}
            if change == "required-input-with-default":
                request["properties"]["language"]["default"] = "en"
            request["required"].append("language")
        elif change == "request-enum-removed":
            request["properties"]["mode"]["enum"].remove("second")
        elif change == "request-min-length-increased":
            request["properties"]["id"]["minLength"] = 2
        elif change == "response-type":
            operation["responses"]["200"]["content"]["application/json"]["schema"] = {
                "type": "integer"
            }
        elif change == "nested-response-type":
            article["properties"]["id"]["type"] = "integer"
        elif change == "required-response-removed":
            del article["properties"]["id"]
            article["required"].remove("id")
        base_path = output / f"{change}.base.json"
        head_path = output / f"{change}.json"
        write_json(base_path, original)
        write_json(head_path, head)
        result = run_diff(breaking_command(binary, config, base_path, head_path, "json"))
        (output / f"{change}.findings.json").write_text(result.stdout)
        findings = json.loads(result.stdout)
        if (
            not isinstance(findings, list)
            or result.returncode != expected
            or bool(findings) != bool(expected)
        ):
            raise ValueError(f"oasdiff failed the {change} regression probe: {result.stderr}")
    print(f"oasdiff regression probes: {len(PROBE_RESULTS)} passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("revision", choices=("head", "base"))
    commands.add_parser("verify")
    args = parser.parse_args()
    if args.command == "verify":
        verify_diff_rules(report_directory("probes"))
    else:
        export_contracts(report_directory(args.revision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
