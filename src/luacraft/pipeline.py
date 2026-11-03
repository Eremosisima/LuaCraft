"""The compilation pipeline.

Ties the parser, the analyser and the backends together, and packages the
result into a ``.mcpack`` (one pack) or ``.mcaddon`` (behaviour plus resource
pack).

Everything here works offline: no network access, no asset downloads, no
vanilla game files.  The mappings the tool needs live in the repository.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .analyser import Analyser
from .api import Edition
from .bedrock_backend import BedrockBackend
from .content import Content, read_content
from .debug import Trace
from .diagnostics import DiagnosticBag, Severity
from .java_backend import BackendResult, JavaBackend
from .luanti_backend import LuantiBackend
from .luaparse import Chunk, LuaSyntaxError, parse
from .textures import TextureSet, prepare_textures, render_textures


@dataclass
class BuildResult:
    """The outcome of one compilation."""

    edition: Edition
    #: Generated files.  Text files are ``str``, images are ``bytes``.
    files: dict[str, bytes | str] = field(default_factory=dict)
    #: Java Edition needs a separate resource pack, which lives here so it is
    #: never mixed into the datapack archive.
    resources: dict[str, bytes | str] = field(default_factory=dict)
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)
    #: Set when the source could not be parsed.
    parse_error: LuaSyntaxError | None = None
    #: The declarative content the source declared.
    content: Content = field(default_factory=Content)
    #: The textures that were used, if any.
    textures: TextureSet | None = None
    #: Phase timings and counters, filled when debug is enabled.
    trace: Trace = field(default_factory=Trace)

    @property
    def ok(self) -> bool:
        return self.parse_error is None and not self.diagnostics.has_errors

    @property
    def warnings(self) -> list:
        return self.diagnostics.warnings

    @property
    def errors(self) -> list:
        return self.diagnostics.errors

    def file_bytes(self, path: str) -> bytes:
        """Read one generated file as bytes, whatever it holds."""
        value = self.files[path]
        return value.encode("utf-8") if isinstance(value, str) else value

    def text_files(self) -> dict[str, str]:
        """Only the text files, which is what the sandbox parses."""
        return {
            path: value
            for path, value in self.files.items()
            if isinstance(value, str)
        }


def build(
    source: str,
    edition: Edition,
    *,
    namespace: str = "luacraft",
    pack_name: str = "LuaCraft Mod",
    pack_format: int = 48,
    min_engine_version: tuple[int, int, int] = (1, 21, 0),
    source_name: str = "<source>",
    texture_dir: Path | None = None,
    generate_textures: bool = False,
    textures: TextureSet | None = None,
    models: dict[str, str] | None = None,
    debug: bool = False,
) -> BuildResult:
    """Compile Lua source for one edition.

    The returned result always carries the diagnostics, even when the build
    failed, so the caller can explain what went wrong.

    When ``generate_textures`` is set, or a ``texture_dir`` is given, the
    declared content is packaged with images.  Missing images get a
    deterministic placeholder rather than a download, so a build is always
    self contained and offline.

    ``debug`` records the phase timings and intermediate counters in
    :attr:`BuildResult.trace`.
    """
    diagnostics = DiagnosticBag()
    trace = Trace(enabled=debug)
    result = BuildResult(edition=edition, diagnostics=diagnostics, trace=trace)

    trace.set("edition", edition.value)
    trace.set("namespace", namespace)
    trace.set("source_chars", len(source))
    trace.set("source_lines", source.count("\n") + 1)

    with trace.phase("parse") as state:
        try:
            chunk: Chunk = parse(source)
        except LuaSyntaxError as exc:
            result.parse_error = exc
            diagnostics.error(
                __import__("luacraft.diagnostics", fromlist=["Category"]).Category.SYNTAX,
                exc.message,
                exc.line,
                snippet=exc.snippet,
                hint="check the Lua syntax of the source file",
            )
            state["detail"] = f"failed at line {exc.line}"
            return result
        state["detail"] = f"{len(chunk.body)} top level statements"
    trace.set("statements", len(chunk.body))

    with trace.phase("analyse") as state:
        analyser = Analyser(edition, diagnostics)
        analyser.analyse(chunk)
        state["detail"] = (
            f"{len(diagnostics.errors)} errors, "
            f"{len(diagnostics.warnings)} warnings"
        )

    with trace.phase("content") as state:
        content = read_content(chunk.body, namespace, diagnostics)
        result.content = content
        state["detail"] = ", ".join(
            f"{count} {name}" for name, count in content.summary().items()
        ) or "none declared"
    trace.set("content", content.summary())
    wants_textures = (
        generate_textures or texture_dir is not None or textures is not None
    )

    with trace.phase(f"backend:{edition.value}") as state:
        if edition is Edition.JAVA:
            backend = JavaBackend(namespace=namespace, pack_format=pack_format)
            backend_result = backend.compile(
                chunk, content, with_textures=wants_textures
            )
        elif edition is Edition.BEDROCK:
            backend = BedrockBackend(
                namespace=namespace,
                pack_name=pack_name,
                min_engine_version=min_engine_version,
            )
            backend_result = backend.compile(chunk, content)
        else:
            backend = LuantiBackend(
                namespace=namespace,
                mod_name=namespace,
                description=pack_name,
                models=dict(models or {}),
            )
            backend_result = backend.compile(chunk, content)
        state["detail"] = f"{len(backend_result.files)} files"

    for generated in backend_result.files:
        result.files[generated.path] = generated.content

    # The backend may report content that cannot be expressed in this
    # edition, so its diagnostics join the analyser's.
    diagnostics.extend(backend.diagnostics)

    if wants_textures and (content.items or content.blocks or content.entities):
        with trace.phase("textures") as state:
            texture_set = textures or prepare_textures(
                content, directory=texture_dir, generate=True
            )
            result.textures = texture_set
            rendered = render_textures(
                edition, content, texture_set, namespace, diagnostics
            )
            destination = (
                result.resources if edition is Edition.JAVA else result.files
            )
            for path, body in rendered.items():
                destination[path] = body
            state["detail"] = (
                f"{len(texture_set.images)} images, {len(rendered)} files"
            )
        trace.set("textures", len(texture_set.images))

    trace.set("files", len(result.files))
    trace.set("resources", len(result.resources))
    trace.set("errors", len(diagnostics.errors))
    trace.set("warnings", len(diagnostics.warnings))
    trace.record(
        "done",
        f"{len(result.files)} files, {len(result.resources)} resource files",
    )

    return result


def compile_all(
    source: str,
    *,
    namespace: str = "luacraft",
    pack_name: str = "LuaCraft Mod",
    source_name: str = "<source>",
    texture_dir: Path | None = None,
    generate_textures: bool = False,
    textures: TextureSet | None = None,
    editions: list[Edition] | None = None,
    models: dict[str, str] | None = None,
    debug: bool = False,
) -> dict[Edition, BuildResult]:
    """Compile the same source for several editions at once.

    This is the compatibility layer the GUI and the MCP server use: one
    source, one texture folder, one diagnostics pass per edition.
    """
    targets = editions or [Edition.JAVA, Edition.BEDROCK, Edition.LUANTI]
    results: dict[Edition, BuildResult] = {}
    for edition in targets:
        results[edition] = build(
            source,
            edition,
            namespace=namespace,
            pack_name=pack_name,
            source_name=source_name,
            texture_dir=texture_dir,
            generate_textures=generate_textures,
            textures=textures,
            models=models,
            debug=debug,
        )
    return results


def write_pack(
    result: BuildResult,
    output: Path,
    *,
    pack_name: str = "LuaCraft Mod",
) -> Path:
    """Write the build output as a zip archive.

    Java Edition gets a datapack zip.  Any resource pack files live in
    :attr:`BuildResult.resources` and are written by
    :func:`write_java_resourcepack`.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(result.files.items()):
            archive.writestr(path, content)
    return output


