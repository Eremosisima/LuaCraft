"""An MCP server for LuaCraft, so an agent can compile and check mods.

The server speaks the Model Context Protocol over stdio: one JSON-RPC 2.0
message per line.  It uses the Python standard library only, which keeps the
"fully offline" promise of the compiler intact.

Running it::

    luacraft mcp

or, equivalently::

    python -m luacraft.mcp_server

Tools exposed:

``luacraft_compile``
    Compile a Lua source for one edition or for every edition.
``luacraft_check``
    Report the capabilities each edition supports, without writing files.
``luacraft_api``
    Describe the portable runtime API and its per-edition capabilities.
``luacraft_content``
    List the items, blocks, recipes and loot tables a source declares.
``luacraft_sandbox``
    Validate a generated archive, or compile and validate in one step.
``luacraft_list_examples``
    List the bundled examples.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

from .api import Capability, Edition, PRIMITIVES
from .pipeline import archive_bundle, build, compile_all, write_bundle
from .sandbox import validate_build, validate_zip

SERVER_NAME = "luacraft"
PROTOCOL_VERSION = "2025-06-18"

_EDITION_ALIASES = {
    "java": Edition.JAVA,
    "je": Edition.JAVA,
    "datapack": Edition.JAVA,
    "bedrock": Edition.BEDROCK,
    "be": Edition.BEDROCK,
    "addon": Edition.BEDROCK,
    "luanti": Edition.LUANTI,
    "minetest": Edition.LUANTI,
    "mt": Edition.LUANTI,
}


# --------------------------------------------------------------------------
# Tool catalog
# --------------------------------------------------------------------------


def _source_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "The Lua source code to compile.",
            },
            "path": {
                "type": "string",
                "description": "Path to a .lua file, used when source is omitted.",
            },
            "namespace": {
                "type": "string",
                "description": "Namespace of the generated pack.",
                "default": "luacraft",
            },
            "pack_name": {
                "type": "string",
                "description": "Display name of the generated pack.",
                "default": "LuaCraft Mod",
            },
            "target": {
                "type": "string",
                "enum": ["java", "bedrock", "luanti", "all"],
                "description": "Edition to compile for.",
                "default": "all",
            },
            "textures": {
                "type": "boolean",
                "description": (
                    "Generate placeholder textures when none are supplied."
                ),
                "default": True,
            },
            "texture_dir": {
                "type": "string",
                "description": "Folder holding the PNG files the mod should use.",
            },
        },
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "luacraft_compile",
        "description": (
            "Compile Lua source into Minecraft Java datapacks, Bedrock "
            "add-ons and Luanti mods. Writes nothing unless output_dir is "
            "given. Returns diagnostics, the file list and, when requested, "
            "the archives as base64."
        ),
        "inputSchema": {
            **_source_schema(),
            "properties": {
                **_source_schema()["properties"],
                "output_dir": {
                    "type": "string",
                    "description": "Directory where the archives are written.",
                },
                "include_archives": {
                    "type": "boolean",
                    "description": "Return the archives as base64 strings.",
                    "default": False,
                },
                "sandbox": {
                    "type": "boolean",
                    "description": "Validate every build with the sandbox.",
                    "default": True,
                },
                "debug": {
                    "type": "boolean",
                    "description": "Include the phase trace of every build.",
                    "default": False,
                },
            },
        },
    },
    {
        "name": "luacraft_check",
        "description": (
            "Analyse Lua source and report, per edition, which capabilities "
            "are fully supported, partial or missing. Never writes files."
        ),
        "inputSchema": {
            **_source_schema(),
            "properties": {
                **_source_schema()["properties"],
                "debug": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "luacraft_api",
        "description": (
            "Describe the portable LuaCraft runtime API: every primitive, its "
            "signature and its capability in each edition."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "luacraft_content",
        "description": (
            "List the items, blocks, recipes and loot tables a Lua source "
            "declares, without compiling."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "path": {"type": "string"},
                "namespace": {"type": "string", "default": "luacraft"},
            },
        },
    },
    {
        "name": "luacraft_sandbox",
        "description": (
            "Validate a generated pack. Give archive to inspect an existing "
            "zip, or source/path to compile and validate in one step."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "archive": {
                    "type": "string",
                    "description": "Path to a .zip, .mcaddon or .mcpack.",
                },
                "source": {"type": "string"},
                "path": {"type": "string"},
                "target": {
                    "type": "string",
                    "enum": ["java", "bedrock", "luanti", "all"],
                    "default": "all",
                },
                "textures": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "luacraft_list_examples",
        "description": "List the example mods bundled with LuaCraft, with their source.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# --------------------------------------------------------------------------
# JSON-RPC handling
# --------------------------------------------------------------------------


class ToolError(Exception):
    """A tool failed in a way the caller should see."""


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC message and return the response, if any."""
    method = message.get("method")
    request_id = message.get("id")

    if method is None:
        return None

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": _version()},
                "instructions": (
                    "LuaCraft compiles one Lua source into Minecraft Java "
                    "datapacks, Bedrock add-ons and Luanti mods. Use "
                    "luacraft_check first to see which edition supports the "
                    "code, then luacraft_compile. Everything runs offline."
                ),
            },
        )

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            payload = _call_tool(name, arguments)
        except ToolError as exc:
            return _result(
                request_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
        except Exception as exc:  # pragma: no cover - defensive
            return _result(
                request_id,
                {
                    "content": [
                        {"type": "text", "text": f"{type(exc).__name__}: {exc}"}
                    ],
                    "isError": True,
                },
            )
        return _result(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(payload, indent=2, ensure_ascii=False),
                    }
                ],
                "isError": False,
            },
        )

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"unknown method {method!r}"},
    }


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _version() -> str:
    from . import __version__

    return __version__


