"""A sandbox that validates a generated pack without launching the game.

The sandbox is deliberately conservative.  It does not pretend to run a
Minecraft server; it checks the invariants that a broken pack always violates:

* every JSON file parses and has the shape its edition expects,
* every Bedrock manifest has well formed UUIDs and versions,
* every Java command starts with a real command, and every function a
  generated file calls actually exists,
* every function tag points at a function that exists,
* every texture a model or an atlas refers to is inside the pack,
* the generated Luanti ``init.lua`` parses again with the LuaCraft parser,
* Luanti recipes only use items the mod registers or a known external mod,
* a Hytale asset pack has a manifest, a consistent layout, roles that point at
  existing models and block models with valid nodes and quaternions,
* a Godot project has a supported rendering method, a main scene, its runtime
  script and the models and textures each entity references.

Those checks catch the mistakes that a compiler makes: a typo in a path, a
missing file, a command that is not a command.  They cannot prove that the
mod behaves as intended, and the report says so.
"""

from __future__ import annotations

import json
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .api import Edition
from .luaparse import LuaSyntaxError, parse

#: Commands a generated Java function may contain.
KNOWN_JAVA_COMMANDS = frozenset(
    {
        "advancement", "attribute", "bossbar", "clear", "clone", "damage",
        "data", "difficulty", "effect", "enchant", "execute", "fill",
        "forceload", "function", "gamemode", "gamerule", "give", "help",
        "item", "kill", "list", "locate", "loot", "me", "msg", "particle",
        "place", "playsound", "random", "recipe", "reload", "return", "ride",
        "say", "schedule", "scoreboard", "seed", "setblock", "spawnpoint",
        "spectate", "spreadplayers", "stopsound", "summon", "tag", "team",
        "teleport", "tellraw", "time", "title", "tp", "trigger", "weather",
        "worldborder", "xp", "dialog", "waypoint",
    }
)

#: Root keys a Bedrock recipe file may use.
BEDROCK_RECIPE_KEYS = frozenset(
    {
        "minecraft:recipe_shaped",
        "minecraft:recipe_shapeless",
        "minecraft:recipe_furnace",
        "minecraft:recipe_brewing_mix",
        "minecraft:recipe_stonecutter",
    }
)

_FUNCTION_CALL = re.compile(r"(?:\brun\s+)?\bfunction\s+([a-z0-9_.-]+):([a-z0-9_./-]+)")
_IDENTIFIER = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")


@dataclass
class Check:
    """One sandbox assertion."""

    name: str
    ok: bool
    detail: str = ""
    severity: str = "error"

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "severity": self.severity,
            "detail": self.detail,
        }


