"""Command line interface for LuaCraft.

    luacraft build   main.lua --target bedrock -o mod.mcaddon
    luacraft build   main.lua --target java    -o mod.zip
    luacraft build   main.lua --target luanti  -o mod.zip
    luacraft check   main.lua --target all
    luacraft bridge  pack.mcaddon -o out/
    luacraft godot   main.lua -o out/godot --graphics opengl
    luacraft hytale  main.lua -o out/pack.zip
    luacraft api

The tool never touches the network.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .api import Edition, describe_capabilities
from .diagnostics import Severity
from .pipeline import (
    build,
    compile_all,
    report_json,
    write_bundle,
    write_godot_project,
    write_hytale_pack,
    write_java_resourcepack,
    write_luanti_mod,
    write_mcaddon,
    write_pack,
)

# ANSI colours, disabled when the output is not a terminal.
_COLOURS = {
    "warning": "\033[33m",
    "error": "\033[31m",
    "reset": "\033[0m",
    "bold": "\033[1m",
}


def _use_colour() -> bool:
    return sys.stdout.isatty() and sys.stdout.encoding and "utf" in sys.stdout.encoding.lower()


def _colourise(text: str, key: str) -> str:
    if not _use_colour():
        return text
    return f"{_COLOURS.get(key, '')}{text}{_COLOURS['reset']}"


def _print_diagnostics(result, source_name: str) -> None:
    for diagnostic in result.diagnostics:
        text = diagnostic.format(source_name)
        if diagnostic.severity is Severity.ERROR:
            print(_colourise(text, "error"), file=sys.stderr)
        else:
            print(_colourise(text, "warning"), file=sys.stderr)


def _summarise(result) -> None:
    warnings = len(result.warnings)
    errors = len(result.errors)
    parts = []
    if warnings:
        parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
    if errors:
        parts.append(f"{errors} error{'s' if errors != 1 else ''}")
    if parts:
        print("  " + ", ".join(parts))
    else:
        print("  no warnings")


def _resolve_edition(value: str) -> Edition:
    lowered = value.lower()
    if lowered in ("java", "je", "datapack", "dp"):
        return Edition.JAVA
    if lowered in ("bedrock", "be", "addon", "bp"):
        return Edition.BEDROCK
    if lowered in ("luanti", "minetest", "mt"):
        return Edition.LUANTI
    if lowered == "both":
        raise argparse.ArgumentTypeError(
            "'both' is only valid for the check command"
        )
    raise argparse.ArgumentTypeError(
        f"unknown target {value!r}; expected 'java', 'bedrock' or 'luanti'"
    )


def _resolve_targets(value: str) -> list[Edition]:
    if value.lower() in ("all", "bundle", "every"):
        return [Edition.JAVA, Edition.BEDROCK, Edition.LUANTI]
    return [_resolve_edition(value)]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="luacraft",
        description=(
            "Write Minecraft mods once in Lua and compile them for Java "
            "Edition and Bedrock Edition. Runs entirely offline."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build_cmd = sub.add_parser(
        "build", help="compile a Lua source into a datapack or an add-on"
    )
    build_cmd.add_argument("source", type=Path, help="the Lua source file")
    build_cmd.add_argument(
        "-t", "--target", required=True, type=_resolve_targets,
        help="the edition to compile for, or 'all'",
    )
    build_cmd.add_argument(
        "-o", "--output", type=Path,
        help="where to write the archive (a directory when --target all)",
    )
    build_cmd.add_argument(
        "-n", "--namespace", default="luacraft",
        help="namespace of the generated pack (default: luacraft)",
    )
    build_cmd.add_argument(
        "--name", default="LuaCraft Mod", help="display name of the pack"
    )
    build_cmd.add_argument(
        "--pack-format", type=int, default=48,
        help="Java Edition pack_format (default: 48)",
    )
    build_cmd.add_argument(
        "--engine-version", default="1.21.0",
        help="Bedrock min_engine_version (default: 1.21.0)",
    )
    build_cmd.add_argument(
        "--json", action="store_true",
        help="print the build report as JSON",
    )
    build_cmd.add_argument(
        "--debug", action="store_true",
        help="print a debug trace of each build phase",
    )
    build_cmd.add_argument(
        "--textures", action="store_true",
        help="generate placeholders and pack textures",
    )
    build_cmd.add_argument(
        "--texture-dir", type=Path,
        help="folder containing PNG files to use as textures",
    )
    build_cmd.add_argument(
        "--resourcepack", type=Path,
        help="separate output for the Java resource pack (datapack only)",
    )
    build_cmd.add_argument(
        "--bundle", type=Path,
        help="write all editions to this directory when --target all",
    )
    build_cmd.add_argument(
        "--sandbox", action="store_true",
        help="validate the generated pack before writing it",
    )
    build_cmd.add_argument(
        "--strict", action="store_true",
        help="exit with an error code if there were any warnings",
    )

    check_cmd = sub.add_parser(
        "check",
        help="analyse a Lua source and report what each edition supports",
    )
    check_cmd.add_argument("source", type=Path, help="the Lua source file")
    check_cmd.add_argument(
        "-t", "--target", default="both",
        choices=["java", "bedrock", "luanti", "both", "all"],
        help="which edition to check (default: both)",
    )
    check_cmd.add_argument(
        "--json", action="store_true", help="print the report as JSON"
    )
    check_cmd.add_argument(
        "--debug", action="store_true", help="print a debug trace per edition"
    )
    check_cmd.add_argument(
        "--textures", action="store_true",
        help="inspect the build as if textures were enabled",
    )

    sandbox_cmd = sub.add_parser(
        "sandbox",
        help="validate a generated datapack, add-on or mod",
    )
    sandbox_cmd.add_argument("archive", type=Path, help="the archive to inspect")
    sandbox_cmd.add_argument(
        "--json", action="store_true", help="print the report as JSON"
    )

    gui_cmd = sub.add_parser(
        "gui", help="open the local mod editor in a browser"
    )
    gui_cmd.add_argument("--host", default="127.0.0.1", help="interface to bind")
    gui_cmd.add_argument("--port", type=int, default=8765, help="port to bind")
    gui_cmd.add_argument(
        "--no-browser", action="store_true", help="do not open a browser"
    )
    gui_cmd.add_argument(
        "--debug", action="store_true", help="include traces in every response"
    )

    mcp_cmd = sub.add_parser(
        "mcp", help="serve LuaCraft to an MCP client over stdio"
    )
    mcp_cmd.add_argument(
        "--debug", action="store_true", help="log MCP traffic to stderr"
    )

    api_cmd = sub.add_parser(
        "api", help="show the portable runtime API and its capabilities"
    )
    api_cmd.add_argument(
        "--json", action="store_true", help="print the API as JSON"
    )

    content_cmd = sub.add_parser(
        "content",
        help="list the content a Lua source declares, without writing anything",
    )
    content_cmd.add_argument("source", type=Path, help="the Lua source file")
    content_cmd.add_argument(
        "-n", "--namespace", default="luacraft",
        help="namespace used to qualify identifiers (default: luacraft)",
    )
    content_cmd.add_argument(
        "--json", action="store_true", help="print the content as JSON"
    )

    bridge_cmd = sub.add_parser(
        "bridge",
        help=(
            "convert a Bedrock add-on and export Java, Bedrock, Luanti, "
            "Hytale and Godot at once"
        ),
    )
    bridge_cmd.add_argument(
        "pack", type=Path, help=".mcaddon, .mcpack, .zip or a pack folder"
    )
    bridge_cmd.add_argument(
        "-o", "--output", type=Path, required=True,
        help="directory for the converted project and archives",
    )
    bridge_cmd.add_argument(
        "-n", "--namespace", help="override the namespace inferred from the pack"
    )
    bridge_cmd.add_argument("--name", help="display name of the converted pack")
    bridge_cmd.add_argument(
        "--copy-textures", action="store_true",
        help="copy the source textures instead of deterministic placeholders",
    )
    bridge_cmd.add_argument(
        "--graphics", default="vulkan",
        choices=["vulkan", "vulkan-mobile", "opengl"],
        help="Godot graphics backend (default: vulkan)",
    )
    bridge_cmd.add_argument(
        "--targets", default=",".join(("java", "bedrock", "luanti", "hytale", "godot")),
        help="comma separated subset of java,bedrock,luanti,hytale,godot",
    )
    bridge_cmd.add_argument(
        "--json", action="store_true", help="print the bridge report as JSON"
    )

    godot_cmd = sub.add_parser(
        "godot",
        help="compile a Lua source into a Godot 4 project",
    )
    godot_cmd.add_argument("source", type=Path, help="the Lua source file")
    godot_cmd.add_argument(
        "-o", "--output", type=Path, required=True,
        help="directory for the Godot project",
    )
    godot_cmd.add_argument(
        "-n", "--namespace", default="luacraft",
        help="namespace used to qualify identifiers (default: luacraft)",
    )
    godot_cmd.add_argument("--name", default="LuaCraft", help="project name")
    godot_cmd.add_argument(
        "--graphics", default="vulkan",
        choices=["vulkan", "vulkan-mobile", "opengl"],
        help="Godot graphics backend (default: vulkan)",
    )
    godot_cmd.add_argument(
        "--textures", action="store_true",
        help="generate a placeholder texture per declared item, block or entity",
    )
    godot_cmd.add_argument(
        "--texture-dir", type=Path, help="folder of author supplied PNG files"
    )
    godot_cmd.add_argument(
        "--model-dir", type=Path,
        help="folder of OBJ models, keyed by file stem",
    )

    hytale_cmd = sub.add_parser(
        "hytale",
        help="compile a Lua source into a Hytale asset pack",
    )
    hytale_cmd.add_argument("source", type=Path, help="the Lua source file")
    hytale_cmd.add_argument(
        "-o", "--output", type=Path, required=True,
        help="path of the .zip asset pack to write",
    )
    hytale_cmd.add_argument(
        "-n", "--namespace", default="luacraft",
        help="namespace used to qualify identifiers (default: luacraft)",
    )
    hytale_cmd.add_argument("--name", default="LuaCraft", help="pack name")
    hytale_cmd.add_argument(
        "--textures", action="store_true",
        help="generate a placeholder texture per declared item, block or entity",
    )
    hytale_cmd.add_argument(
        "--texture-dir", type=Path, help="folder of author supplied PNG files"
    )
    hytale_cmd.add_argument(
        "--model-dir", type=Path,
        help="folder of .blockymodel files, keyed by file stem",
    )

    return parser


def _command_build(args) -> int:
    if not args.source.exists():
        print(f"error: {args.source} does not exist", file=sys.stderr)
        return 2

    source = args.source.read_text(encoding="utf-8")
    engine = tuple(int(p) for p in args.engine_version.split("."))
    editions: list[Edition] = args.target
    wants_textures = bool(args.textures or args.texture_dir)

    results = {}
    for edition in editions:
        results[edition] = build(
            source,
            edition,
            namespace=args.namespace,
            pack_name=args.name,
            pack_format=args.pack_format,
            min_engine_version=engine,
            source_name=str(args.source),
            texture_dir=args.texture_dir,
            generate_textures=wants_textures,
            debug=args.debug,
        )

    single = len(results) == 1
    sandbox_reports = {}
    if args.sandbox:
        from .sandbox import validate_build

        for edition, result in results.items():
            sandbox_reports[edition] = validate_build(result)

    if args.json:
        if single:
            payload = json.loads(report_json(next(iter(results.values()))))
        else:
            payload = {
                edition.value: json.loads(report_json(result))
                for edition, result in results.items()
            }
        if args.debug:
            payload["debug"] = {
                edition.value: result.trace.as_dict()
                for edition, result in results.items()
            }
        if sandbox_reports:
            payload["sandbox"] = {
                edition.value: report.as_dict()
                for edition, report in sandbox_reports.items()
            }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for edition, result in results.items():
            if not single:
                print(f"== {edition.label} ==")
            _print_diagnostics(result, str(args.source))
            print(f"target: {edition.label}")
            _summarise(result)
            if args.debug:
                print(result.trace.format(), file=sys.stderr)
            if edition in sandbox_reports:
                report = sandbox_reports[edition]
                print(report.format())
                for line in report.format().splitlines():
                    if line.startswith("  [FAIL]"):
                        print(line, file=sys.stderr)
            if not single:
                print()

    failed = [edition for edition, result in results.items() if not result.ok]
    sandbox_failed = [
        edition for edition, report in sandbox_reports.items() if not report.ok
    ]
    if failed or sandbox_failed:
        if not args.json:
            print("build failed", file=sys.stderr)
        return 1

    if args.bundle:
        written = write_bundle(
            results, args.bundle, namespace=args.namespace
        )
        if not args.json:
            for path in written.values():
                print(f"  wrote {path}")
    elif args.output:
        if single:
            edition = next(iter(results))
            result = results[edition]
            output = args.output
            if edition is Edition.JAVA:
                write_pack(result, output)
                if args.resourcepack:
                    write_java_resourcepack(result, args.resourcepack)
                    if not args.json:
                        print(f"  wrote {args.resourcepack}")
                elif result.resources:
                    resources = output.with_name(
                        f"{output.stem}-resources{output.suffix or '.zip'}"
                    )
                    write_java_resourcepack(result, resources)
                    if not args.json:
                        print(f"  wrote {resources}")
            elif edition is Edition.BEDROCK:
                write_mcaddon(result, output, namespace=args.namespace)
            else:
                write_luanti_mod(result, output, namespace=args.namespace)
            if not args.json:
                print(f"  wrote {output}")
        else:
            written = write_bundle(
                results, args.output, namespace=args.namespace
            )
            if not args.json:
                for path in written.values():
                    print(f"  wrote {path}")

    if args.strict and any(result.warnings for result in results.values()):
        if not args.json:
            print(
                "strict mode: warnings were found, so the build is marked as failed",
                file=sys.stderr,
            )
        return 1

    return 0


def _command_bridge(args) -> int:
    from .bridge import run_bridge

    if not args.pack.exists():
        print(f"error: {args.pack} does not exist", file=sys.stderr)
        return 2
    result = run_bridge(
        args.pack,
        args.output,
        namespace=args.namespace,
        pack_name=args.name,
        copy_textures=args.copy_textures,
        graphics=args.graphics,
        targets=args.targets,
    )
    if args.json:
        print(json.dumps(result.report, indent=2, ensure_ascii=False))
        return 0 if result.ok else 1

    print(f"source    : {result.conversion.source}")
    print(f"namespace : {result.conversion.namespace}")
    for key, value in result.conversion.counts.items():
        print(f"{key:<22}: {value}")
    print("")
    for name, build_result in result.builds.items():
        status = "ok" if build_result.ok else "failed"
        print(
            f"{name:<8}: {status}, {len(build_result.files)} files, "
            f"{len(build_result.warnings)} warnings"
        )
        for diagnostic in build_result.errors[:5]:
            print(f"  error: {diagnostic.message}")
    for name, path in sorted(result.written.items()):
        print(f"{name:<14}: {path}")
    return 0 if result.ok else 1


def _load_models(directory: Path | None) -> dict[str, str]:
    """Read every ``.obj`` in a folder, keyed by file stem."""
    models: dict[str, str] = {}
    if directory is None or not directory.is_dir():
        return models
    for path in sorted(directory.rglob("*.obj")):
        models[path.stem] = path.read_text(encoding="utf-8")
    return models


def _load_blockymodels(directory: Path | None) -> dict[str, dict]:
    """Read every ``.blockymodel`` in a folder, keyed by file stem."""
    models: dict[str, dict] = {}
    if directory is None or not directory.is_dir():
        return models
    for path in sorted(directory.rglob("*.blockymodel")):
        try:
            models[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
    return models


def _export_source(args, edition: Edition):
    """Shared parse/compile step for the Godot and Hytale commands."""
    source = args.source.read_text(encoding="utf-8")
    result = build(
        source,
        edition,
        namespace=args.namespace,
        pack_name=args.name,
        source_name=str(args.source),
        texture_dir=args.texture_dir,
        generate_textures=bool(args.textures or args.texture_dir),
    )
    return result


def _command_godot(args) -> int:
    if not args.source.exists():
        print(f"error: {args.source} does not exist", file=sys.stderr)
        return 2
    result = _export_source(args, Edition.LUANTI)
    models = _load_models(args.model_dir)
    path = write_godot_project(
        result,
        args.output,
        namespace=args.namespace,
        project_name=args.name,
        graphics=args.graphics,
        models=models,
    )
    _print_diagnostics(result, str(args.source))
    from .godot_export import GraphicsBackend

    backend = GraphicsBackend.parse(args.graphics)
    print(f"target: Godot 4 ({backend.rendering_method}, {backend.api})")
    print(f"project: {path}")
    print(f"models : {len(models)}")
    return 0 if result.ok else 1


def _command_hytale(args) -> int:
    if not args.source.exists():
        print(f"error: {args.source} does not exist", file=sys.stderr)
        return 2
    result = _export_source(args, Edition.LUANTI)
    blockymodels = _load_blockymodels(args.model_dir)
    path = write_hytale_pack(
        result,
        args.output,
        namespace=args.namespace,
        pack_name=args.name,
        blockymodels=blockymodels,
    )
    _print_diagnostics(result, str(args.source))
    print("target: Hytale asset pack")
    print(f"pack   : {path}")
    print(f"models : {len(blockymodels)}")
    return 0 if result.ok else 1


def _command_check(args) -> int:
    if not args.source.exists():
        print(f"error: {args.source} does not exist", file=sys.stderr)
        return 2

    source = args.source.read_text(encoding="utf-8")
    if args.target == "all":
        editions = list(Edition)
    elif args.target == "both":
        editions = [Edition.JAVA, Edition.BEDROCK]
    elif args.target == "luanti":
        editions = [Edition.LUANTI]
    else:
        editions = [Edition.JAVA if args.target == "java" else Edition.BEDROCK]

    combined = {}
    worst = 0
    for edition in editions:
        result = build(
            source,
            edition,
            source_name=str(args.source),
            generate_textures=args.textures,
            debug=args.debug,
        )
        combined[edition.value] = {
            "ok": result.ok,
            "warnings": [d.as_dict() for d in result.warnings],
            "errors": [d.as_dict() for d in result.errors],
        }
        if args.debug:
            combined[edition.value]["trace"] = result.trace.as_dict()
        if result.errors:
            worst = 1
        if not args.json:
            print(f"== {edition.label} ==")
            _print_diagnostics(result, str(args.source))
            _summarise(result)
            if args.debug:
                print(result.trace.format(), file=sys.stderr)
            print()

    if args.json:
        import json

        print(json.dumps(combined, indent=2, ensure_ascii=False))
    return worst


def _command_api(args) -> int:
    if args.json:
        import json

        from .api import PRIMITIVES

        payload = [
            {
                "name": p.name,
                "summary": p.summary,
                "signature": p.signature(),
                "capabilities": {
                    edition.value: p.capability(edition).value for edition in Edition
                },
                "notes": {k.value: v for k, v in p.notes.items()},
            }
            for p in PRIMITIVES
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print("LuaCraft portable runtime API")
    print()
    print(describe_capabilities())
    return 0


def _command_content(args) -> int:
    if not args.source.exists():
        print(f"error: {args.source} does not exist", file=sys.stderr)
        return 2

    source = args.source.read_text(encoding="utf-8")
    result = build(source, Edition.JAVA, namespace=args.namespace,
                   source_name=str(args.source))
    content = result.content

    if args.json:
        import json

        payload = {
            "summary": content.summary(),
            "items": {
                name: {
                    "identifier": item.identifier,
                    "max_stack": item.max_stack,
                    "durability": item.durability,
                    "texture": item.texture,
                    "category": item.category,
                }
                for name, item in content.items.items()
            },
            "blocks": {
                name: {
                    "identifier": block.identifier,
                    "hardness": block.hardness,
                    "light_emission": block.light_emission,
                    "texture": block.texture,
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
            "entities": {
                name: {
                    "identifier": entity.identifier,
                    "model": entity.model,
                    "texture": entity.texture,
                    "health": entity.health,
                    "collision_box": {
                        "width": entity.width,
                        "height": entity.height,
                    },
                    "hostile": entity.hostile,
                    "tameable": entity.tameable is not None,
                    "drops": len(entity.drops),
                }
                for name, entity in content.entities.items()
            },
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"content in {args.source}")
    print()
    for label, entries in (
        ("items", content.items),
        ("blocks", content.blocks),
        ("recipes", content.recipes),
        ("entities", content.entities),
    ):
        if not entries:
            continue
        print(f"{label}:")
        for name, entry in entries.items():
            if label == "recipes":
                extra = f" {entry.type} -> {entry.result} x{entry.count}"
            else:
                extra = f" {entry.identifier}"
            print(f"  {name}{extra}")
        print()
    if not any((content.items, content.blocks, content.recipes, content.entities)):
        print("no content declared")
    return 0


def _command_sandbox(args) -> int:
    from .sandbox import validate_directory, validate_zip

    if not args.archive.exists():
        print(f"error: {args.archive} does not exist", file=sys.stderr)
        return 2

    if args.archive.is_dir():
        report = validate_directory(args.archive)
    else:
        try:
            report = validate_zip(args.archive)
        except Exception as exc:
            print(f"error: could not read {args.archive}: {exc}", file=sys.stderr)
            return 2

    if args.json:
        print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    else:
        print(report.format())
    return 0 if report.ok else 1


def _command_gui(args) -> int:
    from .gui import run_gui

    return run_gui(
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
        debug=args.debug,
    )


def _command_mcp(args) -> int:
    from .mcp_server import serve

    return serve(debug=args.debug)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "build":
        return _command_build(args)
    if args.command == "check":
        return _command_check(args)
    if args.command == "api":
        return _command_api(args)
    if args.command == "content":
        return _command_content(args)
    if args.command == "bridge":
        return _command_bridge(args)
    if args.command == "godot":
        return _command_godot(args)
    if args.command == "hytale":
        return _command_hytale(args)
    if args.command == "sandbox":
        return _command_sandbox(args)
    if args.command == "gui":
        return _command_gui(args)
    if args.command == "mcp":
        return _command_mcp(args)
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