def _call_tool(name: str | None, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "luacraft_compile":
        return _tool_compile(arguments)
    if name == "luacraft_check":
        return _tool_check(arguments)
    if name == "luacraft_api":
        return _tool_api(arguments)
    if name == "luacraft_content":
        return _tool_content(arguments)
    if name == "luacraft_sandbox":
        return _tool_sandbox(arguments)
    if name == "luacraft_list_examples":
        return _tool_examples(arguments)
    raise ToolError(f"unknown tool {name!r}")


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


def _read_source(arguments: dict[str, Any]) -> tuple[str, str]:
    if arguments.get("source"):
        return str(arguments["source"]), "<mcp>"
    path = arguments.get("path")
    if not path:
        raise ToolError("give either 'source' or 'path'")
    file = Path(path)
    if not file.exists():
        raise ToolError(f"{path} does not exist")
    return file.read_text(encoding="utf-8"), str(file)


def _editions(target: str) -> list[Edition]:
    if target == "all":
        return [Edition.JAVA, Edition.BEDROCK, Edition.LUANTI]
    edition = _EDITION_ALIASES.get(target.lower())
    if edition is None:
        raise ToolError(f"unknown target {target!r}")
    return [edition]


def _diagnostics(result) -> list[dict]:
    return [diagnostic.as_dict() for diagnostic in result.diagnostics]


def _tool_compile(arguments: dict[str, Any]) -> dict[str, Any]:
    source, source_name = _read_source(arguments)
    namespace = str(arguments.get("namespace", "luacraft"))
    pack_name = str(arguments.get("pack_name", "LuaCraft Mod"))
    texture_dir = arguments.get("texture_dir")
    textures = bool(arguments.get("textures", True))
    sandbox = bool(arguments.get("sandbox", True))
    debug = bool(arguments.get("debug", False))

    results = compile_all(
        source,
        namespace=namespace,
        pack_name=pack_name,
        source_name=source_name,
        texture_dir=Path(texture_dir) if texture_dir else None,
        generate_textures=textures,
        editions=_editions(str(arguments.get("target", "all"))),
        debug=debug,
    )

    payload: dict[str, Any] = {
        "ok": all(result.ok for result in results.values()),
        "editions": {},
    }
    for edition, result in results.items():
        entry: dict[str, Any] = {
            "ok": result.ok,
            "files": sorted(result.files),
            "resources": sorted(result.resources),
            "diagnostics": _diagnostics(result),
            "content": result.content.summary(),
        }
        if sandbox:
            entry["sandbox"] = validate_build(result).as_dict()
        if debug:
            entry["trace"] = result.trace.as_dict()
        payload["editions"][edition.value] = entry

    output_dir = arguments.get("output_dir")
    if output_dir:
        written = write_bundle(
            results, Path(output_dir), namespace=namespace
        )
        payload["written"] = {
            key: str(path) for key, path in written.items()
        }

    if arguments.get("include_archives"):
        payload["archives"] = {
            name: base64.b64encode(data).decode("ascii")
            for name, data in archive_bundle(
                results, namespace=namespace
            ).items()
        }

    return payload


def _tool_check(arguments: dict[str, Any]) -> dict[str, Any]:
    source, source_name = _read_source(arguments)
    namespace = str(arguments.get("namespace", "luacraft"))
    debug = bool(arguments.get("debug", False))
    results = compile_all(
        source,
        namespace=namespace,
        source_name=source_name,
        generate_textures=bool(arguments.get("textures", True)),
        editions=_editions(str(arguments.get("target", "all"))),
        debug=debug,
    )
    payload: dict[str, Any] = {"editions": {}}
    for edition, result in results.items():
        entry = {
            "ok": result.ok,
            "warnings": [d.as_dict() for d in result.warnings],
            "errors": [d.as_dict() for d in result.errors],
        }
        if debug:
            entry["trace"] = result.trace.as_dict()
        payload["editions"][edition.value] = entry
    payload["portable_capabilities"] = _portable_capabilities()
    return payload


def _tool_api(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "primitives": [
            {
                "name": primitive.name,
                "summary": primitive.summary,
                "signature": primitive.signature(),
                "capabilities": {
                    edition.value: primitive.capability(edition).value
                    for edition in Edition
                },
                "notes": {
                    edition.value: note
                    for edition, note in primitive.notes.items()
                },
            }
            for primitive in PRIMITIVES
        ],
        "portable": _portable_capabilities(),
    }


def _portable_capabilities() -> list[str]:
    return [
        primitive.name
        for primitive in PRIMITIVES
        if all(
            primitive.capability(edition) is Capability.FULL
            for edition in Edition
        )
    ]


def _tool_content(arguments: dict[str, Any]) -> dict[str, Any]:
    source, source_name = _read_source(arguments)
    namespace = str(arguments.get("namespace", "luacraft"))
    result = build(source, Edition.JAVA, namespace=namespace, source_name=source_name)
    content = result.content
    return {
        "summary": content.summary(),
        "items": {
            name: {
                "identifier": item.identifier,
                "max_stack": item.max_stack,
                "durability": item.durability,
                "texture": item.texture or item.name,
                "category": item.category,
            }
            for name, item in content.items.items()
        },
        "blocks": {
            name: {
                "identifier": block.identifier,
                "hardness": block.hardness,
                "light_emission": block.light_emission,
                "texture": block.texture or block.name,
            }
            for name, block in content.blocks.items()
        },
        "recipes": {
            name: {
                "identifier": recipe.identifier,
                "type": recipe.type,
                "result": recipe.result,
                "count": recipe.count,
            }
            for name, recipe in content.recipes.items()
        },
        "diagnostics": _diagnostics(result),
    }


def _tool_sandbox(arguments: dict[str, Any]) -> dict[str, Any]:
    archive = arguments.get("archive")
    if archive:
        path = Path(archive)
        if not path.exists():
            raise ToolError(f"{archive} does not exist")
        return validate_zip(path).as_dict()

    source, source_name = _read_source(arguments)
    results = compile_all(
        source,
        namespace=str(arguments.get("namespace", "luacraft")),
        source_name=source_name,
        generate_textures=bool(arguments.get("textures", True)),
        editions=_editions(str(arguments.get("target", "all"))),
        debug=bool(arguments.get("debug", False)),
    )
    return {
        edition.value: validate_build(result).as_dict()
        for edition, result in results.items()
    }


def _tool_examples(arguments: dict[str, Any]) -> dict[str, Any]:
    examples_dir = Path(__file__).resolve().parents[2] / "examples"
    if not examples_dir.is_dir():
        examples_dir = Path.cwd() / "examples"
    examples: dict[str, str] = {}
    if examples_dir.is_dir():
        for path in sorted(examples_dir.glob("*.lua")):
            examples[path.name] = path.read_text(encoding="utf-8")
    return {"examples": examples}


# --------------------------------------------------------------------------
# stdio transport
# --------------------------------------------------------------------------


def serve(stdin=None, stdout=None, *, debug: bool = False) -> int:
    """Serve MCP messages until stdin closes."""
    input_stream = stdin or sys.stdin.buffer
    output_stream = stdout or sys.stdout.buffer

    for raw in input_stream:
        if isinstance(raw, bytes):
            line = raw.decode("utf-8", errors="replace")
        else:
            line = raw
        line = line.strip()
        if not line:
            continue
        if debug:  # pragma: no cover - humans only
            print(f"<< {line}", file=sys.stderr)
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "parse error"},
            }
        else:
            if not isinstance(message, dict):
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "invalid request"},
                }
            else:
                response = handle_message(message)

        if response is None:
            continue
        data = json.dumps(response, ensure_ascii=False) + "\n"
        if debug:  # pragma: no cover - humans only
            print(f">> {data.strip()}", file=sys.stderr)
        try:
            output_stream.write(data)
        except TypeError:
            output_stream.write(data.encode("utf-8"))
        output_stream.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    return serve(debug="--debug" in arguments)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
