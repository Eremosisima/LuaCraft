"""The local LuaCraft GUI.

A single page editor served by the Python standard library.  There is no
build step, no npm, no CDN and no network access beyond ``127.0.0.1``: the
HTML, the CSS and the JavaScript all live in this file.

    luacraft gui

Features
--------

* Edit Lua in the browser and compile to any of the three editions.
* Upload PNG textures; missing textures get deterministic placeholders.
* See the diagnostics of every edition side by side.
* Inspect the generated files, the sandbox report and the debug trace.
* Download the packs: a datapack, an add-on and a Luanti mod.

The server binds to the loopback interface by default.  It is a development
tool, not a public web service, and it has no authentication because it is
not meant to leave the machine.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import __version__
from .api import Edition
from .pipeline import archive_bundle, compile_all
from .sandbox import validate_build
from .textures import TextureSet

#: Largest request body the GUI accepts, in bytes.
MAX_BODY = 8 * 1024 * 1024

#: Longest file preview the GUI sends back, in characters.
MAX_PREVIEW = 200_000


def _edition(value: str) -> Edition | None:
    return {
        "java": Edition.JAVA,
        "bedrock": Edition.BEDROCK,
        "luanti": Edition.LUANTI,
    }.get(value)


def _textures(payload: list[dict[str, Any]]) -> TextureSet:
    images: dict[str, bytes] = {}
    for entry in payload or []:
        name = re.sub(r"[^a-z0-9_.-]+", "_", str(entry.get("name", "")).lower())
        if name.endswith(".png"):
            name = name[:-4]
        if not name:
            continue
        try:
            data = base64.b64decode(str(entry.get("data", "")))
        except (ValueError, TypeError):
            continue
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            images[name] = data
    return TextureSet(images=images, source="upload")


def _file_payload(path: str, body: bytes | str) -> dict[str, Any]:
    if isinstance(body, str):
        data = body.encode("utf-8")
        text = body if len(body) <= MAX_PREVIEW else None
        return {
            "path": path,
            "size": len(data),
            "kind": "text",
            "text": text,
            "truncated": text is None,
        }
    return {
        "path": path,
        "size": len(body),
        "kind": "image",
        "data_url": (
            "data:image/png;base64," + base64.b64encode(body).decode("ascii")
            if len(body) <= MAX_PREVIEW
            else None
        ),
    }


def compile_request(payload: dict[str, Any], *, debug: bool = False) -> dict[str, Any]:
    """Run one GUI request and build the JSON response."""
    source = str(payload.get("source", ""))
    namespace = str(payload.get("namespace") or "luacraft")
    pack_name = str(payload.get("pack_name") or "LuaCraft Mod")
    targets = payload.get("targets") or ["java", "bedrock", "luanti"]
    editions = [edition for edition in (_edition(t) for t in targets) if edition]
    if not editions:
        editions = [Edition.JAVA, Edition.BEDROCK, Edition.LUANTI]

    uploaded = _textures(payload.get("textures") or [])
    generate = bool(payload.get("generate_textures", True))
    want_sandbox = bool(payload.get("sandbox", True))
    want_debug = bool(payload.get("debug", False)) or debug

    started = time.perf_counter()
    results = compile_all(
        source,
        namespace=namespace,
        pack_name=pack_name,
        generate_textures=generate,
        textures=uploaded or None,
        editions=editions,
        debug=want_debug,
    )
    elapsed = (time.perf_counter() - started) * 1000.0

    response: dict[str, Any] = {
        "ok": all(result.ok for result in results.values()),
        "version": __version__,
        "elapsed_ms": round(elapsed, 2),
        "namespace": namespace,
        "editions": {},
        "archives": {},
    }

    for edition, result in results.items():
        entry = {
            "label": edition.label,
            "ok": result.ok,
            "warnings": [d.as_dict() for d in result.warnings],
            "errors": [d.as_dict() for d in result.errors],
            "files": [
                _file_payload(path, body)
                for path, body in sorted(result.files.items())
            ],
            "resources": [
                _file_payload(path, body)
                for path, body in sorted(result.resources.items())
            ],
            "content": result.content.summary(),
        }
        if want_sandbox:
            entry["sandbox"] = validate_build(result).as_dict()
        if want_debug:
            entry["trace"] = result.trace.as_dict()
        response["editions"][edition.value] = entry

    if any(result.ok for result in results.values()):
        archives = archive_bundle(results, namespace=namespace)
        response["archives"] = {
            name: base64.b64encode(data).decode("ascii")
            for name, data in archives.items()
        }

    return response


def check_request(payload: dict[str, Any], *, debug: bool = False) -> dict[str, Any]:
    """Compile without archives, which is what the check button needs."""
    trimmed = dict(payload)
    trimmed["sandbox"] = False
    response = compile_request(trimmed, debug=debug)
    response.pop("archives", None)
    for entry in response["editions"].values():
        entry.pop("files", None)
        entry.pop("resources", None)
    return response


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    server_version = f"LuaCraft/{__version__}"
    protocol_version = "HTTP/1.1"

    #: Set by :func:`run_gui`.
    debug = False

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", INDEX_HTML.replace(
                "__VERSION__", __version__
            ).encode("utf-8"))
            return
        if route == "/api/health":
            self._send_json(200, {"ok": True, "version": __version__})
            return
        if route == "/api/examples":
            self._send_json(200, {"examples": _examples()})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route not in ("/api/compile", "/api/check"):
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._send_json(400, {"error": "invalid body size"})
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": f"invalid JSON: {exc}"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "the body must be a JSON object"})
            return

        try:
            if route == "/api/compile":
                response = compile_request(payload, debug=self.debug)
            else:
                response = check_request(payload, debug=self.debug)
        except Exception as exc:  # pragma: no cover - defensive
            self._send_json(
                500, {"error": f"{type(exc).__name__}: {exc}", "ok": False}
            )
            return
        self._send_json(200, response)

    # -- helpers ---------------------------------------------------------

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body)

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if self.debug:  # pragma: no cover - only for humans
            super().log_message(format, *args)


def _examples() -> dict[str, str]:
    directory = Path(__file__).resolve().parents[2] / "examples"
    if not directory.is_dir():
        directory = Path.cwd() / "examples"
    examples: dict[str, str] = {}
    if directory.is_dir():
        for path in sorted(directory.glob("*.lua")):
            examples[path.name] = path.read_text(encoding="utf-8")
    return examples


def make_server(
    host: str = "127.0.0.1", port: int = 8765, *, debug: bool = False
) -> ThreadingHTTPServer:
    """Create the server without starting it, which the tests use."""
    handler = type("BoundHandler", (_Handler,), {"debug": debug})
    return ThreadingHTTPServer((host, port), handler)


def run_gui(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    open_browser: bool = True,
    debug: bool = False,
) -> int:
    """Serve the GUI until interrupted."""
    server = make_server(host, port, debug=debug)
    actual_host, actual_port = server.server_address[:2]
    url = f"http://{actual_host}:{actual_port}/"
    print(f"LuaCraft GUI {__version__} listening on {url}")
    print("press Ctrl+C to stop")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------

INDEX_HTML = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LuaCraft __VERSION__</title>
<style>
  :root {
    --bg: #0f1115; --panel: #171a21; --panel2: #1e222b; --line: #2a2f3a;
    --text: #e6e9ef; --muted: #9aa4b2; --accent: #7dd3fc; --accent2: #a78bfa;
    --ok: #4ade80; --warn: #fbbf24; --err: #f87171;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  header {
    display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
    padding: 14px 20px; border-bottom: 1px solid var(--line); background: var(--panel);
  }
  header h1 { font-size: 17px; margin: 0; letter-spacing: .3px; }
  header h1 span { color: var(--accent); }
  header .tag { color: var(--muted); font-size: 12px; }
  main { display: grid; grid-template-columns: minmax(320px, 1fr) minmax(380px, 1.1fr); gap: 16px; padding: 16px; }
  @media (max-width: 980px) { main { grid-template-columns: 1fr; } }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
  .card h2 { margin: 0; padding: 10px 14px; font-size: 13px; text-transform: uppercase; letter-spacing: .8px; color: var(--muted); border-bottom: 1px solid var(--line); }
  .card .body { padding: 12px 14px; }
  textarea {
    width: 100%; min-height: 380px; resize: vertical; padding: 12px;
    background: #0b0d11; color: #dbe4f0; border: 1px solid var(--line); border-radius: 8px;
    font: 13px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace; tab-size: 2;
  }
  textarea:focus, input:focus, select:focus { outline: 1px solid var(--accent); }
  label.field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
  input[type=text], select {
    background: #0b0d11; color: var(--text); border: 1px solid var(--line);
    border-radius: 6px; padding: 7px 9px; font: inherit;
  }
  .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: end; }
  .row > * { flex: 0 1 auto; }
  .row .grow { flex: 1 1 160px; }
  .checks { display: flex; gap: 14px; flex-wrap: wrap; padding: 6px 0; }
  .checks label { display: flex; align-items: center; gap: 6px; color: var(--text); font-size: 13px; cursor: pointer; }
  .buttons { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
  button {
    border: 1px solid var(--line); background: var(--panel2); color: var(--text);
    padding: 8px 14px; border-radius: 7px; cursor: pointer; font: inherit;
  }
  button:hover { border-color: var(--accent); }
  button.primary { background: linear-gradient(120deg, #0ea5e9, #7c3aed); border-color: transparent; font-weight: 600; }
  button:disabled { opacity: .5; cursor: wait; }
  .drops {
    border: 1px dashed var(--line); border-radius: 8px; padding: 12px; color: var(--muted);
    text-align: center; cursor: pointer; font-size: 12px;
  }
  .drops.over { border-color: var(--accent); color: var(--accent); }
  .thumbs { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
  .thumb { width: 46px; text-align: center; font-size: 10px; color: var(--muted); }
  .thumb img { width: 42px; height: 42px; image-rendering: pixelated; border: 1px solid var(--line); border-radius: 5px; background: #000; }
  .edition { border: 1px solid var(--line); border-radius: 8px; margin-bottom: 12px; overflow: hidden; }
  .edition .head { display: flex; align-items: center; gap: 10px; padding: 9px 12px; background: var(--panel2); cursor: pointer; }
  .edition .head strong { font-size: 13px; }
  .badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); }
  .badge.ok { color: var(--ok); border-color: #14532d; }
  .badge.bad { color: var(--err); border-color: #7f1d1d; }
  .badge.warn { color: var(--warn); border-color: #78350f; }
  .edition .inner { padding: 10px 12px; display: none; }
  .edition.open .inner { display: block; }
  .diag { padding: 7px 9px; border-left: 3px solid var(--line); margin: 6px 0; background: #12151c; border-radius: 0 6px 6px 0; }
  .diag.warning { border-color: var(--warn); }
  .diag.error { border-color: var(--err); }
  .diag .meta { color: var(--muted); font-size: 11px; }
  .diag code { color: var(--accent); }
  .tabs { display: flex; gap: 6px; margin: 10px 0 6px; flex-wrap: wrap; }
  .tabs button { padding: 5px 10px; font-size: 12px; }
  .tabs button.active { border-color: var(--accent); color: var(--accent); }
  .filelist { max-height: 230px; overflow: auto; border: 1px solid var(--line); border-radius: 6px; }
  .filelist div { padding: 5px 8px; font: 12px ui-monospace, Consolas, monospace; border-bottom: 1px solid #1b1f27; cursor: pointer; display: flex; justify-content: space-between; gap: 8px; }
  .filelist div:hover { background: #1b202a; }
  .filelist .size { color: var(--muted); }
  pre { margin: 8px 0 0; padding: 10px; background: #0b0d11; border: 1px solid var(--line); border-radius: 6px; max-height: 300px; overflow: auto; font: 12px/1.45 ui-monospace, Consolas, monospace; white-space: pre-wrap; }
  .muted { color: var(--muted); }
  .sandbox-line { font: 12px ui-monospace, Consolas, monospace; padding: 2px 0; }
  .sandbox-line.fail { color: var(--err); }
  .sandbox-line.warn { color: var(--warn); }
  .sandbox-line.ok { color: var(--ok); }
  .downloads { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
  .downloads button { font-size: 12px; }
  .empty { color: var(--muted); font-size: 13px; padding: 10px 0; }
  .status { font-size: 12px; color: var(--muted); }
  .toast { position: fixed; right: 18px; bottom: 18px; background: var(--panel2); border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; font-size: 12px; opacity: 0; transition: opacity .2s; pointer-events: none; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>
<header>
  <h1>Lua<span>Craft</span></h1>
  <span class="tag">Lua &rarr; Java &middot; Bedrock &middot; Luanti &mdash; v__VERSION__</span>
  <span class="tag" id="health">local &middot; offline</span>
</header>

<main>
  <section class="card">
    <h2>Editor</h2>
    <div class="body">
      <div class="row" style="margin-bottom:10px">
        <label class="field grow">Ejemplo
          <select id="example"><option value="">— cargar ejemplo —</option></select>
        </label>
        <label class="field">Namespace
          <input id="namespace" type="text" value="luacraft" spellcheck="false">
        </label>
        <label class="field grow">Nombre
          <input id="packName" type="text" value="LuaCraft Mod">
        </label>
      </div>
      <textarea id="source" spellcheck="false" placeholder="onTick(function() ... end)"></textarea>

      <div class="checks">
        <label><input type="checkbox" id="tJava" checked> Java</label>
        <label><input type="checkbox" id="tBedrock" checked> Bedrock</label>
        <label><input type="checkbox" id="tLuanti" checked> Luanti</label>
        <label><input type="checkbox" id="genTex" checked> Generar texturas</label>
        <label><input type="checkbox" id="sandbox" checked> Sandbox</label>
        <label><input type="checkbox" id="debug"> Debug</label>
      </div>

      <div class="drops" id="drop">Arrastra archivos PNG aquí o haz clic para elegirlos (opcional)</div>
      <input type="file" id="files" accept="image/png" multiple hidden>
      <div class="thumbs" id="thumbs"></div>

      <div class="buttons">
        <button class="primary" id="compile">Compilar</button>
        <button id="check">Analizar</button>
        <span class="status" id="status"></span>
      </div>
    </div>
  </section>

  <section class="card">
    <h2>Resultado</h2>
    <div class="body" id="results">
      <div class="empty">Compila o analiza un archivo para ver las ediciones aquí.</div>
    </div>
    <div class="body" style="padding-top:0"><div class="downloads" id="downloads"></div></div>
  </section>
</main>

<div class="toast" id="toast"></div>

<script>
const $ = (id) => document.getElementById(id);
let uploaded = [];

function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 2200);
}

async function loadExamples() {
  try {
    const r = await fetch("/api/examples");
    const data = await r.json();
    for (const name of Object.keys(data.examples || {})) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      $("example").appendChild(option);
    }
    window._examples = data.examples || {};
  } catch (e) { /* the GUI still works without examples */ }
}

$("example").addEventListener("change", (event) => {
  const name = event.target.value;
  if (name && window._examples && window._examples[name] != null) {
    $("source").value = window._examples[name];
  }
});

// -- textures -------------------------------------------------------------
const drop = $("drop");
drop.addEventListener("click", () => $("files").click());
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => {
  e.preventDefault(); drop.classList.remove("over");
  addFiles(e.dataTransfer.files);
});
$("files").addEventListener("change", (e) => addFiles(e.target.files));

function addFiles(fileList) {
  for (const file of fileList) {
    if (!file.type.includes("png") && !file.name.endsWith(".png")) continue;
    const reader = new FileReader();
    reader.onload = () => {
      const data = String(reader.result).split(",")[1] || "";
      const name = file.name.replace(/\.png$/i, "");
      uploaded = uploaded.filter((t) => t.name !== name);
      uploaded.push({ name, data, url: reader.result });
      renderThumbs();
    };
    reader.readAsDataURL(file);
  }
}

function renderThumbs() {
  $("thumbs").innerHTML = "";
  for (const texture of uploaded) {
    const div = document.createElement("div");
    div.className = "thumb";
    div.innerHTML = `<img alt="" src="${texture.url}"><div>${texture.name}</div>`;
    div.title = "quitar";
    div.onclick = () => { uploaded = uploaded.filter(t => t !== texture); renderThumbs(); };
    $("thumbs").appendChild(div);
  }
}

// -- request --------------------------------------------------------------
function payload() {
  const targets = [];
  if ($("tJava").checked) targets.push("java");
  if ($("tBedrock").checked) targets.push("bedrock");
  if ($("tLuanti").checked) targets.push("luanti");
  return {
    source: $("source").value,
    namespace: $("namespace").value || "luacraft",
    pack_name: $("packName").value || "LuaCraft Mod",
    targets,
    textures: uploaded.map(({name, data}) => ({name, data})),
    generate_textures: $("genTex").checked,
    sandbox: $("sandbox").checked,
    debug: $("debug").checked,
  };
}

async function run(route) {
  const buttons = [$("compile"), $("check")];
  buttons.forEach(b => b.disabled = true);
  $("status").textContent = "trabajando…";
  try {
    const response = await fetch(route, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload()),
    });
    const data = await response.json();
    if (data.error) throw new Error(data.error);
    render(data);
    $("status").textContent = `${data.elapsed_ms} ms`;
  } catch (error) {
    $("results").innerHTML = `<div class="diag error">${error.message}</div>`;
    $("status").textContent = "error";
  } finally {
    buttons.forEach(b => b.disabled = false);
  }
}

$("compile").onclick = () => run("/api/compile");
$("check").onclick = () => run("/api/check");

// -- rendering ------------------------------------------------------------
function esc(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function render(data) {
  const results = $("results");
  results.innerHTML = "";
  $("downloads").innerHTML = "";
  const editions = Object.entries(data.editions || {});
  if (!editions.length) { results.innerHTML = `<div class="empty">sin ediciones</div>`; return; }

  for (const [key, entry] of editions) {
    const errors = entry.errors || [];
    const warnings = entry.warnings || [];
    const badge = !entry.ok ? `<span class="badge bad">error</span>`
      : warnings.length ? `<span class="badge warn">${warnings.length} aviso(s)</span>`
      : `<span class="badge ok">ok</span>`;

    const card = document.createElement("div");
    card.className = "edition";
    card.innerHTML = `
      <div class="head"><strong>${esc(entry.label || key)}</strong>${badge}
        <span class="muted" style="font-size:11px">${(entry.files||[]).length} archivos</span>
      </div>
      <div class="inner">
        <div class="diags"></div>
        <div class="tabs"></div>
        <div class="panel"></div>
      </div>`;
    const diags = card.querySelector(".diags");
    if (!errors.length && !warnings.length) {
      diags.innerHTML = `<div class="empty">Sin diagnósticos.</div>`;
    }
    for (const d of [...errors, ...warnings]) {
      const div = document.createElement("div");
      div.className = "diag " + (d.severity === "error" ? "error" : "warning");
      div.innerHTML = `<div class="meta">${esc(d.file || "")}${d.line ? ":" + d.line : ""} &middot; ${esc(d.category || "")}</div>
        <div>${esc(d.message)}</div>
        ${d.snippet ? `<div class="meta"><code>${esc(d.snippet)}</code></div>` : ""}
        ${d.hint ? `<div class="meta">hint: ${esc(d.hint)}</div>` : ""}`;
      diags.appendChild(div);
    }

    const tabs = card.querySelector(".tabs");
    const panel = card.querySelector(".panel");
    const views = {
      files: () => fileView(entry, panel),
      sandbox: () => sandboxView(entry.sandbox, panel),
      debug: () => debugView(entry.trace, panel),
    };
    for (const name of Object.keys(views)) {
      if (name === "sandbox" && !entry.sandbox) continue;
      if (name === "debug" && !entry.trace) continue;
      const button = document.createElement("button");
      button.textContent = name;
      button.onclick = () => {
        tabs.querySelectorAll("button").forEach(b => b.classList.remove("active"));
        button.classList.add("active");
        panel.innerHTML = "";
        views[name]();
      };
      tabs.appendChild(button);
    }
    const first = tabs.querySelector("button");
    if (first) first.click();

    card.querySelector(".head").onclick = () => card.classList.toggle("open");
    if (!entry.ok || warnings.length) card.classList.add("open");
    results.appendChild(card);
  }

  for (const [name, data64] of Object.entries(data.archives || {})) {
    const button = document.createElement("button");
    button.textContent = "Descargar " + name;
    button.onclick = () => download(name, data64);
    $("downloads").appendChild(button);
  }
  if (!Object.keys(data.archives || {}).length) {
    $("downloads").innerHTML = `<span class="muted" style="font-size:12px">Analizar no genera archivos; usa Compilar.</span>`;
  }
}

function fileView(entry, panel) {
  const all = [...(entry.files || []), ...(entry.resources || [])];
  if (!all.length) { panel.innerHTML = `<div class="empty">Sin archivos.</div>`; return; }
  const list = document.createElement("div");
  list.className = "filelist";
  const preview = document.createElement("pre");
  preview.textContent = "Selecciona un archivo.";
  for (const file of all) {
    const row = document.createElement("div");
    row.innerHTML = `<span>${esc(file.path)}</span><span class="size">${file.size} B</span>`;
    row.onclick = () => {
      if (file.kind === "image") {
        preview.innerHTML = file.data_url ? `<img alt="" style="max-width:100%;image-rendering:pixelated" src="${file.data_url}">` : "imagen demasiado grande";
      } else {
        preview.textContent = file.text == null ? "(demasiado grande para previsualizar)" : file.text;
      }
    };
    list.appendChild(row);
  }
  panel.appendChild(list);
  panel.appendChild(preview);
}

function sandboxView(report, panel) {
  if (!report) { panel.innerHTML = `<div class="empty">Sandbox desactivado.</div>`; return; }
  const head = `<div class="sandbox-line ${report.ok ? "ok" : "fail"}">${report.ok ? "sandbox ok" : "sandbox: " + report.failures.length + " fallo(s)"}</div>`;
  const lines = (report.checks || []).map(c => {
    const cls = c.ok ? "ok" : (c.severity === "warning" ? "warn" : "fail");
    return `<div class="sandbox-line ${cls}">[${c.ok ? "ok" : (c.severity === "warning" ? "warn" : "fail")}] ${esc(c.name)}${c.detail ? " — " + esc(c.detail) : ""}</div>`;
  }).join("");
  panel.innerHTML = head + lines;
}

function debugView(trace, panel) {
  if (!trace || !trace.enabled) { panel.innerHTML = `<div class="empty">Debug desactivado.</div>`; return; }
  const lines = (trace.events || []).map(e => `${String(e.duration_ms).padStart(9)} ms  ${e.phase}${e.detail ? "  (" + e.detail + ")" : ""}`).join("\n");
  const info = Object.entries(trace.data || {}).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join("\n");
  panel.innerHTML = `<pre>${esc(lines + "\n\n" + info + "\n\ntotal: " + trace.total_ms + " ms")}</pre>`;
}

function download(name, data64) {
  const bytes = atob(data64);
  const buffer = new Uint8Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) buffer[i] = bytes.charCodeAt(i);
  const url = URL.createObjectURL(new Blob([buffer], {type: "application/zip"}));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 3000);
  toast("descargado " + name);
}

// -- start ----------------------------------------------------------------
loadExamples();
const starter = `items.ruby = {
  id = "luacraft:ruby",
  max_stack = 1,
  texture = "ruby",
}

onTick(function()
  forEachPlayer(function(player)
    score(player, "coins", "add", 1)
    if score(player, "coins") >= 100 then
      give(player, "luacraft:ruby", 1)
      score(player, "coins", "remove", 100)
      message(player, "Has ganado una gema!")
    end
  end)
end)
`;
$("source").value = starter;
</script>
</body>
</html>
"""