def write_java_resourcepack(result: BuildResult, output: Path) -> Path:
    """Write the Java resource pack that carries the textures."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(result.resources.items()):
            archive.writestr(path, content)
    return output


def write_mcaddon(
    result: BuildResult,
    output: Path,
    *,
    namespace: str = "luacraft",
) -> Path:
    """Write a Bedrock add-on: a zip holding a behaviour pack and a resource pack."""
    output.parent.mkdir(parents=True, exist_ok=True)
    behaviour_pack = f"{namespace}_bp"
    resource_pack = f"{namespace}_rp"

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(result.files.items()):
            if path == "bp/manifest.json":
                archive.writestr(f"{behaviour_pack}/manifest.json", content)
                continue
            if path == "rp/manifest.json":
                archive.writestr(f"{resource_pack}/manifest.json", content)
                continue
            if path.startswith("bp/"):
                archive.writestr(f"{behaviour_pack}/{path[3:]}", content)
                continue
            if path.startswith("rp/"):
                archive.writestr(f"{resource_pack}/{path[3:]}", content)
                continue
            archive.writestr(path, content)
    return output


def write_luanti_mod(
    result: BuildResult,
    output: Path,
    *,
    namespace: str = "luacraft",
) -> Path:
    """Write a Luanti mod as a zip archive.

    Luanti expects a directory with an ``init.lua`` and a ``mod.conf``.  The
    archive holds the same layout, so it can be extracted straight into the
    mods folder.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(result.files.items()):
            archive.writestr(f"{namespace}/{path}", content)
    return output


def write_hytale_pack(
    result: BuildResult,
    output: Path,
    *,
    namespace: str = "luacraft",
    pack_name: str = "LuaCraft",
    blockymodels: dict[str, dict] | None = None,
) -> Path:
    """Write a Hytale asset pack as a zip archive."""
    from .hytale_export import HytalePack

    exporter = HytalePack(
        namespace=namespace,
        pack_name=pack_name,
        blockymodels=dict(blockymodels or {}),
    )
    files = exporter.build(result.content, result.textures)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, body in sorted(files.items()):
            archive.writestr(path, body)
    return output


