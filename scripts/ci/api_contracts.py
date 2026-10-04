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


def verify_diff_rules(output: Path, binary: str = "oasdiff") -> None:
    """Exercise the pinned classifier with real compatible and breaking schemas."""
    output.mkdir(parents=True, exist_ok=True)
    config = output / "oasdiff.yaml"
    config.write_text("{}\n")
    base = tool_contract(
        [
            {
                "name": "get_article",
                "inputSchema": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {"article": {"$ref": "#/$defs/Article"}},
                    "required": ["article"],
                    "$defs": {
                        "Article": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                            "required": ["id"],
                        }
                    },
                },
            }
        ],
        "Compatibility probes",
    )
    base_path = output / "base.json"
    write_json(base_path, base)
    for change in (
        "optional-field",
        "removed-tool",
        "required-input",
        "response-type",
        "nested-response-type",
    ):
        head = copy.deepcopy(base)
        operation = head["paths"]["/tools/get_article"]["post"]
        request = operation["requestBody"]["content"]["application/json"]["schema"]
        if change == "optional-field":
            request["properties"]["language"] = {"type": "string"}
        elif change == "removed-tool":
            head["paths"] = {}
        elif change == "required-input":
            request["properties"]["language"] = {"type": "string"}
            request["required"].append("language")
        elif change == "response-type":
            operation["responses"]["200"]["content"]["application/json"]["schema"] = {
                "type": "integer"
            }
        else:
            article = head["components"]["schemas"]["get_article__output__Article"]
            article["properties"]["id"]["type"] = "integer"
        head_path = output / f"{change}.json"
        write_json(head_path, head)
        result = run_diff(breaking_command(binary, config, base_path, head_path, "json"))
        (output / f"{change}.findings.json").write_text(result.stdout)
        expected = 0 if change == "optional-field" else 1
        findings = json.loads(result.stdout)
        if (
            not isinstance(findings, list)
            or result.returncode != expected
            or bool(findings) != bool(expected)
        ):
            raise ValueError(f"oasdiff failed the {change} regression probe: {result.stderr}")
    print("oasdiff regression probes: 5 passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("output", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("output", type=Path)
    verify.add_argument("--oasdiff", default="oasdiff")
    args = parser.parse_args()
    if args.command == "verify":
        verify_diff_rules(args.output, args.oasdiff)
    else:
        export_contracts(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
