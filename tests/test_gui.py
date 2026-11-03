"""Tests for the local GUI."""

from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request

import pytest

from luacraft import __version__
from luacraft.gui import check_request, compile_request, make_server
from luacraft.textures import placeholder_png

SOURCE = """
items.ruby = { id = "test:ruby", texture = "ruby" }
onTick(function()
  forEachPlayer(function(p) say("hola") end)
end)
"""


@pytest.fixture()
def server():
    httpd = make_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, response.headers.get("Content-Type", ""), response.read()


def _post(url, payload):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, json.loads(response.read())


def test_index_serves_the_page(server) -> None:
    status, content_type, body = _get(server + "/")
    assert status == 200
    assert "text/html" in content_type
    text = body.decode("utf-8")
    assert "LuaCraft" in text
    assert __version__ in text
    assert "__VERSION__" not in text


def test_health(server) -> None:
    status, _, body = _get(server + "/api/health")
    assert status == 200
    assert json.loads(body) == {"ok": True, "version": __version__}


def test_examples(server) -> None:
    status, _, body = _get(server + "/api/examples")
    assert status == 200
    assert isinstance(json.loads(body)["examples"], dict)


def test_unknown_route_is_404(server) -> None:
    with pytest.raises(urllib.error.HTTPError) as error:
        _get(server + "/nope")
    assert error.value.code == 404


def test_compile_endpoint(server) -> None:
    status, payload = _post(
        server + "/api/compile",
        {"source": SOURCE, "namespace": "test", "debug": True},
    )
    assert status == 200
    assert payload["ok"] is True
    assert set(payload["editions"]) == {"java", "bedrock", "luanti"}
    assert payload["archives"]
    assert payload["editions"]["java"]["trace"]["enabled"] is True


def test_check_endpoint_has_no_archives(server) -> None:
    status, payload = _post(
        server + "/api/check", {"source": SOURCE, "namespace": "test"}
    )
    assert status == 200
    assert "archives" not in payload
    assert "files" not in payload["editions"]["java"]


def test_invalid_json_is_400(server) -> None:
    request = urllib.request.Request(
        server + "/api/compile",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=10)
    assert error.value.code == 400


def test_compile_request_with_uploaded_texture() -> None:
    data = base64.b64encode(placeholder_png("ruby")).decode("ascii")
    payload = compile_request(
        {
            "source": SOURCE,
            "namespace": "test",
            "targets": ["luanti"],
            "textures": [{"name": "ruby.png", "data": data}],
        }
    )
    assert payload["ok"] is True
    files = [f["path"] for f in payload["editions"]["luanti"]["files"]]
    assert "textures/ruby.png" in files


def test_check_request_skips_files_and_archives() -> None:
    payload = check_request({"source": SOURCE, "namespace": "test"})
    assert "archives" not in payload
    for entry in payload["editions"].values():
        assert "files" not in entry
        assert "sandbox" not in entry