def write_godot_project(
    result: BuildResult,
    output: Path,
    *,
    namespace: str = "luacraft",
    project_name: str = "LuaCraft",
    graphics: str = "vulkan",
    models: dict[str, str] | None = None,
) -> Path:
    """Write a self contained Godot 4 project into a directory."""
    from .godot_export import GodotProject, GraphicsBackend

    exporter = GodotProject(
        project_name=project_name,
        namespace=namespace,
        graphics=GraphicsBackend.parse(graphics),
        models=dict(models or {}),
    )
    files = exporter.build(result.content, result.textures)
    output.mkdir(parents=True, exist_ok=True)
    for path, body in files.items():
        destination = output / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            destination.write_bytes(body)
        else:
            destination.write_text(body, encoding="utf-8")
    return output


def archive_hytale(
    result: BuildResult,
    *,
    namespace: str = "luacraft",
    pack_name: str = "LuaCraft",
    blockymodels: dict[str, dict] | None = None,
) -> bytes:
    """Render the Hytale asset pack in memory, for the CLI and MCP server."""
    import io

    from .hytale_export import HytalePack

    exporter = HytalePack(
        namespace=namespace,
        pack_name=pack_name,
        blockymodels=dict(blockymodels or {}),
    )
    files = exporter.build(result.content, result.textures)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, body in sorted(files.items()):
            archive.writestr(path, body)
    return buffer.getvalue()


def archive_godot(
    result: BuildResult,
    *,
    namespace: str = "luacraft",
    project_name: str = "LuaCraft",
    graphics: str = "vulkan",
    models: dict[str, str] | None = None,
) -> bytes:
    """Render the Godot project in memory."""
    import io

    from .godot_export import GodotProject, GraphicsBackend

    exporter = GodotProject(
        project_name=project_name,
        namespace=namespace,
        graphics=GraphicsBackend.parse(graphics),
        models=dict(models or {}),
    )
    files = exporter.build(result.content, result.textures)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, body in sorted(files.items()):
            archive.writestr(path, body)
    return buffer.getvalue()


def report_json(result: BuildResult) -> str:
    """Render the build result as JSON, for CI consumption."""
    payload = {
        "edition": result.edition.value,
        "ok": result.ok,
        "warnings": [d.as_dict() for d in result.warnings],
        "errors": [d.as_dict() for d in result.errors],
        "files": sorted(result.files),
        "resources": sorted(result.resources),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def write_bundle(
    results: dict[Edition, BuildResult],
    output_dir: Path,
    *,
    stem: str = "luacraft",
    namespace: str = "luacraft",
) -> dict[str, Path]:
    """Write one archive per edition into a directory.

    This is what ``luacraft build --target all`` uses: a bundle the author
    can hand to a server owner without having to run the tool again.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    java = results.get(Edition.JAVA)
    if java is not None:
        written["java"] = write_pack(java, output_dir / f"{stem}-java.zip")
        if java.resources:
            written["java-resources"] = write_java_resourcepack(
                java, output_dir / f"{stem}-java-resources.zip"
            )

    bedrock = results.get(Edition.BEDROCK)
    if bedrock is not None:
        written["bedrock"] = write_mcaddon(
            bedrock, output_dir / f"{stem}.mcaddon", namespace=namespace
        )

    luanti = results.get(Edition.LUANTI)
    if luanti is not None:
        written["luanti"] = write_luanti_mod(
            luanti, output_dir / f"{stem}-luanti.zip", namespace=namespace
        )

    return written


def archive_bundle(
    results: dict[Edition, BuildResult],
    *,
    namespace: str = "luacraft",
    stem: str = "luacraft",
) -> dict[str, bytes]:
    """Build the archives in memory, for the GUI and the MCP server.

    The layout matches :func:`write_bundle` exactly, so anything an agent
    downloads is the same file a user gets from the CLI.
    """
    import io

    archives: dict[str, bytes] = {}

    def zip_bytes(entries: dict[str, bytes | str]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path, body in sorted(entries.items()):
                archive.writestr(path, body)
        return buffer.getvalue()

    java = results.get(Edition.JAVA)
    if java is not None:
        archives[f"{stem}-java.zip"] = zip_bytes(java.files)
        if java.resources:
            archives[f"{stem}-java-resources.zip"] = zip_bytes(java.resources)

    bedrock = results.get(Edition.BEDROCK)
    if bedrock is not None:
        behaviour_pack = f"{namespace}_bp"
        resource_pack = f"{namespace}_rp"
        entries: dict[str, bytes | str] = {}
        for path, body in bedrock.files.items():
            if path == "bp/manifest.json":
                entries[f"{behaviour_pack}/manifest.json"] = body
            elif path == "rp/manifest.json":
                entries[f"{resource_pack}/manifest.json"] = body
            elif path.startswith("bp/"):
                entries[f"{behaviour_pack}/{path[3:]}"] = body
            elif path.startswith("rp/"):
                entries[f"{resource_pack}/{path[3:]}"] = body
            else:
                entries[path] = body
        archives[f"{stem}.mcaddon"] = zip_bytes(entries)

    luanti = results.get(Edition.LUANTI)
    if luanti is not None:
        archives[f"{stem}-luanti.zip"] = zip_bytes(
            {f"{namespace}/{path}": body for path, body in luanti.files.items()}
        )

    return archives
