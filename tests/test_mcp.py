"""Tests for the MCP server."""

from __future__ import annotations

import base64
import io
import json

from luacraft.mcp_server import handle_message, serve

SOURCE = """
items.ruby = { id = "test:ruby", texture = "ruby" }
say("hola")
"""


def _call(method: str, params=None, request_id=1):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return handle_message(message)


def _tool(name: str, arguments: dict):
    response = _call("tools/call", {"name": name, "arguments": arguments})
    payload = json.loads(response["result"]["content"][0]["text"])
    return response, payload


def test_initialize() -> None:
    response = _call("initialize", {"protocolVersion": "2025-06-18"})
    result = response["result"]
    assert result["serverInfo"]["name"] == "luacraft"
    assert result["capabilities"]["tools"]["listChanged"] is False


def test_ping() -> None:
    assert _call("ping")["result"] == {}


def test_initialized_notification_has_no_response() -> None:
    assert handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list() -> None:
    names = {tool["name"] for tool in _call("tools/list")["result"]["tools"]}
    assert {
        "luacraft_compile",
        "luacraft_check",
        "luacraft_api",
        "luacraft_content",
        "luacraft_sandbox",
        "luacraft_list_examples",
    } <= names


def test_unknown_method_is_an_error() -> None:
    response = _call("no/such")
    assert response["error"]["code"] == -32601


def test_compile_all_editions() -> None:
    response, payload = _tool(
        "luacraft_compile",
        {"source": SOURCE, "namespace": "test", "debug": True},
    )
    assert response["result"]["isError"] is False
    assert set(payload["editions"]) == {"java", "bedrock", "luanti"}
    for entry in payload["editions"].values():
        assert entry["ok"] is True
        assert entry["sandbox"]["ok"] is True
        assert entry["trace"]["enabled"] is True


def test_compile_includes_archives() -> None:
    _, payload = _tool(
        "luacraft_compile",
        {"source": SOURCE, "namespace": "test", "include_archives": True},
    )
    assert "luacraft-java.zip" in payload["archives"]
    assert "luacraft.mcaddon" in payload["archives"]
    data = base64.b64decode(payload["archives"]["luacraft-java.zip"])
    assert data.startswith(b"PK")


def test_compile_writes_to_output_dir(tmp_path) -> None:
    _, payload = _tool(
        "luacraft_compile",
        {"source": SOURCE, "namespace": "test", "output_dir": str(tmp_path)},
    )
    assert (tmp_path / "luacraft-java.zip").exists()
    assert "java" in payload["written"]


def test_check_reports_capabilities() -> None:
    _, payload = _tool("luacraft_check", {"source": SOURCE})
    assert "portable_capabilities" in payload
    assert set(payload["editions"]) == {"java", "bedrock", "luanti"}


def test_check_warns_about_a_partial_capability() -> None:
    _, payload = _tool(
        "luacraft_check",
        {"source": 'setblock(0, 64, 0, "minecraft:stone")', "target": "bedrock"},
    )
    assert payload["editions"]["bedrock"]["ok"] is True
    assert payload["editions"]["bedrock"]["warnings"]


def test_api_lists_primitives() -> None:
    _, payload = _tool("luacraft_api", {})
    assert payload["primitives"]
    assert any(p["name"] == "say" for p in payload["primitives"])


def test_content_lists_declarations() -> None:
    _, payload = _tool("luacraft_content", {"source": SOURCE, "namespace": "test"})
    assert payload["items"]["ruby"]["identifier"] == "test:ruby"


def test_sandbox_from_source() -> None:
    _, payload = _tool("luacraft_sandbox", {"source": SOURCE})
    assert payload["java"]["ok"] is True


def test_sandbox_from_archive(tmp_path) -> None:
    _, compiled = _tool(
        "luacraft_compile",
        {"source": SOURCE, "output_dir": str(tmp_path), "target": "java"},
    )
    archive = compiled["written"]["java"]
    _, payload = _tool("luacraft_sandbox", {"archive": archive})
    assert payload["edition"] == "java"
    assert payload["ok"] is True


def test_missing_source_is_a_tool_error() -> None:
    response = _call("tools/call", {"name": "luacraft_compile", "arguments": {}})
    assert response["result"]["isError"] is True
    assert "source" in response["result"]["content"][0]["text"]


def test_unknown_tool_is_an_error() -> None:
    response = _call("tools/call", {"name": "nope", "arguments": {}})
    assert response["result"]["isError"] is True


def test_examples_are_listed() -> None:
    _, payload = _tool("luacraft_list_examples", {})
    assert isinstance(payload["examples"], dict)


def test_stdio_transport() -> None:
    stdin = io.BytesIO(
        b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
        b'not json\n'
        b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
    )
    stdout = io.BytesIO()
    assert serve(stdin, stdout) == 0
    lines = [json.loads(l) for l in stdout.getvalue().decode().splitlines()]
    assert lines[0]["result"] == {}
    assert lines[1]["error"]["code"] == -32700
    assert lines[2]["result"]["tools"]


def test_stdio_accepts_text_streams() -> None:
    stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
    stdout = io.StringIO()
    assert serve(stdin, stdout) == 0
    assert json.loads(stdout.getvalue())["result"] == {}
