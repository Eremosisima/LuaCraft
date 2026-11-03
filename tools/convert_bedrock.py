"""Convert a Bedrock add-on into a LuaCraft source project.

This is a *conversion* tool, not a Minecraft emulator.  It reads the
declarative parts of a Bedrock behaviour pack (items, blocks, recipes) and the
resource pack metadata (language files and texture presence), and writes a
LuaCraft ``mod.lua`` plus a ``textures/`` folder and a ``conversion.json``
report.

Entities are the reason this tool exists: Cobblemon, Fakemon and similar packs
ship their content as entities with models.  LuaCraft has no entity support, so
each entity becomes a **collectible species card**: a one-per-stack item that
keeps the species identity, its display name and its texture.  That is a
representable proxy, not an entity port, and the report says so.

Everything is offline and standard-library only.  Third-party textures are
never copied unless ``--copy-textures`` is passed explicitly; by default the
converter only records that a texture exists and LuaCraft generates a
deterministic placeholder.

Usage::

    py tools/convert_bedrock.py pack.mcaddon --out out/converted/pack
    py tools/convert_bedrock.py pack.zip --namespace pmon --copy-textures
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from luacraft import bedrock_geometry  # noqa: E402
from luacraft.textures import placeholder_png  # noqa: E402

#: Bedrock recipe root keys this converter understands.
RECIPE_KEYS = {
    "minecraft:recipe_shaped": "shaped",
    "minecraft:recipe_shapeless": "shapeless",
}

#: LuaCraft creative categories.
CATEGORIES = {"construction", "equipment", "items", "nature", "none"}

#: File families that this converter cannot represent.
UNSUPPORTED = {
    "animations": ("/animations/",),
    "render_controllers": ("/render_controllers/",),
    "spawn_rules": ("/spawn_rules/",),
    "scripts": ("/scripts/",),
    "functions": ("/functions/",),
    "particles": ("/particles/",),
    "sounds": ("/sounds/",),
    "structures": ("/structures/",),
    "dialogue": ("/dialogue/",),
    "trade_tables": ("/trading/",),
}

_TEXTURE_ROOT = re.compile(r"(?:^|/)textures/(?:entity|items|blocks)/", re.I)
_SLUG = re.compile(r"[^a-z0-9_]+")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def slugify(value: str) -> str:
    """A safe Bedrock/Luanti identifier fragment."""
    slug = _SLUG.sub("_", value.strip().lower()).strip("_")
    if not slug:
        slug = "entry"
    if slug[0].isdigit():
        slug = f"x_{slug}"
    return slug


def humanize(value: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[_\-\s]+", value) if part)


def lua_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def lua_value(value: Any, indent: int = 0) -> str:
    """Render a Python value as a Lua literal."""
    pad = "  " * indent
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, dict):
        inner = "  " * (indent + 1)
        lines = [
            f"{inner}[{lua_string(str(key))}] = {lua_value(item, indent + 1)},"
            for key, item in value.items()
        ]
        return "{\n" + "\n".join(lines) + f"\n{pad}}}"
    if isinstance(value, (list, tuple)):
        inner = "  " * (indent + 1)
        lines = [f"{inner}{lua_value(item, indent + 1)}," for item in value]
        return "{\n" + "\n".join(lines) + f"\n{pad}}}"
    if value is None:
        return "nil"
    return lua_string(str(value))


def qualified(identifier: str, namespace: str) -> str:
    return identifier if ":" in identifier else f"{namespace}:{identifier}"


# --------------------------------------------------------------------------
# Pack reading
# --------------------------------------------------------------------------


class PackReader:
    """Read a ``.mcaddon``/``.zip`` or an unpacked pack directory once."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._zip: zipfile.ZipFile | None = None
        if path.is_dir():
            self._names = sorted(
                file.relative_to(path).as_posix()
                for file in path.rglob("*")
                if file.is_file()
            )
        else:
            self._zip = zipfile.ZipFile(path)
            self._names = sorted(
                name for name in self._zip.namelist() if not name.endswith("/")
            )
        #: Lowercase path -> real path, for case-insensitive lookups.
        self._lower = {name.lower(): name for name in self._names}

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()

    def __enter__(self) -> "PackReader":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def names(self) -> list[str]:
        return self._names

    def read(self, name: str) -> bytes:
        if self._zip is not None:
            return self._zip.read(name)
        return (self.path / name).read_bytes()

    def find(self, suffix: str) -> list[str]:
        suffix = suffix.lower()
        return [name for name in self._names if name.lower().endswith(suffix)]

    def read_json(self, name: str) -> Any:
        try:
            return json.loads(self.read(name).decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None


@dataclass
class Lang:
    """Language keys from the resource pack."""

    values: dict[str, str] = field(default_factory=dict)
    source: str = ""

    def entity_name(self, identifier: str) -> str | None:
        return (
            self.values.get(f"entity.{identifier}.name")
            or self.values.get(f"entity.{identifier}")
        )

    def item_name(self, identifier: str) -> str | None:
        return (
            self.values.get(f"item.{identifier}.name")
            or self.values.get(f"item.{identifier}")
        )


def read_lang(reader: PackReader) -> Lang:
    """Read the English language file, falling back to the first ``.lang``."""
    candidates = [n for n in reader.names if n.lower().endswith(".lang")]
    preferred = [n for n in candidates if n.lower().endswith(("en_us.lang", "en_gb.lang"))]
    ordered = preferred + [n for n in candidates if n not in preferred]
    for name in ordered:
        values: dict[str, str] = {}
        try:
            text = reader.read(name).decode("utf-8-sig", "replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
        if values:
            return Lang(values=values, source=name)
    return Lang()


def texture_index(reader: PackReader) -> dict[str, str]:
    """Map every texture file stem to its path, for one-pass lookups."""
    index: dict[str, str] = {}
    for name in reader.names:
        if not name.lower().endswith(".png"):
            continue
        stem = Path(name).stem
        index.setdefault(stem.lower(), name)
        # Cobblemon nests one folder per species: <name>/<name>.png
        if "/" in name:
            parent = Path(name).parent.name
            index.setdefault(parent.lower(), name)
    return index


# --------------------------------------------------------------------------
# Parsed model
# --------------------------------------------------------------------------


@dataclass
class ConvertedItem:
    """An item the converter can emit into LuaCraft."""

    name: str
    identifier: str
    texture: str
    display_name: str
    max_stack: int = 64
    category: str = "items"
    source: str = ""
    durability: int | None = None
    glint: bool | None = None
    hand_equipped: bool | None = None
    food: dict[str, Any] | None = None
    fuel: dict[str, Any] | None = None
    extra_components: dict[str, Any] = field(default_factory=dict)
    has_source_texture: bool = False
    texture_source: str | None = None
    kind: str = "item"


@dataclass
class ConvertedBlock:
    name: str
    identifier: str
    texture: str
    hardness: float | None = None
    resistance: float | None = None
    light_emission: int | None = None
    friction: float | None = None
    map_color: str | None = None
    category: str = "construction"
    states: dict[str, Any] = field(default_factory=dict)
    extra_components: dict[str, Any] = field(default_factory=dict)
    has_source_texture: bool = False
    texture_source: str | None = None


@dataclass
class ConvertedRecipe:
    name: str
    identifier: str
    type: str
    result: str
    count: int = 1
    pattern: list[str] = field(default_factory=list)
    key: dict[str, str] = field(default_factory=dict)
    ingredients: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source: str = ""


@dataclass
class ConvertedEntity:
    """An entity the converter can carry into LuaCraft.

    This is a *semantic* record: two backends turn it into a real entity
    (Luanti) or an NPC role plus block model (Hytale), while Bedrock and Java
    keep the species card because neither can add entities from a pack.
    """

    name: str
    identifier: str
    display_name: str
    texture: str
    model: str | None = None
    width: float = 0.6
    height: float = 1.8
    health: float | None = None
    movement_speed: float | None = None
    hostile: bool = False
    spawnable: bool = True
    tameable: dict[str, Any] | None = None
    drops: list[dict[str, Any]] = field(default_factory=list)
    families: list[str] = field(default_factory=list)
    texture_width: int | None = None
    texture_height: int | None = None
    visible_width: float | None = None
    visible_height: float | None = None
    source: str = ""
    unsupported: list[str] = field(default_factory=list)
    has_source_texture: bool = False
    texture_source: str | None = None


@dataclass
class Conversion:
    """The result of converting one pack."""

    source: str
    namespace: str
    pack_name: str
    items: list[ConvertedItem] = field(default_factory=list)
    blocks: list[ConvertedBlock] = field(default_factory=list)
    recipes: list[ConvertedRecipe] = field(default_factory=list)
    lang_source: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    unsupported: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    entity_ids: list[str] = field(default_factory=list)
    entities: list[ConvertedEntity] = field(default_factory=list)
    #: OBJ models keyed by model name, ready for the Luanti backend.
    models: dict[str, str] = field(default_factory=dict)
    #: ``.blockymodel`` documents keyed by model name, ready for Hytale.
    blockymodels: dict[str, dict[str, Any]] = field(default_factory=dict)
    textures: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "namespace": self.namespace,
            "pack_name": self.pack_name,
            "counts": self.counts,
            "unsupported": self.unsupported,
            "lang_source": self.lang_source,
            "textures": self.textures,
            "notes": self.notes,
            "skipped": self.skipped,
            "entity_ids_sample": self.entity_ids[:20],
            "entities": [
                {
                    "name": entity.name,
                    "identifier": entity.identifier,
                    "display_name": entity.display_name,
                    "model": entity.model,
                    "texture": entity.texture,
                    "health": entity.health,
                    "collision": {"width": entity.width, "height": entity.height},
                    "visible": {
                        "width": entity.visible_width,
                        "height": entity.visible_height,
                    },
                    "movement_speed": entity.movement_speed,
                    "hostile": entity.hostile,
                    "spawnable": entity.spawnable,
                    "tameable": entity.tameable,
                    "drops": entity.drops,
                    "families": entity.families,
                    "unsupported": entity.unsupported,
                    "has_source_texture": entity.has_source_texture,
                    "texture_source": entity.texture_source,
                    "source": entity.source,
                }
                for entity in self.entities
            ],
            "items": [
                {
                    "name": item.name,
                    "identifier": item.identifier,
                    "texture": item.texture,
                    "display_name": item.display_name,
                    "kind": item.kind,
                    "source": item.source,
                    "has_source_texture": item.has_source_texture,
                    "texture_source": item.texture_source,
                    "extra_components": sorted(item.extra_components),
                }
                for item in self.items
            ],
            "blocks": [
                {
                    "name": block.name,
                    "identifier": block.identifier,
                    "texture": block.texture,
                    "source": block.texture_source,
                    "has_source_texture": block.has_source_texture,
                }
                for block in self.blocks
            ],
            "recipes": [
                {
                    "name": recipe.name,
                    "identifier": recipe.identifier,
                    "type": recipe.type,
                    "result": recipe.result,
                    "count": recipe.count,
                    "source": recipe.source,
                }
                for recipe in self.recipes
            ],
        }


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("value", "max", "seconds_to_destroy"):
            if key in value:
                return _num(value[key])
    return None


def _int(value: Any) -> int | None:
    number = _num(value)
    return int(number) if number is not None else None


def _category(value: Any, default: str) -> str:
    if isinstance(value, dict):
        value = value.get("category", default)
    text = str(value or default).lower()
    return text if text in CATEGORIES else default


def _icon_texture(components: dict[str, Any], fallback: str) -> str:
    icon = components.get("minecraft:icon") or components.get("icon")
    if isinstance(icon, dict):
        icon = icon.get("texture") or icon.get("value")
    if isinstance(icon, str) and icon:
        return slugify(icon)
    return slugify(fallback)


#: Components the converter translates, so they are not reported as lost.
_TRANSLATED_COMPONENTS = frozenset(
    {
        "minecraft:collision_box",
        "minecraft:health",
        "minecraft:movement",
        "minecraft:type_family",
        "minecraft:loot",
        "minecraft:tameable",
        "minecraft:is_tameable",
        "minecraft:physics",
        "minecraft:pushable",
        "minecraft:nameable",
        "minecraft:persistent",
        "minecraft:despawn",
    }
)

_HOSTILE_FAMILIES = frozenset({"monster", "hostile", "mob"})


def _entity_identifiers(reader: PackReader) -> list[str]:
    """Entity identifiers from behaviour-pack entity files."""
    identifiers: list[str] = []
    seen: set[str] = set()
    for name in reader.names:
        low = name.lower()
        if "/entities/" not in low or not low.endswith(".json"):
            continue
        data = reader.read_json(name)
        if not isinstance(data, dict):
            continue
        description = (data.get("minecraft:entity") or {}).get("description") or {}
        identifier = description.get("identifier")
        if not isinstance(identifier, str) or identifier in seen:
            continue
        seen.add(identifier)
        identifiers.append(identifier)
    return identifiers


def _client_entities(reader: PackReader) -> dict[str, dict[str, Any]]:
    """Client entity descriptions, keyed by identifier.

    The resource pack is where the texture and geometry references live; the
    behaviour pack only describes gameplay.
    """
    clients: dict[str, dict[str, Any]] = {}
    for name in reader.names:
        low = name.lower()
        if "/entity/" not in low or not low.endswith(".entity.json"):
            continue
        data = reader.read_json(name)
        if not isinstance(data, dict):
            continue
        body = data.get("minecraft:client_entity") or {}
        description = body.get("description") or {}
        identifier = description.get("identifier")
        if not isinstance(identifier, str):
            continue
        clients[identifier] = description
    return clients


def _geometry_index(
    reader: PackReader,
) -> dict[str, tuple[str, "bedrock_geometry.Geometry"]]:
    """Every geometry in the pack, keyed by identifier and by file stem."""
    index: dict[str, tuple[str, bedrock_geometry.Geometry]] = {}
    for name in reader.names:
        if not name.lower().endswith(".geo.json"):
            continue
        data = reader.read_json(name)
        for geometry in bedrock_geometry.parse_geometries(data, source=name):
            index.setdefault(geometry.identifier.lower(), (name, geometry))
            index.setdefault(geometry.identifier.split(".", 1)[-1].lower(), (name, geometry))
            stem = Path(name).name
            for suffix in (".geo.json", ".json"):
                if stem.lower().endswith(suffix):
                    stem = stem[: -len(suffix)]
                    break
            index.setdefault(stem.lower(), (name, geometry))
    return index


def _loot_index(reader: PackReader) -> dict[str, str]:
    """Loot table paths by their pack-relative suffix."""
    index: dict[str, str] = {}
    for name in reader.names:
        if "/loot_tables/" not in name.lower() or not name.lower().endswith(".json"):
            continue
        marker = name.lower().find("loot_tables/")
        index[name[marker:].lower()] = name
    return index


def _loot_drops(
    reader: PackReader, loot_index: dict[str, str], table: str
) -> list[dict[str, Any]]:
    """Turn a Bedrock loot table into simple drop entries."""
    key = table.lower().replace("\\", "/")
    path = loot_index.get(key)
    if path is None:
        # The pack may prefix the table with a folder name.
        for suffix, candidate in loot_index.items():
            if suffix.endswith(key):
                path = candidate
                break
    if path is None:
        return []
    data = reader.read_json(path)
    if not isinstance(data, dict):
        return []

    drops: list[dict[str, Any]] = []
    for pool in data.get("pools") or []:
        if not isinstance(pool, dict):
            continue
        entries = pool.get("entries") or []
        entries = [entry for entry in entries if isinstance(entry, dict)]
        weight_total = sum(float(entry.get("weight", 1)) for entry in entries) or 1.0
        for entry in entries:
            if entry.get("type", "item") != "item":
                continue
            item = entry.get("name")
            if not isinstance(item, str):
                continue
            minimum, maximum = 1, 1
            for function in entry.get("functions") or []:
                if not isinstance(function, dict):
                    continue
                if function.get("function") != "set_count":
                    continue
                count = function.get("count")
                if isinstance(count, dict):
                    minimum = int(_num(count.get("min")) or 1)
                    maximum = int(_num(count.get("max")) or minimum)
                elif count is not None:
                    minimum = maximum = int(_num(count) or 1)
            drops.append(
                {
                    "item": item,
                    "min": max(1, minimum),
                    "max": max(minimum, maximum),
                    "chance": min(
                        1.0, float(entry.get("weight", 1)) / weight_total
                    ),
                }
            )
    return drops


def _collect_entities(
    reader: PackReader,
    lang: Lang,
    textures: dict[str, str],
    conversion: Conversion,
    used_names: set[str],
) -> list[ConvertedEntity]:
    """Parse behaviour entities, client entities and geometry in one pass."""
    clients = _client_entities(reader)
    geometries = _geometry_index(reader)
    loot_index = _loot_index(reader)
    entities: list[ConvertedEntity] = []
    seen: set[str] = set()

    for name in reader.names:
        low = name.lower()
        if "/entities/" not in low or not low.endswith(".json"):
            continue
        data = reader.read_json(name)
        if not isinstance(data, dict):
            continue
        body = data.get("minecraft:entity") or {}
        description = body.get("description") or {}
        identifier = description.get("identifier")
        if not isinstance(identifier, str) or identifier in seen:
            continue
        seen.add(identifier)

        bare = identifier.split(":", 1)[-1]
        entity_name = slugify(bare)
        while entity_name in used_names:
            entity_name = f"{entity_name}_mob"
        used_names.add(entity_name)

        components: dict[str, Any] = dict(body.get("components") or {})
        for group in (body.get("component_groups") or {}).values():
            if isinstance(group, dict):
                # Only merge components that add behaviour; later groups win.
                for key, value in group.items():
                    if key.startswith("minecraft:behavior.") or key in {
                        "minecraft:health",
                        "minecraft:movement",
                        "minecraft:collision_box",
                    }:
                        components.setdefault(key, value)

        client = clients.get(identifier) or {}
        raw_textures = client.get("textures") or {}
        texture_ref = ""
        if isinstance(raw_textures, dict):
            texture_ref = str(raw_textures.get("default") or next(iter(raw_textures.values()), ""))
        elif isinstance(raw_textures, list) and raw_textures:
            texture_ref = str(raw_textures[0])
        texture_stem = slugify(Path(texture_ref).stem) if texture_ref else entity_name
        texture_source = None
        if texture_ref:
            lowered = texture_ref.lower()
            for suffix in (".png",):
                if lowered.endswith(suffix):
                    lowered = lowered[: -len(suffix)]
            texture_source = textures.get(Path(lowered).stem.lower())
        if texture_source is None:
            texture_source = textures.get(bare.lower()) or textures.get(texture_stem.lower())

        raw_geometry = client.get("geometry") or {}
        geometry_ref = ""
        if isinstance(raw_geometry, dict):
            geometry_ref = str(
                raw_geometry.get("default") or next(iter(raw_geometry.values()), "")
            )
        elif isinstance(raw_geometry, str):
            geometry_ref = raw_geometry
        geometry: bedrock_geometry.Geometry | None = None
        if geometry_ref:
            entry = geometries.get(geometry_ref.lower())
            if entry is None:
                entry = geometries.get(geometry_ref.split(".", 1)[-1].lower())
            if entry is not None:
                geometry = entry[1]
        if geometry is None:
            entry = geometries.get(bare.lower())
            if entry is not None:
                geometry = entry[1]

        model_name: str | None = None
        texture_width = None
        texture_height = None
        visible_width = None
        visible_height = None
        if geometry is not None:
            model_name = slugify(f"{bare}_model")
            try:
                obj = bedrock_geometry.to_obj(geometry, name=model_name)
                conversion.models[model_name] = obj
                conversion.blockymodels[model_name] = bedrock_geometry.to_blockymodel(
                    geometry, name=model_name
                )
            except Exception:  # pragma: no cover - a malformed model must not stop the pack
                model_name = None
            texture_width = geometry.texture_width
            texture_height = geometry.texture_height
            visible_width = geometry.visible_width
            visible_height = geometry.visible_height

        collision = components.get("minecraft:collision_box") or {}
        width = _num(collision.get("width")) if isinstance(collision, dict) else None
        height = _num(collision.get("height")) if isinstance(collision, dict) else None
        if width is None and geometry is not None:
            lower, upper = bedrock_geometry.bounds_blocks(geometry)
            width = max(0.1, upper[0] - lower[0])
        if height is None and geometry is not None:
            lower, upper = bedrock_geometry.bounds_blocks(geometry)
            height = max(0.1, upper[1] - lower[1])
        width = width if width and width > 0 else 0.6
        height = height if height and height > 0 else 1.8

        health = _num(components.get("minecraft:health"))
        movement = _num(components.get("minecraft:movement"))
        families: list[str] = []
        family_value = (components.get("minecraft:type_family") or {}).get("family")
        if isinstance(family_value, list):
            families = [str(value) for value in family_value]
        hostile = bool(_HOSTILE_FAMILIES & {value.lower() for value in families}) or any(
            key.startswith("minecraft:behavior.nearest_attackable_target")
            or key.startswith("minecraft:behavior.melee_attack")
            for key in components
        )

        tame_component = components.get("minecraft:tameable") or components.get(
            "minecraft:is_tameable"
        )
        tameable = None
        if isinstance(tame_component, dict):
            items = tame_component.get("tame_items") or tame_component.get("items") or []
            if isinstance(items, str):
                items = [items]
            tameable = {
                "probability": _num(tame_component.get("probability")) or 1.0,
                "tame_items": [str(item) for item in items if isinstance(item, str)],
            }

        drops: list[dict[str, Any]] = []
        loot = components.get("minecraft:loot")
        if isinstance(loot, dict) and isinstance(loot.get("table"), str):
            drops = _loot_drops(reader, loot_index, loot["table"])

        unsupported = sorted(
            key
            for key in components
            if key not in _TRANSLATED_COMPONENTS
            and not key.startswith("minecraft:behavior.")
            and key
            not in {
                "minecraft:navigation.walk",
                "minecraft:navigation.generic",
                "minecraft:movement.basic",
                "minecraft:movement.generic",
                "minecraft:jump.static",
                "minecraft:jump.dynamic",
                "minecraft:can_climb",
                "minecraft:breathable",
            }
        )
        unsupported.extend(
            f"{key} (AI)"
            for key in components
            if key.startswith("minecraft:behavior.")
        )
        if texture_ref and not texture_source:
            unsupported.append("texture missing from the pack")
        if geometry_ref and geometry is None:
            unsupported.append("geometry file missing from the pack")

        entity = ConvertedEntity(
            name=entity_name,
            identifier=identifier,
            display_name=lang.entity_name(identifier) or humanize(bare),
            texture=texture_stem,
            model=model_name,
            width=width,
            height=height,
            health=health,
            movement_speed=movement,
            hostile=hostile,
            spawnable=bool(description.get("is_spawnable", False)),
            tameable=tameable,
            drops=drops,
            families=families,
            texture_width=texture_width,
            texture_height=texture_height,
            visible_width=visible_width,
            visible_height=visible_height,
            source=name,
            unsupported=unsupported,
            has_source_texture=texture_source is not None,
            texture_source=texture_source,
        )
        entities.append(entity)
        conversion.entities.append(entity)
    return entities


def _collect_items(
    reader: PackReader,
    lang: Lang,
    textures: dict[str, str],
    conversion: Conversion,
    used_names: set[str],
) -> None:
    for name in reader.names:
        low = name.lower()
        if "/items/" not in low or not low.endswith(".json"):
            continue
        data = reader.read_json(name)
        if not isinstance(data, dict):
            continue
        body = data.get("minecraft:item") or {}
        description = body.get("description") or {}
        identifier = description.get("identifier")
        if not isinstance(identifier, str):
            continue
        components = body.get("components") or {}
        bare = identifier.split(":", 1)[-1]
        item_name = slugify(bare)
        while item_name in used_names:
            item_name = f"{item_name}_item"
        used_names.add(item_name)
        texture = _icon_texture(components, bare)
        texture_source = textures.get(texture.lower())
        display = lang.item_name(identifier) or humanize(bare)

        food = components.get("minecraft:food")
        fuel = components.get("minecraft:fuel")
        extra = {
            key: value
            for key, value in components.items()
            if key
            not in {
                "minecraft:icon",
                "minecraft:max_stack_size",
                "minecraft:durability",
                "minecraft:glint",
                "minecraft:hand_equipped",
                "minecraft:food",
                "minecraft:fuel",
                "minecraft:display_name",
            }
        }
        conversion.items.append(
            ConvertedItem(
                name=item_name,
                identifier=identifier,
                texture=texture,
                display_name=display,
                max_stack=_int(components.get("minecraft:max_stack_size")) or 64,
                category=_category(
                    description.get("menu_category"), "items"
                ),
                source=name,
                durability=_int(components.get("minecraft:durability")),
                glint=components.get("minecraft:glint")
                if isinstance(components.get("minecraft:glint"), bool)
                else None,
                hand_equipped=components.get("minecraft:hand_equipped")
                if isinstance(components.get("minecraft:hand_equipped"), bool)
                else None,
                food=dict(food) if isinstance(food, dict) else None,
                fuel=dict(fuel) if isinstance(fuel, dict) else None,
                extra_components=extra,
                has_source_texture=texture_source is not None,
                texture_source=texture_source,
                kind="item",
            )
        )
        if extra:
            conversion.notes.append(
                f"{identifier}: components not represented by LuaCraft: "
                + ", ".join(sorted(extra))
            )


def _collect_blocks(
    reader: PackReader,
    textures: dict[str, str],
    conversion: Conversion,
    used_names: set[str],
) -> None:
    for name in reader.names:
        low = name.lower()
        if "/blocks/" not in low or not low.endswith(".json"):
            continue
        data = reader.read_json(name)
        if not isinstance(data, dict):
            continue
        body = data.get("minecraft:block") or {}
        description = body.get("description") or {}
        identifier = description.get("identifier")
        if not isinstance(identifier, str):
            continue
        bare = identifier.split(":", 1)[-1]
        block_name = slugify(bare)
        while block_name in used_names:
            block_name = f"{block_name}_block"
        used_names.add(block_name)
        components = body.get("components") or {}

        material = components.get("minecraft:material_instances") or {}
        texture = slugify(bare)
        if isinstance(material, dict):
            first = next(iter(material.values()), None)
            if isinstance(first, dict) and isinstance(first.get("texture"), str):
                texture = slugify(first["texture"])
            elif isinstance(first, str):
                texture = slugify(first)
        texture_source = textures.get(texture.lower())

        extra = {
            key: value
            for key, value in components.items()
            if key
            not in {
                "minecraft:destructible_by_mining",
                "minecraft:destructible_by_explosion",
                "minecraft:light_emission",
                "minecraft:friction",
                "minecraft:map_color",
                "minecraft:material_instances",
            }
        }
        conversion.blocks.append(
            ConvertedBlock(
                name=block_name,
                identifier=identifier,
                texture=texture,
                hardness=_num(components.get("minecraft:destructible_by_mining")),
                resistance=_num(
                    components.get("minecraft:destructible_by_explosion")
                ),
                light_emission=_int(components.get("minecraft:light_emission")),
                friction=_num(components.get("minecraft:friction")),
                map_color=components.get("minecraft:map_color")
                if isinstance(components.get("minecraft:map_color"), str)
                else None,
                category=_category(description.get("menu_category"), "construction"),
                states=description.get("states")
                if isinstance(description.get("states"), dict)
                else {},
                extra_components=extra,
                has_source_texture=texture_source is not None,
                texture_source=texture_source,
            )
        )
        if extra:
            conversion.notes.append(
                f"{identifier}: block components not represented: "
                + ", ".join(sorted(extra))
            )


def _ingredient(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        item = value.get("item") or value.get("id")
        if isinstance(item, str):
            return item
    return None


def _collect_recipes(
    reader: PackReader, conversion: Conversion, used_names: set[str]
) -> None:
    for name in reader.names:
        low = name.lower()
        if "/recipes/" not in low or not low.endswith(".json"):
            continue
        data = reader.read_json(name)
        if not isinstance(data, dict):
            continue
        for root, recipe_type in RECIPE_KEYS.items():
            body = data.get(root)
            if not isinstance(body, dict):
                continue
            description = body.get("description") or {}
            identifier = description.get("identifier")
            if not isinstance(identifier, str):
                continue
            result = body.get("result") or {}
            if isinstance(result, str):
                result_item, count = result, 1
            elif isinstance(result, dict):
                result_item = result.get("item")
                count = _int(result.get("count")) or 1
            else:
                result_item, count = None, 1
            if not isinstance(result_item, str):
                conversion.skipped.append(
                    {"source": name, "reason": "recipe has no item result"}
                )
                continue

            recipe_name = slugify(identifier.split(":", 1)[-1])
            while recipe_name in used_names:
                recipe_name = f"{recipe_name}_recipe"
            used_names.add(recipe_name)

            converted = ConvertedRecipe(
                name=recipe_name,
                identifier=identifier,
                type=recipe_type,
                result=result_item,
                count=count,
                tags=[t for t in body.get("tags", []) if isinstance(t, str)],
                source=name,
            )
            if recipe_type == "shaped":
                pattern = body.get("pattern")
                if isinstance(pattern, list) and all(
                    isinstance(row, str) for row in pattern
                ):
                    converted.pattern = list(pattern)
                raw_key = body.get("key") or {}
                if isinstance(raw_key, dict):
                    for character, entry in raw_key.items():
                        item = _ingredient(entry)
                        if item and isinstance(character, str) and len(character) == 1:
                            converted.key[character] = item
            else:
                ingredients = body.get("ingredients") or []
                converted.ingredients = [
                    item
                    for item in (_ingredient(entry) for entry in ingredients)
                    if item is not None
                ]
            conversion.recipes.append(converted)
            break


def _count_unsupported(reader: PackReader) -> dict[str, int]:
    counts: dict[str, int] = {family: 0 for family in UNSUPPORTED}
    for name in reader.names:
        low = name.lower()
        if not low.endswith(".json"):
            continue
        for family, markers in UNSUPPORTED.items():
            if any(marker in low for marker in markers):
                counts[family] += 1
                break
    return counts


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------


def convert_pack(
    path: Path,
    *,
    namespace: str | None = None,
    pack_name: str | None = None,
    copy_textures: bool = False,
) -> Conversion:
    """Read one pack and return the representable LuaCraft content."""
    if not path.exists():
        raise FileNotFoundError(path)

    with PackReader(path) as reader:
        lang = read_lang(reader)
        textures = texture_index(reader)
        entity_ids = _entity_identifiers(reader)

        inferred = namespace
        if inferred is None and entity_ids:
            inferred = entity_ids[0].split(":", 1)[0]
        if inferred is None:
            inferred = slugify(path.stem)
        namespace = inferred
        pack_name = pack_name or f"{path.stem} (LuaCraft conversion)"

        conversion = Conversion(
            source=str(path),
            namespace=namespace,
            pack_name=pack_name,
            lang_source=lang.source,
            entity_ids=entity_ids,
        )

        used_names: set[str] = set()
        # Entities first: their real names take priority over card names.
        entities = _collect_entities(reader, lang, textures, conversion, used_names)

        for entity in entities:
            card_name = f"species_{slugify(entity.name)}"
            while card_name in used_names:
                card_name = f"{card_name}_card"
            used_names.add(card_name)
            conversion.items.append(
                ConvertedItem(
                    name=card_name,
                    identifier=f"{namespace}:{card_name}",
                    texture=entity.texture,
                    display_name=f"{entity.display_name} (species card)",
                    max_stack=1,
                    category="items",
                    source=entity.identifier,
                    kind="species_card",
                    has_source_texture=entity.has_source_texture,
                    texture_source=entity.texture_source,
                )
            )

        _collect_items(reader, lang, textures, conversion, used_names)
        _collect_blocks(reader, textures, conversion, used_names)
        _collect_recipes(reader, conversion, used_names)
        conversion.unsupported = _count_unsupported(reader)

    conversion.counts = {
        "entities": len(entities),
        "entities_with_model": sum(1 for entity in entities if entity.model),
        "entities_with_texture": sum(1 for entity in entities if entity.has_source_texture),
        "entities_with_drops": sum(1 for entity in entities if entity.drops),
        "entities_tameable": sum(1 for entity in entities if entity.tameable),
        "blockymodels": len(conversion.blockymodels),
        "species_cards": sum(
            1 for item in conversion.items if item.kind == "species_card"
        ),
        "items": sum(1 for item in conversion.items if item.kind == "item"),
        "blocks": len(conversion.blocks),
        "recipes": len(conversion.recipes),
        "shaped_recipes": sum(
            1 for recipe in conversion.recipes if recipe.type == "shaped"
        ),
        "shapeless_recipes": sum(
            1 for recipe in conversion.recipes if recipe.type == "shapeless"
        ),
    }

    conversion.notes.insert(
        0,
        (
            "Entities are parsed as real entities: movement speed, health, "
            "collision box, taming, loot drops and Bedrock geometry are carried. "
            "Luanti registers them with OBJ models; Hytale gets NPC roles and "
            "block models. Bedrock and Java cannot add entities from a pack, so "
            "they keep the species card as a proxy."
        ),
    )
    if conversion.entities:
        conversion.notes.append(
            (
                "Animations, render controllers, Molang and behaviour AI are not "
                "ported. Luanti gets a small procedural wander/chase AI; Hytale "
                "roles are static until a Java plugin is written."
            )
        )
    conversion.notes.append(
        (
            "Textures: copies of third-party art are disabled by default; "
            "LuaCraft generates deterministic placeholders. Pass "
            "--copy-textures only if the pack licence allows redistribution."
        )
    )
    return conversion


# --------------------------------------------------------------------------
# Writing the LuaCraft project
# --------------------------------------------------------------------------


def render_lua(conversion: Conversion) -> str:
    lines: list[str] = []
    lines.append("-- Generated by LuaCraft tools/convert_bedrock.py")
    lines.append(f"-- Source: {conversion.source}")
    lines.append(
        "-- Entities carry model, hitbox, health, taming and drops; Luanti "
        "registers them, Bedrock and Java keep the species card."
    )
    lines.append("")

    for item in conversion.items:
        lines.append(f"items.{item.name} = {{")
        lines.append(f"  id = {lua_string(item.identifier)},")
        lines.append(f"  texture = {lua_string(item.texture)},")
        lines.append(f"  display_name = {lua_string(item.display_name)},")
        lines.append(f"  max_stack = {item.max_stack},")
        lines.append(f"  category = {lua_string(item.category)},")
        if item.kind == "species_card" and item.source:
            lines.append(f"  fallback_for = {lua_string(item.source)},")
        if item.durability is not None:
            lines.append(f"  durability = {item.durability},")
        if item.glint is not None:
            lines.append(f"  glint = {'true' if item.glint else 'false'},")
        if item.hand_equipped is not None:
            lines.append(
                f"  hand_equipped = {'true' if item.hand_equipped else 'false'},"
            )
        if item.food:
            lines.append(f"  food = {lua_value(item.food, 1)},")
        if item.fuel:
            lines.append(f"  fuel = {lua_value(item.fuel, 1)},")
        lines.append("}")
        lines.append("")

    for entity in conversion.entities:
        lines.append(f"entities.{entity.name} = {{")
        lines.append(f"  id = {lua_string(entity.identifier)},")
        lines.append(f"  texture = {lua_string(entity.texture)},")
        if entity.model:
            lines.append(f"  model = {lua_string(entity.model)},")
        lines.append(f"  display_name = {lua_string(entity.display_name)},")
        lines.append(
            "  collision_box = {width = "
            f"{entity.width!r}, height = {entity.height!r}}},"
        )
        if entity.health is not None:
            lines.append(f"  health = {entity.health!r},")
        if entity.movement_speed is not None:
            lines.append(f"  movement_speed = {entity.movement_speed!r},")
        lines.append(f"  hostile = {'true' if entity.hostile else 'false'},")
        lines.append(f"  spawnable = {'true' if entity.spawnable else 'false'},")
        if entity.tameable:
            lines.append(f"  tameable = {lua_value(entity.tameable, 1)},")
        if entity.drops:
            lines.append(f"  drops = {lua_value(entity.drops, 1)},")
        if entity.families:
            lines.append(f"  families = {lua_value(entity.families, 1)},")
        if entity.texture_width:
            lines.append(f"  texture_width = {entity.texture_width},")
        if entity.texture_height:
            lines.append(f"  texture_height = {entity.texture_height},")
        if entity.visible_width:
            lines.append(f"  visible_width = {entity.visible_width!r},")
        if entity.visible_height:
            lines.append(f"  visible_height = {entity.visible_height!r},")
        if entity.unsupported:
            lines.append(f"  unsupported = {lua_value(entity.unsupported, 1)},")
        lines.append(f"  source = {lua_string(entity.source)},")
        lines.append("}")
        lines.append("")

    for block in conversion.blocks:
        lines.append(f"blocks.{block.name} = {{")
        lines.append(f"  id = {lua_string(block.identifier)},")
        lines.append(f"  texture = {lua_string(block.texture)},")
        lines.append(f"  category = {lua_string(block.category)},")
        if block.hardness is not None:
            lines.append(f"  hardness = {block.hardness!r},")
        if block.resistance is not None:
            lines.append(f"  resistance = {block.resistance!r},")
        if block.light_emission is not None:
            lines.append(f"  light_emission = {block.light_emission},")
        if block.friction is not None:
            lines.append(f"  friction = {block.friction!r},")
        if block.map_color:
            lines.append(f"  map_color = {lua_string(block.map_color)},")
        if block.states:
            lines.append(f"  states = {lua_value(block.states, 1)},")
        lines.append("}")
        lines.append("")

    for recipe in conversion.recipes:
        lines.append(f"recipes.{recipe.name} = {{")
        lines.append(f"  id = {lua_string(recipe.identifier)},")
        lines.append(f"  type = {lua_string(recipe.type)},")
        if recipe.type == "shaped":
            lines.append(f"  pattern = {lua_value(recipe.pattern, 1)},")
            lines.append(f"  key = {lua_value(recipe.key, 1)},")
        else:
            lines.append(f"  ingredients = {lua_value(recipe.ingredients, 1)},")
        lines.append(f"  result = {lua_string(recipe.result)},")
        lines.append(f"  count = {recipe.count},")
        if recipe.tags:
            lines.append(f"  tags = {lua_value(recipe.tags, 1)},")
        lines.append("}")
        lines.append("")

    return "\n".join(lines)


def write_project(
    conversion: Conversion,
    output: Path,
    *,
    copy_textures: bool = False,
) -> dict[str, Path]:
    """Write ``mod.lua``, ``textures/`` and ``conversion.json``."""
    output.mkdir(parents=True, exist_ok=True)
    texture_dir = output / "textures"
    texture_dir.mkdir(parents=True, exist_ok=True)

    source_images: dict[str, bytes | None] = {}
    with PackReader(Path(conversion.source)) as reader:
        for item in conversion.items:
            if item.texture not in source_images:
                source_images[item.texture] = (
                    reader.read(item.texture_source)
                    if copy_textures and item.texture_source
                    else None
                )
        for entity in conversion.entities:
            if entity.texture not in source_images:
                source_images[entity.texture] = (
                    reader.read(entity.texture_source)
                    if copy_textures and entity.texture_source
                    else None
                )
        for block in conversion.blocks:
            if block.texture not in source_images:
                source_images[block.texture] = (
                    reader.read(block.texture_source)
                    if copy_textures and block.texture_source
                    else None
                )

    copied = 0
    for name, image in source_images.items():
        if image is None:
            image = placeholder_png(name)
        else:
            copied += 1
        (texture_dir / f"{name}.png").write_bytes(image)

    model_dir = output / "models"
    if conversion.models or conversion.blockymodels:
        model_dir.mkdir(parents=True, exist_ok=True)
        for name, obj in conversion.models.items():
            (model_dir / f"{name}.obj").write_text(obj, encoding="utf-8")
        for name, document in conversion.blockymodels.items():
            (model_dir / f"{name}.blockymodel").write_text(
                json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

    source_path = output / "mod.lua"
    source_path.write_text(render_lua(conversion), encoding="utf-8")

    conversion.textures = {
        "written": len(source_images),
        "copied_from_pack": copied,
        "placeholder": len(source_images) - copied,
    }
    report = conversion.as_dict()
    report_path = output / "conversion.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {
        "source": source_path,
        "textures": texture_dir,
        "models": model_dir,
        "report": report_path,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def convert(
    path: Path,
    output: Path,
    *,
    namespace: str | None = None,
    pack_name: str | None = None,
    copy_textures: bool = False,
) -> tuple[Conversion, dict[str, Path]]:
    """Convert and write in one call, for the validation runner."""
    conversion = convert_pack(
        path,
        namespace=namespace,
        pack_name=pack_name,
        copy_textures=copy_textures,
    )
    written = write_project(conversion, output, copy_textures=copy_textures)
    return conversion, written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", type=Path, help=".mcaddon, .zip or pack folder")
    parser.add_argument("--out", type=Path, required=True, help="output folder")
    parser.add_argument("--namespace", help="override the inferred namespace")
    parser.add_argument("--name", help="display name for the converted pack")
    parser.add_argument(
        "--copy-textures",
        action="store_true",
        help="copy textures from the source pack instead of placeholders",
    )
    args = parser.parse_args(argv)

    conversion, written = convert(
        args.pack,
        args.out,
        namespace=args.namespace,
        pack_name=args.name,
        copy_textures=args.copy_textures,
    )
    print(f"source     : {conversion.source}")
    print(f"namespace  : {conversion.namespace}")
    for key, value in conversion.counts.items():
        print(f"{key:<12}: {value}")
    for family, count in conversion.unsupported.items():
        if count:
            print(f"not ported : {family} = {count}")
    print(f"lua        : {written['source']}")
    print(f"report     : {written['report']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
