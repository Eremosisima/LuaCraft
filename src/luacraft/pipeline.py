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
from .diagnostics import DiagnosticBag, Severity
from .java_backend import BackendResult, JavaBackend
from .luaparse import Chunk, LuaSyntaxError, parse


@dataclass
class BuildResult:
    """The outcome of one compilation."""

    edition: Edition
    files: dict[str, str] = field(default_factory=dict)
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)
    #: Set when the source could not be parsed.
    parse_error: LuaSyntaxError | None = None
    #: The declarative content the source declared.
    content: Content = field(default_factory=Content)

    @property
    def ok(self) -> bool:
        return self.parse_error is None and not self.diagnostics.has_errors

    @property
    def warnings(self) -> list:
        return self.diagnostics.warnings

    @property
    def errors(self) -> list:
        return self.diagnostics.errors


def build(
    source: str,
    edition: Edition,
    *,
    namespace: str = "luacraft",
    pack_name: str = "LuaCraft Mod",
    pack_format: int = 48,
    min_engine_version: tuple[int, int, int] = (1, 21, 0),
    source_name: str = "<source>",
) -> BuildResult:
    """Compile Lua source for one edition.

    The returned result always carries the diagnostics, even when the build
    failed, so the caller can explain what went wrong.
    """
    diagnostics = DiagnosticBag()
    result = BuildResult(edition=edition, diagnostics=diagnostics)

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
        return result

    analyser = Analyser(edition, diagnostics)
    analyser.analyse(chunk)

    content = read_content(chunk.body, namespace, diagnostics)

    if edition is Edition.JAVA:
        backend = JavaBackend(namespace=namespace, pack_format=pack_format)
        backend_result = backend.compile(chunk, content)
    else:
        backend = BedrockBackend(
            namespace=namespace,
            pack_name=pack_name,
            min_engine_version=min_engine_version,
        )
        backend_result = backend.compile(chunk, content)

    for generated in backend_result.files:
        result.files[generated.path] = generated.content

    # The backend may report content that cannot be expressed in this
    # edition, so its diagnostics join the analyser's.
    diagnostics.extend(backend.diagnostics)
    result.content = content
    return result


def write_pack(
    result: BuildResult,
    output: Path,
    *,
    pack_name: str = "LuaCraft Mod",
) -> Path:
    """Write the build output as a zip archive.

    Java Edition gets a datapack zip.  Bedrock gets an add-on containing both
    a behaviour pack and a resource pack.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(result.files.items()):
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


def report_json(result: BuildResult) -> str:
    """Render the build result as JSON, for CI consumption."""
    payload = {
        "edition": result.edition.value,
        "ok": result.ok,
        "warnings": [d.as_dict() for d in result.warnings],
        "errors": [d.as_dict() for d in result.errors],
        "files": sorted(result.files),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)
