"""Command line interface for LuaCraft.

    luacraft build   main.lua --target bedrock -o mod.mcaddon
    luacraft build   main.lua --target java    -o mod.zip
    luacraft check   main.lua --target both
    luacraft api

The tool never touches the network.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .api import Edition, describe_capabilities
from .diagnostics import Severity
from .pipeline import build, report_json, write_mcaddon, write_pack

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
    if lowered == "both":
        raise argparse.ArgumentTypeError(
            "'both' is only valid for the check command"
        )
    raise argparse.ArgumentTypeError(
        f"unknown target {value!r}; expected 'java' or 'bedrock'"
    )


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
        "-t", "--target", required=True, type=_resolve_edition,
        help="the edition to compile for",
    )
    build_cmd.add_argument(
        "-o", "--output", type=Path, help="where to write the archive"
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
        "--strict", action="store_true",
        help="exit with an error code if there were any warnings",
    )

    check_cmd = sub.add_parser(
        "check",
        help="analyse a Lua source and report what each edition supports",
    )
    check_cmd.add_argument("source", type=Path, help="the Lua source file")
    check_cmd.add_argument(
        "-t", "--target", default="both", choices=["java", "bedrock", "both"],
        help="which edition to check (default: both)",
    )
    check_cmd.add_argument(
        "--json", action="store_true", help="print the report as JSON"
    )

    api_cmd = sub.add_parser(
        "api", help="show the portable runtime API and its capabilities"
    )
    api_cmd.add_argument(
        "--json", action="store_true", help="print the API as JSON"
    )

    return parser


def _command_build(args) -> int:
    if not args.source.exists():
        print(f"error: {args.source} does not exist", file=sys.stderr)
        return 2

    source = args.source.read_text(encoding="utf-8")
    engine = tuple(int(p) for p in args.engine_version.split("."))

    result = build(
        source,
        args.target,
        namespace=args.namespace,
        pack_name=args.name,
        pack_format=args.pack_format,
        min_engine_version=engine,
        source_name=str(args.source),
    )

    if args.json:
        print(report_json(result))
    else:
        _print_diagnostics(result, str(args.source))
        print()
        print(f"target: {args.target.label}")
        _summarise(result)

    if not result.ok:
        if not args.json:
            print("build failed", file=sys.stderr)
        return 1

    if args.output:
        if args.target is Edition.JAVA:
            write_pack(result, args.output)
        else:
            write_mcaddon(result, args.output, namespace=args.namespace)
        if not args.json:
            print(f"  wrote {args.output}")

    if args.strict and result.warnings:
        if not args.json:
            print(
                "strict mode: warnings were found, so the build is marked as failed",
                file=sys.stderr,
            )
        return 1

    return 0


def _command_check(args) -> int:
    if not args.source.exists():
        print(f"error: {args.source} does not exist", file=sys.stderr)
        return 2

    source = args.source.read_text(encoding="utf-8")
    editions = (
        [Edition.JAVA, Edition.BEDROCK]
        if args.target == "both"
        else [Edition.JAVA if args.target == "java" else Edition.BEDROCK]
    )

    combined = {}
    worst = 0
    for edition in editions:
        result = build(source, edition, source_name=str(args.source))
        combined[edition.value] = {
            "ok": result.ok,
            "warnings": [d.as_dict() for d in result.warnings],
            "errors": [d.as_dict() for d in result.errors],
        }
        if result.errors:
            worst = 1
        if not args.json:
            print(f"== {edition.label} ==")
            _print_diagnostics(result, str(args.source))
            _summarise(result)
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


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "build":
        return _command_build(args)
    if args.command == "check":
        return _command_check(args)
    if args.command == "api":
        return _command_api(args)
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
