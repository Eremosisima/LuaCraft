"""The Bedrock-to-everything bridge.

``luacraft bridge pack.mcaddon`` runs the whole pipeline:

1. read the Bedrock pack and convert its declarative content and entities,
2. compile the generated ``mod.lua`` for Java Edition, Bedrock and Luanti,
3. export a Hytale asset pack with ``.blockymodel`` models,
4. export a Godot 4 project (Vulkan or OpenGL),
5. write every archive and a JSON report that lists exactly what was kept,
   approximated or lost per target.

The converter itself lives in ``tools/convert_bedrock.py`` so it can also be
used standalone.  This module loads it by path, which keeps the bridge fully
local and dependency free.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from .api import Edition
from .pipeline import (
    archive_godot,
    archive_hytale,
    build,
    write_bundle,
    write_godot_project,
)
from .textures import TextureSet


def _load_converter() -> ModuleType:
    """Load ``tools/convert_bedrock.py`` as a module."""
    root = Path(__file__).resolve().parents[2]
    candidate = root / "tools" / "convert_bedrock.py"
    if not candidate.is_file():
        raise FileNotFoundError(
            "tools/convert_bedrock.py was not found next to the package. "
            "Run the bridge from the LuaCraft checkout, or use "
            "tools/convert_bedrock.py directly."
        )
    spec = importlib.util.spec_from_file_location("luacraft_convert_bedrock", candidate)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {candidate}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class BridgeResult:
    """Everything the bridge produced, in memory and on disk."""

    conversion: Any
    output: Path
    written: dict[str, Path] = field(default_factory=dict)
    builds: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(getattr(result, "ok", True) for result in self.builds.values())


#: Targets the bridge knows about.
TARGETS = ("java", "bedrock", "luanti", "hytale", "godot")


def _parse_targets(value: str | None) -> list[str]:
    if not value:
        return list(TARGETS)
    selected = []
    for item in value.split(","):
        name = item.strip().lower()
        if name not in TARGETS:
            raise ValueError(
                f"unknown bridge target {item!r}; use {', '.join(TARGETS)}"
            )
        selected.append(name)
    return selected


def run_bridge(
    pack: Path,
    output: Path,
    *,
    namespace: str | None = None,
    pack_name: str | None = None,
    copy_textures: bool = False,
    graphics: str = "vulkan",
    targets: str | None = None,
) -> BridgeResult:
    """Convert a pack and compile every requested target."""
    converter = _load_converter()
    selected = _parse_targets(targets)
    output.mkdir(parents=True, exist_ok=True)

    conversion = converter.convert_pack(
        pack,
        namespace=namespace,
        pack_name=pack_name,
        copy_textures=copy_textures,
    )
    source_name = output / "mod.lua"
    source_name.write_text(converter.render_lua(conversion), encoding="utf-8")
    converter.write_project(conversion, output, copy_textures=copy_textures)

    textures = TextureSet.from_directory(output / "textures")
    source = source_name.read_text(encoding="utf-8")
    resolved_namespace = namespace or conversion.namespace
    title = pack_name or conversion.pack_name
    result = BridgeResult(conversion=conversion, output=output)

    if "java" in selected:
        result.builds["java"] = build(
            source,
            Edition.JAVA,
            namespace=resolved_namespace,
            pack_name=title,
            source_name=str(source_name),
            textures=textures,
        )
    if "bedrock" in selected:
        result.builds["bedrock"] = build(
            source,
            Edition.BEDROCK,
            namespace=resolved_namespace,
            pack_name=title,
            source_name=str(source_name),
            textures=textures,
            models=dict(conversion.models),
        )
    if "luanti" in selected:
        result.builds["luanti"] = build(
            source,
            Edition.LUANTI,
            namespace=resolved_namespace,
            pack_name=title,
            source_name=str(source_name),
            textures=textures,
            models=dict(conversion.models),
        )

    # Archives for the game editions.
    game_results = {
        edition: result.builds[name]
        for name, edition in (
            ("java", Edition.JAVA),
            ("bedrock", Edition.BEDROCK),
            ("luanti", Edition.LUANTI),
        )
        if name in result.builds
    }
    if game_results:
        result.written.update(
            write_bundle(
                game_results,
                output,
                stem=resolved_namespace,
                namespace=resolved_namespace,
            )
        )

    luanti = result.builds.get("luanti")
    if "hytale" in selected and luanti is not None:
        hytale = archive_hytale(
            luanti,
            namespace=resolved_namespace,
            pack_name=title,
            blockymodels=dict(conversion.blockymodels),
        )
        path = output / f"{resolved_namespace}-hytale.zip"
        path.write_bytes(hytale)
        result.written["hytale"] = path
    if "godot" in selected and luanti is not None:
        path = write_godot_project(
            luanti,
            output / "godot",
            namespace=resolved_namespace,
            project_name=title,
            graphics=graphics,
            models=dict(conversion.models),
        )
        result.written["godot"] = path
        (output / "godot" / "graphics.json").write_text(
            json.dumps(
                {
                    "requested": graphics,
                    "rendering_method": _rendering_method(graphics),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        # Keep an archive next to the extracted project as well.
        result.written["godot-archive"] = _write_bytes(
            output / f"{resolved_namespace}-godot.zip",
            archive_godot(
                luanti,
                namespace=resolved_namespace,
                project_name=title,
                graphics=graphics,
                models=dict(conversion.models),
            ),
        )

    result.report = _report(result, selected)
    report_path = output / "bridge.json"
    report_path.write_text(
        json.dumps(result.report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    result.written["report"] = report_path
    return result


def _rendering_method(graphics: str) -> str:
    from .godot_export import GraphicsBackend

    return GraphicsBackend.parse(graphics).rendering_method


def _write_bytes(path: Path, body: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def _report(result: BridgeResult, selected: list[str]) -> dict[str, Any]:
    conversion = result.conversion
    payload: dict[str, Any] = {
        "source": conversion.source,
        "namespace": conversion.namespace,
        "pack_name": conversion.pack_name,
        "targets": selected,
        "counts": conversion.counts,
        "converted": {
            "entities": len(conversion.entities),
            "models": len(conversion.models),
            "blockymodels": len(conversion.blockymodels),
            "species_cards": sum(
                1 for item in conversion.items if item.kind == "species_card"
            ),
            "items": sum(1 for item in conversion.items if item.kind == "item"),
            "recipes": len(conversion.recipes),
            "blocks": len(conversion.blocks),
        },
        "notes": list(conversion.notes),
        "archives": {
            name: str(path)
            for name, path in sorted(result.written.items())
        },
        "builds": {},
        "capabilities": {
            "java": "items, blocks, recipes and the species card; no custom entities",
            "bedrock": "items, blocks, recipes and the species card; no custom entities",
            "luanti": "real entities with OBJ models, hitbox, health, taming, drops",
            "hytale": "NPC roles and .blockymodel assets; no Java gameplay code",
            "godot": "native 3D re-host with selectable Vulkan/OpenGL backend",
        },
    }
    for name, build_result in result.builds.items():
        payload["builds"][name] = {
            "ok": build_result.ok,
            "files": len(build_result.files),
            "errors": [diagnostic.message for diagnostic in build_result.errors],
            "warnings": [diagnostic.message for diagnostic in build_result.warnings],
        }
    return payload