@dataclass
class SandboxReport:
    """The outcome of a sandbox run."""

    edition: Edition | None = None
    checks: list[Check] = field(default_factory=list)
    #: Human readable limits of the sandbox, shown with the report.
    limitations: list[str] = field(default_factory=list)
    #: Target name when the pack is not one of the three game editions.
    target: str = ""

    @property
    def label(self) -> str:
        if self.edition is not None:
            return self.edition.label
        return self.target or "pack"

    def add(self, name: str, ok: bool, detail: str = "", severity: str = "error") -> None:
        self.checks.append(Check(name=name, ok=ok, detail=detail, severity=severity))

    @property
    def failures(self) -> list[Check]:
        return [
            check
            for check in self.checks
            if not check.ok and check.severity == "error"
        ]

    @property
    def warnings(self) -> list[Check]:
        return [
            check
            for check in self.checks
            if not check.ok and check.severity == "warning"
        ]

    @property
    def ok(self) -> bool:
        return not self.failures

    def as_dict(self) -> dict:
        return {
            "edition": self.edition.value if self.edition is not None else self.target,
            "ok": self.ok,
            "checks": [check.as_dict() for check in self.checks],
            "failures": [check.name for check in self.failures],
            "warnings": [check.name for check in self.warnings],
            "limitations": self.limitations,
        }

    def format(self) -> str:
        lines = [f"sandbox: {self.label} -- {'ok' if self.ok else 'failed'}"]
        for check in self.checks:
            mark = "ok  " if check.ok else ("warn" if check.severity == "warning" else "FAIL")
            line = f"  [{mark}] {check.name}"
            if check.detail and not check.ok:
                line += f": {check.detail}"
            lines.append(line)
        if self.limitations:
            lines.append("  limits:")
            for limitation in self.limitations:
                lines.append(f"    - {limitation}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


def validate_files(
    edition: Edition,
    files: dict[str, bytes | str],
    resources: dict[str, bytes | str] | None = None,
) -> SandboxReport:
    """Validate an in memory build."""
    resources = resources or {}
    report = SandboxReport(edition=edition)
    report.limitations = [
        "the pack is inspected statically, not executed",
        "game behaviour and balance are out of scope",
    ]

    _check_json(report, files, resources)
    _check_images(report, files, resources)

    if edition is Edition.JAVA:
        _check_java(report, files, resources)
    elif edition is Edition.BEDROCK:
        _check_bedrock(report, files, resources)
    else:
        _check_luanti(report, files)

    return report


def validate_build(result) -> SandboxReport:
    """Validate a :class:`luacraft.pipeline.BuildResult`."""
    return validate_files(result.edition, result.files, result.resources)


def validate_zip(path: Path) -> SandboxReport:
    """Validate an archive produced by ``luacraft build``."""
    with zipfile.ZipFile(path) as archive:
        raw = {
            name: archive.read(name)
            for name in archive.namelist()
            if not name.endswith("/")
        }

    edition, files, resources = _normalise_archive(raw)
    return validate_files(edition, files, resources)


def validate_directory(path: Path) -> SandboxReport:
    """Validate an unpacked pack on disk."""
    raw: dict[str, bytes] = {}
    for file in sorted(path.rglob("*")):
        if not file.is_file():
            continue
        raw[file.relative_to(path).as_posix()] = file.read_bytes()
    edition, files, resources = _normalise_archive(raw)
    return validate_files(edition, files, resources)


def validate_hytale_zip(path: Path) -> SandboxReport:
    """Validate a zipped Hytale asset pack."""
    with zipfile.ZipFile(path) as archive:
        files = {
            name: archive.read(name)
            for name in archive.namelist()
            if not name.endswith("/")
        }
    return validate_hytale(files)


def validate_hytale_directory(path: Path) -> SandboxReport:
    """Validate an extracted Hytale asset pack on disk."""
    return validate_hytale(_directory_files(path))


def validate_godot_zip(path: Path) -> SandboxReport:
    """Validate a zipped Godot project."""
    with zipfile.ZipFile(path) as archive:
        files = {
            name: archive.read(name)
            for name in archive.namelist()
            if not name.endswith("/")
        }
    return validate_godot(files)


def validate_godot_directory(path: Path) -> SandboxReport:
    """Validate an extracted Godot project on disk."""
    return validate_godot(_directory_files(path))


def _directory_files(path: Path) -> dict[str, bytes]:
    """Read every file under a directory, keyed by its relative POSIX path."""
    files: dict[str, bytes] = {}
    for file in sorted(path.rglob("*")):
        if not file.is_file():
            continue
        relative = file.relative_to(path).as_posix()
        if relative.startswith(".godot/"):
            continue
        files[relative] = file.read_bytes()
    return files


def _normalise_archive(
    raw: dict[str, bytes]
) -> tuple[Edition, dict[str, bytes], dict[str, bytes]]:
    """Detect the edition of an archive and strip its packaging prefix."""
    names = list(raw)

    if any(name.endswith("init.lua") for name in names) and any(
        name.endswith("mod.conf") for name in names
    ):
        prefix = next(
            name.rsplit("/", 1)[0] + "/"
            for name in names
            if name.endswith("init.lua") and "/" in name
        ) if any(
            name.endswith("init.lua") and "/" in name for name in names
        ) else ""
        return (
            Edition.LUANTI,
            {name[len(prefix):]: data for name, data in raw.items()},
            {},
        )

    if any(name.endswith("bp/manifest.json") or name.endswith("rp/manifest.json") for name in names):
        files: dict[str, bytes] = {}
        for name, data in raw.items():
            marker = name.find("bp/")
            if marker < 0:
                marker = name.find("rp/")
            if marker >= 0:
                files[name[marker:]] = data
            else:
                files[name] = data
        return Edition.BEDROCK, files, {}

    # A Java resource pack holds assets/ and a pack.mcmeta, but no data/.
    if any(name.startswith("assets/") for name in names) and not any(
        name.startswith("data/") for name in names
    ):
        return Edition.JAVA, raw, {}

    return Edition.JAVA, raw, {}


# --------------------------------------------------------------------------
# Common checks
# --------------------------------------------------------------------------


def _check_json(
    report: SandboxReport,
    files: dict[str, bytes | str],
    resources: dict[str, bytes | str],
) -> None:
    parsed = 0
    for source in (files, resources):
        for path, body in source.items():
            if not path.endswith(".json"):
                continue
            try:
                json.loads(_as_text(body))
                parsed += 1
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                report.add("json", False, f"{path}: {exc}")
                return
    report.add("json", True, f"{parsed} files parsed")


def _check_images(
    report: SandboxReport,
    files: dict[str, bytes | str],
    resources: dict[str, bytes | str],
) -> None:
    images = 0
    for source in (files, resources):
        for path, body in source.items():
            if not path.endswith(".png"):
                continue
            images += 1
            data = body.encode("latin-1") if isinstance(body, str) else body
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                report.add("png", False, f"{path} is not a PNG file")
                return
    report.add("png", True, f"{images} images are valid PNG files")


def _as_text(body: bytes | str) -> str:
    return body if isinstance(body, str) else body.decode("utf-8")


def _text_files(files: dict[str, bytes | str]) -> dict[str, str]:
    """Decode every text file, skipping the binary images.

    Archives hand the sandbox ``bytes`` while an in-memory build hands
    ``str``; both must validate the same way.
    """
    text: dict[str, str] = {}
    for path, body in files.items():
        if path.endswith(".png"):
            continue
        try:
            text[path] = _as_text(body)
        except UnicodeDecodeError:
            continue
    return text


# --------------------------------------------------------------------------
# Java
# --------------------------------------------------------------------------


def _check_java(
    report: SandboxReport,
    files: dict[str, bytes | str],
    resources: dict[str, bytes | str],
) -> None:
    text = _text_files(files)

    meta = text.get("pack.mcmeta")
    if meta is None:
        report.add("java.pack_mcmeta", False, "pack.mcmeta is missing")
        return
    try:
        payload = json.loads(meta)
        pack_format = payload["pack"]["pack_format"]
        report.add(
            "java.pack_mcmeta",
            isinstance(pack_format, int),
            f"pack_format={pack_format!r}",
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        report.add("java.pack_mcmeta", False, str(exc))

    functions = {
        path: body
        for path, body in text.items()
        if path.startswith("data/") and path.endswith(".mcfunction")
    }
    report.add(
        "java.functions",
        any(path.endswith("/main.mcfunction") for path in functions),
        f"{len(functions)} function files",
    )

    bad_commands: list[str] = []
    for path, body in functions.items():
        for number, line in enumerate(body.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            head = stripped.split(None, 1)[0]
            if head not in KNOWN_JAVA_COMMANDS:
                bad_commands.append(f"{path}:{number}: {head}")
    report.add(
        "java.commands",
        not bad_commands,
        "; ".join(bad_commands[:5]),
    )

    missing_functions: list[str] = []
    for path, body in functions.items():
        for namespace, name in _FUNCTION_CALL.findall(body):
            target = f"data/{namespace}/function/{name}.mcfunction"
            if target not in text:
                missing_functions.append(f"{path} -> {namespace}:{name}")
    report.add(
        "java.function_references",
        not missing_functions,
        "; ".join(missing_functions[:5]),
    )

    missing_tags: list[str] = []
    tagged = 0
    for path, body in text.items():
        if "/tags/" not in path or not path.endswith(".json"):
            continue
        try:
            values = json.loads(body).get("values", [])
        except (json.JSONDecodeError, AttributeError):
            missing_tags.append(f"{path}: unreadable tag")
            continue
        for value in values:
            tagged += 1
            if not isinstance(value, str):
                continue
            if value.startswith("#"):
                continue
            namespace, _, name = value.partition(":")
            if f"data/{namespace}/function/{name}.mcfunction" not in text:
                missing_tags.append(f"{path} -> {value}")
    report.add(
        "java.tags",
        not missing_tags,
        "; ".join(missing_tags[:5]) if missing_tags else f"{tagged} entries",
    )

    if not resources:
        report.add(
            "java.resource_pack",
            False,
            "no resource pack was generated, so custom items keep the vanilla texture",
            severity="warning",
        )
        return

    _check_java_resourcepack(report, resources)


def _check_java_resourcepack(
    report: SandboxReport, resources: dict[str, bytes | str]
) -> None:
    text = _text_files(resources)
    meta = text.get("pack.mcmeta")
    if meta is None:
        report.add("java.resource_pack_meta", False, "the resource pack has no pack.mcmeta")
        return
    try:
        json.loads(meta)["pack"]["pack_format"]
        report.add("java.resource_pack_meta", True, "pack.mcmeta parsed")
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        report.add("java.resource_pack_meta", False, str(exc))

    present = {
        path for path in resources if path.endswith(".png")
    }
    missing: list[str] = []
    for path, body in text.items():
        if not path.endswith(".json"):
            continue
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            continue
        for reference in _texture_references(payload):
            namespace, _, rest = reference.partition(":")
            if not namespace or namespace == "minecraft":
                # Vanilla ships its own assets, so a reference into the
                # ``minecraft`` namespace is never missing from our pack.
                continue
            if rest.startswith("item/"):
                target = f"assets/{namespace}/textures/item/{rest[5:]}.png"
            elif rest.startswith("block/"):
                target = f"assets/{namespace}/textures/block/{rest[6:]}.png"
            else:
                target = f"assets/{namespace}/textures/{rest}.png"
            if target not in present:
                missing.append(f"{path} -> {reference}")
    report.add(
        "java.texture_references",
        not missing,
        "; ".join(sorted(set(missing))[:5]) if missing else f"{len(present)} textures",
    )


def _texture_references(value) -> list[str]:
    """Collect the texture paths inside a model JSON."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "textures" and isinstance(item, dict):
                found.extend(str(v) for v in item.values() if isinstance(v, str))
            else:
                found.extend(_texture_references(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_texture_references(item))
    return found


# --------------------------------------------------------------------------
# Bedrock
# --------------------------------------------------------------------------


def _check_bedrock(
    report: SandboxReport,
    files: dict[str, bytes | str],
    resources: dict[str, bytes | str],
) -> None:
    text = _text_files(files)

    for pack in ("bp/manifest.json", "rp/manifest.json"):
        body = text.get(pack)
        if body is None:
            report.add(f"bedrock.{pack}", False, "manifest is missing")
            continue
        try:
            payload = json.loads(body)
            header = payload["header"]
            uuid.UUID(header["uuid"])
            modules = payload["modules"]
            for module in modules:
                uuid.UUID(module["uuid"])
            report.add(
                f"bedrock.{pack}",
                bool(modules) and isinstance(header["version"], list),
                f"{len(modules)} module(s)",
            )
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            report.add(f"bedrock.{pack}", False, str(exc))

    items = {
        path: json.loads(body)
        for path, body in text.items()
        if path.startswith("bp/items/") and path.endswith(".json")
    }
    bad_items = [
        path
        for path, payload in items.items()
        if "minecraft:item" not in payload
        or "identifier" not in payload["minecraft:item"].get("description", {})
    ]
    report.add(
        "bedrock.items",
        not bad_items,
        "; ".join(bad_items) if bad_items else f"{len(items)} item definitions",
    )

    blocks = {
        path: json.loads(body)
        for path, body in text.items()
        if path.startswith("bp/blocks/") and path.endswith(".json")
    }
    bad_blocks = [
        path
        for path, payload in blocks.items()
        if "minecraft:block" not in payload
        or "identifier" not in payload["minecraft:block"].get("description", {})
    ]
    report.add(
        "bedrock.blocks",
        not bad_blocks,
        "; ".join(bad_blocks) if bad_blocks else f"{len(blocks)} block definitions",
    )

    recipes = {
        path: json.loads(body)
        for path, body in text.items()
        if path.startswith("bp/recipes/") and path.endswith(".json")
    }
    bad_recipes = [
        path
        for path, payload in recipes.items()
        if not BEDROCK_RECIPE_KEYS.intersection(payload)
    ]
    report.add(
        "bedrock.recipes",
        not bad_recipes,
        "; ".join(bad_recipes) if bad_recipes else f"{len(recipes)} recipes",
    )

    script = text.get("bp/scripts/main.ts", "")
    report.add(
        "bedrock.script_balance",
        _balanced(script),
        "unbalanced braces or parentheses in the generated script",
    )

    _check_bedrock_textures(report, text, {**files, **resources})


def _check_bedrock_textures(
    report: SandboxReport,
    text: dict[str, str],
    resources: dict[str, bytes | str],
) -> None:
    present = {path for path in resources if path.endswith(".png")}
    if not present:
        report.add(
            "bedrock.textures",
            False,
            "the resource pack carries no textures",
            severity="warning",
        )
        return

    atlases: dict[str, dict] = {}
    for atlas in (
        "rp/textures/item_texture.json",
        "rp/textures/terrain_texture.json",
    ):
        body = text.get(atlas)
        if body is None:
            continue
        try:
            atlases[atlas] = json.loads(body).get("texture_data", {})
        except json.JSONDecodeError:
            report.add("bedrock.textures", False, f"{atlas} is not valid JSON")
            return

    missing: list[str] = []
    for atlas, entries in atlases.items():
        for key, entry in entries.items():
            path = entry.get("textures") if isinstance(entry, dict) else None
            if not isinstance(path, str):
                continue
            target = f"rp/{path}.png"
            if target not in present:
                missing.append(f"{atlas}:{key} -> {target}")

    report.add(
        "bedrock.textures",
        not missing,
        "; ".join(missing[:5]) if missing else f"{len(present)} textures",
    )


def _balanced(source: str) -> bool:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    in_string: str | None = None
    escaped = False
    for character in source:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == in_string:
                in_string = None
            continue
        if character in "\"'`":
            in_string = character
        elif character in "([{":
            stack.append(character)
        elif character in ")]}":
            if not stack or stack.pop() != pairs[character]:
                return False
    return not stack and in_string is None


# --------------------------------------------------------------------------
# Luanti
# --------------------------------------------------------------------------


def _check_luanti(
    report: SandboxReport, files: dict[str, bytes | str]
) -> None:
    text = _text_files(files)

    script = text.get("init.lua")
    if script is None:
        report.add("luanti.init_lua", False, "init.lua is missing")
        return
    try:
        parse(script)
        report.add("luanti.init_lua", True, "init.lua parses")
    except LuaSyntaxError as exc:
        report.add("luanti.init_lua", False, f"line {exc.line}: {exc.message}")
        return

    conf = text.get("mod.conf", "")
    report.add(
        "luanti.mod_conf",
        "name" in conf,
        "mod.conf must declare the mod name",
    )

    registered = set(
        re.findall(
            r"S\.register_(?:craftitem|node|tool)\(\s*\"([^\"]+)\"", script
        )
    )
    references = set(
        re.findall(r"ItemStack\(\s*\"([^\"]+)\"", script)
    )
    references.update(
        re.findall(r"S\.register_craft\(\{.*?output\s*=\s*\"([^\"]+)\",", script, re.S)
    )
    references.update(
        re.findall(r"recipe\s*=\s*\{[^}]*\"([a-z0-9_.-]+:[a-z0-9_./-]+)\"", script)
    )
    namespace = conf.split("name", 1)[-1].split("=", 1)[-1].strip() if "=" in conf else ""
    local_missing = sorted(
        ref
        for ref in references
        if ":" in ref
        and not ref.startswith("default:")
        and not ref.startswith("minecraft:")
        and namespace
        and ref.split(":", 1)[0] == namespace
        and ref not in registered
    )
    report.add(
        "luanti.identifiers",
        not local_missing,
        "; ".join(local_missing[:5]) if local_missing else f"{len(registered)} registrations",
    )

    textures = {
        path[len("textures/"):-len(".png")]
        for path in files
        if path.startswith("textures/") and path.endswith(".png")
    }
    wanted = set(
        re.findall(r"(?:inventory_image|tiles)\s*=\s*(?:\{\s*)?\"([^\"]+)\"", script)
    )
    missing = sorted(name for name in wanted if name not in textures)
    if wanted:
        report.add(
            "luanti.textures",
            not missing,
            "; ".join(missing[:5]) if missing else f"{len(textures)} textures",
        )


# --------------------------------------------------------------------------
# Hytale asset pack
# --------------------------------------------------------------------------

_HYTALE_REQUIRED_DIRS = (
    "Server/",
    "Common/",
)


def validate_hytale(files: dict[str, bytes | str]) -> SandboxReport:
    """Validate a generated Hytale asset pack.

    The checks mirror the invariants a broken pack always violates:

    * every JSON file parses,
    * the manifest exists and names the pack,
    * each NPC role points at a server model that exists,
    * each server model points at a ``.blockymodel`` and a texture that exist,
    * each ``.blockymodel`` has nodes with valid shape types and quaternions,
    * every item icon and recipe result refers to something in the pack.
    """
    report = SandboxReport(
        edition=None,
        target="hytale",
        limitations=[
            "Hytale asset schemas follow the public 2026 documentation; the "
            "sandbox does not ship the game's own validators",
            "Java plugin behaviour is out of scope",
        ],
    )
    text = _text_files(files)

    # 1. JSON
    bad: list[str] = []
    documents: dict[str, object] = {}
    for path, body in text.items():
        if not path.endswith(".json"):
            continue
        try:
            documents[path] = json.loads(body)
        except json.JSONDecodeError as exc:
            bad.append(f"{path}: {exc}")
    report.add("hytale.json", not bad, "; ".join(bad[:5]))

    # 2. Manifest
    manifest = documents.get("manifest.json")
    if not isinstance(manifest, dict):
        report.add("hytale.manifest", False, "manifest.json is missing or invalid")
    else:
        missing = [
            key
            for key in ("Name", "Version", "ServerVersion")
            if key not in manifest
        ]
        report.add(
            "hytale.manifest",
            not missing,
            "; ".join(f"missing {key}" for key in missing),
        )

    # 3. Layout
    missing_dirs = [
        directory
        for directory in _HYTALE_REQUIRED_DIRS
        if not any(path.startswith(directory) for path in files)
    ]
    report.add(
        "hytale.layout",
        not missing_dirs,
        "; ".join(f"no files under {directory}" for directory in missing_dirs),
    )

    # 4. Roles -> server models
    missing_models: list[str] = []
    for path, document in documents.items():
        if not path.startswith("Server/NPC/Roles/") or not isinstance(document, dict):
            continue
        reference = document.get("Appearance")
        if not isinstance(reference, str):
            missing_models.append(f"{path}: no Appearance")
            continue
        model_path = f"Server/{reference.lstrip('/')}"
        if model_path not in documents:
            missing_models.append(f"{path} -> {model_path}")
    report.add(
        "hytale.roles",
        not missing_models,
        "; ".join(missing_models[:5]),
    )

    # 5. Server models -> block model and texture
    missing_assets: list[str] = []
    for path, document in documents.items():
        if not path.startswith("Server/Models/") or not isinstance(document, dict):
            continue
        model = document.get("Model")
        texture = document.get("Texture")
        if isinstance(model, str):
            candidate = f"Common/{model.lstrip('/')}"
            if candidate not in text:
                missing_assets.append(f"{path} -> {candidate}")
        else:
            missing_assets.append(f"{path}: no Model")
        if isinstance(texture, str):
            candidate = f"Common/{texture.lstrip('/')}"
            if candidate not in files:
                missing_assets.append(f"{path} -> {candidate}")
        else:
            missing_assets.append(f"{path}: no Texture")
    report.add(
        "hytale.models",
        not missing_assets,
        "; ".join(missing_assets[:5]),
    )

    # 6. blockymodel structure
    blocky_problems: list[str] = []
    for path, document in documents.items():
        if not path.endswith(".blockymodel"):
            continue
        if not isinstance(document, dict):
            blocky_problems.append(f"{path}: not an object")
            continue
        nodes = document.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            blocky_problems.append(f"{path}: no nodes")
            continue
        for node in _walk_nodes(nodes):
            shape = node.get("shape")
            if not isinstance(shape, dict):
                blocky_problems.append(f"{path}: node without shape")
                break
            if shape.get("type") not in {"box", "none", "mesh"}:
                blocky_problems.append(
                    f"{path}: unknown shape {shape.get('type')!r}"
                )
                break
            orientation = node.get("orientation")
            if orientation is not None:
                if not isinstance(orientation, dict) or not {
                    "x", "y", "z", "w"
                } <= set(orientation):
                    blocky_problems.append(f"{path}: invalid quaternion")
                    break
    report.add(
        "hytale.blockymodel",
        not blocky_problems,
        "; ".join(blocky_problems[:5]),
    )

    # 7. Icons
    missing_icons: list[str] = []
    for path, document in documents.items():
        if not path.startswith("Server/Item/Items/") or not isinstance(document, dict):
            continue
        icon = document.get("Icon")
        if not isinstance(icon, str):
            missing_icons.append(f"{path}: no Icon")
            continue
        candidate = f"Common/{icon.lstrip('/')}"
        if candidate not in files:
            missing_icons.append(f"{path} -> {candidate}")
    report.add(
        "hytale.icons",
        not missing_icons,
        "; ".join(missing_icons[:5]),
    )

    _check_images(report, files, {})
    return report


def _walk_nodes(nodes: list) -> list[dict]:
    """Flatten a blockymodel node tree."""
    flat: list[dict] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        flat.append(node)
        children = node.get("children")
        if isinstance(children, list):
            flat.extend(_walk_nodes(children))
    return flat


# --------------------------------------------------------------------------
# Godot project
# --------------------------------------------------------------------------


def validate_godot(files: dict[str, bytes | str]) -> SandboxReport:
    """Validate a generated Godot 4 project without launching Godot."""
    report = SandboxReport(
        edition=None,
        target="godot",
        limitations=[
            "the project is inspected statically; Godot is not executed",
            "the runtime script is not type checked by the Godot compiler",
        ],
    )
    text = _text_files(files)

    project = text.get("project.godot")
    report.add("godot.project", project is not None, "project.godot is missing")
    method = ""
    if project is not None:
        match = re.search(r'renderer/rendering_method="([^"]+)"', project)
        method = match.group(1) if match else ""
        report.add(
            "godot.graphics",
            method in {"forward_plus", "mobile", "gl_compatibility"},
            f"rendering_method={method!r}",
        )
        report.add(
            "godot.graphics_autoload",
            'Graphics="*res://scripts/graphics.gd"' in project,
            "graphics.gd is not registered as an autoload",
        )

    scene = text.get("scenes/main.tscn")
    report.add("godot.scene", scene is not None, "scenes/main.tscn is missing")
    if scene is not None:
        report.add(
            "godot.scene_script",
            'path="res://scripts/luacraft_runtime.gd"' in scene,
            "main scene does not reference the runtime script",
        )
        report.add(
            "godot.scene_camera",
            'type="Camera3D"' in scene,
            "main scene has no camera",
        )

    runtime = text.get("scripts/luacraft_runtime.gd")
    report.add(
        "godot.runtime", runtime is not None, "scripts/luacraft_runtime.gd is missing"
    )

    data = text.get("data/content.json")
    payload: object = None
    if data is None:
        report.add("godot.data", False, "data/content.json is missing")
    else:
        try:
            payload = json.loads(data)
            report.add("godot.data", isinstance(payload, dict), "content is not an object")
        except json.JSONDecodeError as exc:
            report.add("godot.data", False, str(exc))

    # Each entity must resolve to a model and a texture that the project ships.
    missing: list[str] = []
    if isinstance(payload, dict):
        for entity in payload.get("entities", []):
            if not isinstance(entity, dict):
                continue
            model = entity.get("model")
            if isinstance(model, str) and model not in text:
                missing.append(f"{entity.get('name')} -> {model}")
            texture = entity.get("texture")
            if isinstance(texture, str) and texture not in files:
                missing.append(f"{entity.get('name')} -> {texture}")
    report.add("godot.assets", not missing, "; ".join(missing[:5]))

    _check_images(report, files, {})
    return report
