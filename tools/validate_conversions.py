"""Convert local Bedrock packs to LuaCraft and validate every edition.

The runner is deliberately end to end:

1. convert each available pack with :mod:`convert_bedrock`,
2. compile the generated ``mod.lua`` for Java, Bedrock and Luanti,
3. run the offline sandbox on the in-memory build,
4. write one archive per edition plus a Hytale asset pack and a Godot project,
5. run the sandbox again on every archive (and on the Godot directory),
6. write one JSON report with counts, diagnostics, checks and timings.

Unavailable sources (Cobbleverse, Anime Characters) are reported as missing
instead of being invented.  Anything LuaCraft cannot represent is listed with
the reason, so the report states exactly what a conversion preserves.

Usage::

    py tools/validate_conversions.py
    py tools/validate_conversions.py --out out/conversions --copy-textures
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from luacraft.api import Edition  # noqa: E402
from luacraft.godot_export import GodotProject, GraphicsBackend  # noqa: E402
from luacraft.hytale_export import HytalePack  # noqa: E402
from luacraft.pipeline import (  # noqa: E402
    archive_godot,
    archive_hytale,
    compile_all,
    write_bundle,
    write_godot_project,
)
from luacraft.sandbox import (  # noqa: E402
    validate_build,
    validate_godot,
    validate_godot_directory,
    validate_godot_zip,
    validate_hytale,
    validate_hytale_zip,
    validate_zip,
)
from luacraft.textures import TextureSet  # noqa: E402

from convert_bedrock import convert  # noqa: E402

#: The four sources the user asked about, with the ones that exist locally.
SOURCES = [
    {
        "label": "cobblemon",
        "title": "Cobblemon",
        "path": "CobblemonBedrock/Cobblemon1074.mcaddon",
        "namespace": "pmon",
    },
    {
        "label": "fakemon",
        "title": "Fakemon (Laser's Fakemon Pack)",
        "path": "CobblemonBedrock/FakemonOptimizado.mcaddon",
        "namespace": "pmon",
    },
    {
        "label": "cobblemon-sample",
        "title": "Cobblemon sample",
        "path": "CobblemonBedrock/CobblemonBedrock.mcaddon",
        "namespace": "pmon",
    },
    {
        "label": "faunalibre",
        "title": "FaunaLibre",
        "path": "FaunaLibre/FaunaLibre.mcaddon",
        "namespace": "faunalibre",
    },
    {
        "label": "cobbleverse",
        "title": "Cobbleverse",
        "path": "Cobbleverse/Cobbleverse.mcaddon",
        "namespace": "cobbleverse",
        "missing": True,
    },
    {
        "label": "anime-characters",
        "title": "Anime Characters",
        "path": "AnimeCharacters/AnimeCharacters.mcaddon",
        "namespace": "anime",
        "missing": True,
    },
]

#: What every edition can and cannot express, stated once in the report.
EDITION_LIMITS = {
    "java": [
        "A datapack cannot register new item or block types by name.",
        "Custom items are vanilla base items (paper by default) selected with "
        "custom_model_data; the resource pack supplies the texture and model.",
        "Entities cannot be added from a datapack, so the species card stays as "
        "the Java proxy for each converted entity.",
        "Models, animations, render controllers, spawn rules, loot tables and "
        "scripts from the source pack cannot be represented.",
    ],
    "bedrock": [
        "The LuaCraft Bedrock backend emits items, blocks and shaped/shapeless "
        "recipes; it does not author behaviour-pack entity definitions.",
        "Every converted entity keeps the species-card item as the Bedrock proxy.",
        "Geometry, animations, render controllers, spawn rules, loot tables, "
        "scripts and functions are not ported.",
    ],
    "luanti": [
        "Entities become real Luanti entities: OBJ geometry, collision box, "
        "health, movement speed, taming, drops and a small wander/chase AI.",
        "Molang animations, render controllers, Bedrock spawn rules and scripts "
        "are not ported; spawns are approximated with a chat command and ABM.",
        "Items and blocks are craftitems and nodes; recipes are registered nodes "
        "for shaped/shapeless crafting.",
    ],
    "hytale": [
        "Only the JSON asset half of Hytale modding is generated: manifest, "
        "items, recipes, NPC roles, server models, drop tables and .blockymodel "
        "geometry. Java plugin logic is out of scope.",
        "Models are static; Molang and Bedrock animations have no blockymodel "
        "equivalent in this exporter.",
        "Field names follow the public 2026 Hytale asset documentation and must "
        "be checked against the installed game version.",
    ],
    "godot": [
        "This is a native Godot 4 re-host of the converted content, not an "
        "embedded Luanti client.",
        "Luanti Lua gameplay, Molang animations and shaders are not executed; "
        "the generated runtime only spawns entities and cycles through them.",
        "The graphics backend is a project setting: Vulkan writes forward_plus "
        "or mobile, OpenGL writes gl_compatibility. Regenerate the project to "
        "switch backend.",
    ],
}


@dataclass
class PackReport:
    label: str
    title: str
    path: str
    status: str = "pending"
    namespace: str = ""
    conversion: dict[str, Any] = field(default_factory=dict)
    editions: dict[str, Any] = field(default_factory=dict)
    archives: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "title": self.title,
            "path": self.path,
            "status": self.status,
            "namespace": self.namespace,
            "conversion": self.conversion,
            "editions": self.editions,
            "archives": self.archives,
            "timings": self.timings,
            "error": self.error,
        }


def _diagnostics(result) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "files": len(result.files),
        "resources": len(result.resources),
        "errors": [d.as_dict() for d in result.errors],
        "warnings": [d.as_dict() for d in result.warnings],
        "content": result.content.summary(),
        "trace": result.trace.as_dict() if hasattr(result.trace, "as_dict") else {},
    }


def _sandbox(report) -> dict[str, Any]:
    payload = report.as_dict()
    payload["failures"] = [check.as_dict() for check in report.failures]
    payload["warnings"] = [check.as_dict() for check in report.warnings]
    return payload


def _sample(items: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    return items[:limit]


def _exported(files: dict[str, Any], diagnostics, result) -> dict[str, Any]:
    """Diagnostics for an exporter that is not one of the game editions."""
    return {
        "ok": not diagnostics.has_errors,
        "files": len(files),
        "resources": 0,
        "errors": [d.as_dict() for d in diagnostics.errors],
        "warnings": [d.as_dict() for d in diagnostics.warnings],
        "content": result.content.summary(),
        "trace": {},
    }


def validate_source(
    source: dict[str, Any],
    workspace: Path,
    output_root: Path,
    *,
    copy_textures: bool,
    graphics: str = "vulkan",
) -> PackReport:
    pack_path = workspace / source["path"]
    pack = PackReport(
        label=source["label"],
        title=source["title"],
        path=str(pack_path),
    )

    if not pack_path.exists():
        pack.status = "missing"
        pack.error = (
            "source pack is not present in the workspace; no conversion was "
            "attempted and no substitute data was invented"
        )
        return pack

    project_dir = output_root / source["label"]
    started = time.perf_counter()
    try:
        conversion_start = time.perf_counter()
        conversion, written = convert(
            pack_path,
            project_dir,
            namespace=source.get("namespace"),
            pack_name=f"{source['title']} — LuaCraft",
            copy_textures=copy_textures,
        )
        pack.timings["convert"] = round(time.perf_counter() - conversion_start, 3)
        pack.namespace = conversion.namespace
        pack.conversion = conversion.as_dict()
        pack.conversion["project"] = {
            "source": str(written["source"]),
            "textures": str(written["textures"]),
            "models": str(written.get("models", "")),
            "report": str(written["report"]),
        }

        source_text = written["source"].read_text(encoding="utf-8")
        texture_set = TextureSet.from_directory(written["textures"])

        compile_start = time.perf_counter()
        results = compile_all(
            source_text,
            namespace=conversion.namespace,
            pack_name=conversion.pack_name,
            source_name=str(written["source"]),
            texture_dir=written["textures"],
            textures=texture_set,
            models=dict(conversion.models),
            debug=True,
        )
        pack.timings["compile_all"] = round(time.perf_counter() - compile_start, 3)

        sandbox_start = time.perf_counter()
        for edition, result in results.items():
            report = validate_build(result)
            pack.editions[edition.value] = {
                "build": _diagnostics(result),
                "sandbox": _sandbox(report),
                "limits": EDITION_LIMITS[edition.value],
            }
        pack.timings["sandbox_build"] = round(time.perf_counter() - sandbox_start, 3)

        # Hytale and Godot re-use the parsed Luanti content, which already
        # carries every entity, item and recipe plus the texture set.
        luanti = results.get(Edition.LUANTI)
        assets_start = time.perf_counter()
        if luanti is not None:
            hytale_pack = HytalePack(
                namespace=conversion.namespace,
                pack_name=conversion.pack_name,
                blockymodels=dict(conversion.blockymodels),
            )
            hytale_files = hytale_pack.build(luanti.content, luanti.textures)
            pack.editions["hytale"] = {
                "build": _exported(hytale_files, hytale_pack.diagnostics, luanti),
                "sandbox": _sandbox(validate_hytale(hytale_files)),
                "limits": EDITION_LIMITS["hytale"],
            }

            godot_project = GodotProject(
                project_name=conversion.pack_name,
                namespace=conversion.namespace,
                graphics=GraphicsBackend.parse(graphics),
                models=dict(conversion.models),
            )
            godot_files = godot_project.build(luanti.content, luanti.textures)
            pack.editions["godot"] = {
                "build": _exported(godot_files, godot_project.diagnostics, luanti),
                "sandbox": _sandbox(validate_godot(godot_files)),
                "limits": EDITION_LIMITS["godot"],
            }
        pack.timings["export_assets"] = round(
            time.perf_counter() - assets_start, 3
        )

        archive_start = time.perf_counter()
        archives = write_bundle(
            results,
            project_dir / "dist",
            stem=conversion.namespace,
            namespace=conversion.namespace,
        )
        pack.timings["write_bundle"] = round(time.perf_counter() - archive_start, 3)

        for name, archive in archives.items():
            if name == "java-resources":
                # A resource pack is not a datapack; the sandbox has no
                # datapack verdict for it, so it is only measured.
                pack.archives[name] = {
                    "path": str(archive),
                    "bytes": archive.stat().st_size,
                    "sandbox": None,
                    "note": "Java resource pack: textures and models, not a datapack",
                }
                continue
            report = validate_zip(archive)
            pack.archives[name] = {
                "path": str(archive),
                "bytes": archive.stat().st_size,
                "sandbox": _sandbox(report),
            }

        if luanti is not None:
            dist = project_dir / "dist"
            title = conversion.pack_name
            namespace = conversion.namespace

            hytale_path = dist / f"{namespace}-hytale.zip"
            hytale_path.write_bytes(
                archive_hytale(
                    luanti,
                    namespace=namespace,
                    pack_name=title,
                    blockymodels=dict(conversion.blockymodels),
                )
            )
            pack.archives["hytale"] = {
                "path": str(hytale_path),
                "bytes": hytale_path.stat().st_size,
                "sandbox": _sandbox(validate_hytale_zip(hytale_path)),
            }

            godot_dir = project_dir / "godot"
            write_godot_project(
                luanti,
                godot_dir,
                namespace=namespace,
                project_name=title,
                graphics=graphics,
                models=dict(conversion.models),
            )
            pack.archives["godot-directory"] = {
                "path": str(godot_dir),
                "bytes": sum(
                    path.stat().st_size
                    for path in godot_dir.rglob("*")
                    if path.is_file()
                ),
                "sandbox": _sandbox(validate_godot_directory(godot_dir)),
            }

            godot_path = dist / f"{namespace}-godot.zip"
            godot_path.write_bytes(
                archive_godot(
                    luanti,
                    namespace=namespace,
                    project_name=title,
                    graphics=graphics,
                    models=dict(conversion.models),
                )
            )
            pack.archives["godot"] = {
                "path": str(godot_path),
                "bytes": godot_path.stat().st_size,
                "sandbox": _sandbox(validate_godot_zip(godot_path)),
            }

        pack.status = "ok"
    except Exception as exc:  # pragma: no cover - exercised by real packs
        pack.status = "failed"
        pack.error = f"{type(exc).__name__}: {exc}"
        pack.timings["total"] = round(time.perf_counter() - started, 3)
        pack.conversion.setdefault("traceback", traceback.format_exc())
        return pack

    pack.timings["total"] = round(time.perf_counter() - started, 3)
    return pack


def build_report(
    packs: list[PackReport], *, started: float, graphics: str = "vulkan"
) -> dict[str, Any]:
    def summarize(pack: PackReport) -> dict[str, Any]:
        archive_keys = {
            "java": ["java"],
            "bedrock": ["bedrock"],
            "luanti": ["luanti"],
            "hytale": ["hytale"],
            "godot": ["godot", "godot-directory"],
        }
        editions = {}
        for edition, data in pack.editions.items():
            build = data["build"]
            sandbox = data["sandbox"]
            editions[edition] = {
                "ok": build["ok"],
                "files": build["files"],
                "resources": build["resources"],
                "errors": len(build["errors"]),
                "warnings": len(build["warnings"]),
                "warning_sample": _sample(build["warnings"]),
                "sandbox_ok": sandbox["ok"],
                "sandbox_failures": len(sandbox["failures"]),
                "sandbox_warning_count": len(sandbox["warnings"]),
                "sandbox_failure_sample": _sample(sandbox["failures"]),
                "archives": {
                    name: {
                        "ok": pack.archives[name]["sandbox"]["ok"],
                        "bytes": pack.archives[name]["bytes"],
                        "failures": len(
                            pack.archives[name]["sandbox"]["failures"]
                        ),
                    }
                    for name in archive_keys.get(edition, [])
                    if name in pack.archives
                },
                # The Java resource pack is not a datapack, so it has no
                # archive sandbox verdict of its own.
                "resources_archive": {
                    name: {
                        "bytes": pack.archives[name]["bytes"],
                    }
                    for name in ("java-resources",)
                    if name in pack.archives
                },
                "limits": data["limits"],
            }
        conversion = pack.conversion
        return {
            "status": pack.status,
            "namespace": pack.namespace,
            "counts": conversion.get("counts", {}),
            "unsupported": conversion.get("unsupported", {}),
            "textures": conversion.get("textures", {}),
            "lang_source": conversion.get("lang_source", ""),
            "editions": editions,
            "timings": pack.timings,
            "error": pack.error,
        }

    summary = {pack.label: summarize(pack) for pack in packs}
    available = [p for p in packs if p.status == "ok"]
    missing = [p for p in packs if p.status == "missing"]
    failed = [p for p in packs if p.status == "failed"]

    return {
        "generated_by": "LuaCraft tools/validate_conversions.py",
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "sources": [pack.as_dict() for pack in packs],
        "summary": summary,
        "overall": {
            "converted_and_validated": [p.label for p in available],
            "missing_sources": [p.label for p in missing],
            "failed": [p.label for p in failed],
            "all_sandboxes_ok": all(
                edition["sandbox_ok"]
                for pack in available
                for edition in summary[pack.label]["editions"].values()
            )
            if available
            else False,
            "all_archives_ok": all(
                entry["sandbox"]["ok"]
                for pack in available
                for entry in pack.archives.values()
                if entry.get("sandbox")
            )
            if available
            else False,
            "godot_graphics": graphics,
            "entity_policy": (
                "Entities are parsed as real entities and carried per target. "
                "Luanti registers them with OBJ models, collision boxes, health, "
                "movement, taming, drops and a small wander/chase AI. Hytale gets "
                "NPC roles, server models, drop tables and .blockymodel geometry. "
                "Godot re-hosts them as native 3D nodes with OBJ meshes. Bedrock "
                "and Java cannot add entities from a pack, so each converted "
                "entity keeps a species-card item as its proxy."
            ),
            "texture_policy": (
                "Third-party textures are not copied unless --copy-textures is "
                "used. By default LuaCraft writes deterministic placeholders."
            ),
            "unavailable_capabilities": EDITION_LIMITS,
        },
    }


def print_report(report: dict[str, Any]) -> None:
    print("")
    print("LuaCraft interconversion validation")
    print("=" * 62)
    for label, data in report["summary"].items():
        counts = data["counts"]
        print("")
        print(f"{label}: {data['status']}")
        if data["status"] == "missing":
            print(f"  {data['error']}")
            continue
        if data["status"] == "failed":
            print(f"  {data['error']}")
            continue
        print(
            "  entities={entities} models={entities_with_model} "
            "blockymodels={blockymodels} cards={species_cards} items={items} "
            "blocks={blocks} recipes={recipes}".format(**counts)
        )
        for edition, edition_data in data["editions"].items():
            print(
                "  {edition:<7} ok={ok} files={files} resources={resources} "
                "errors={errors} warnings={warnings} sandbox={sandbox_ok} "
                "archive={archive_ok}".format(
                    edition=edition,
                    ok=edition_data["ok"],
                    files=edition_data["files"],
                    resources=edition_data["resources"],
                    errors=edition_data["errors"],
                    warnings=edition_data["warnings"],
                    sandbox_ok=edition_data["sandbox_ok"],
                    archive_ok=all(
                        entry["ok"] for entry in edition_data["archives"].values()
                    ),
                )
            )
        if data["timings"]:
            timings = ", ".join(
                f"{key}={value}s" for key, value in data["timings"].items()
            )
            print(f"  timings: {timings}")

    overall = report["overall"]
    print("")
    print(f"converted and validated : {', '.join(overall['converted_and_validated']) or 'none'}")
    print(f"missing sources         : {', '.join(overall['missing_sources']) or 'none'}")
    print(f"failed                  : {', '.join(overall['failed']) or 'none'}")
    print(f"all sandboxes ok        : {overall['all_sandboxes_ok']}")
    print(f"all archives ok         : {overall['all_archives_ok']}")
    print(f"godot graphics          : {overall['godot_graphics']}")
    print(f"elapsed                 : {report['elapsed_seconds']}s")
    print("")
    print("Luanti, Hytale and Godot receive real entities; Bedrock and Java keep")
    print("the species card as their proxy because a pack cannot add entities.")
    print("Third-party art is not copied by default; placeholders are generated.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=ROOT.parent,
        help="folder that holds the source packs (default: parent of LuaCraft)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "out" / "conversions",
        help="output folder for converted projects and archives",
    )
    parser.add_argument(
        "--copy-textures",
        action="store_true",
        help="copy source textures instead of generating placeholders",
    )
    parser.add_argument(
        "--only",
        action="append",
        help="convert only these labels (repeatable)",
    )
    parser.add_argument(
        "--graphics",
        default="vulkan",
        choices=["vulkan", "vulkan-mobile", "opengl"],
        help="Godot graphics backend to validate (default: vulkan)",
    )
    args = parser.parse_args(argv)

    started = time.perf_counter()
    sources = SOURCES
    if args.only:
        wanted = set(args.only)
        sources = [source for source in SOURCES if source["label"] in wanted]

    reports: list[PackReport] = []
    for source in sources:
        print(f"==> {source['title']} ({source['path']})")
        report = validate_source(
            source,
            args.workspace,
            args.out,
            copy_textures=args.copy_textures,
            graphics=args.graphics,
        )
        reports.append(report)
        print(f"    status: {report.status}")
        if report.error:
            print(f"    {report.error}")

    payload = build_report(reports, started=started, graphics=args.graphics)
    args.out.mkdir(parents=True, exist_ok=True)
    output = args.out / "validation.json"
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print_report(payload)
    print(f"report                  : {output}")
    return 0 if payload["overall"]["failed"] == [] else 1


if __name__ == "__main__":
    raise SystemExit(main())
