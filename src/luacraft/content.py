"""Declarative content: items, blocks, recipes and loot tables.

The author declares content with plain Lua tables::

    items.ruby = {
      id = "luacraft:ruby",
      max_stack = 1,
      texture = "ruby",
    }

    recipes.ruby_sword = {
      type = "shaped",
      pattern = { "R", "R", "S" },
      key = { R = "luacraft:ruby", S = "minecraft:stick" },
      result = "luacraft:ruby_sword",
    }

This module turns those tables into an intermediate representation, and the
two backends render it into the JSON each edition expects.

The two editions disagree about custom items more than anything else, so the
representation keeps track of what each side can do:

* Bedrock has item definitions in the behaviour pack, so an item can declare
  components, durability, food, and so on.
* Java Edition has no item definitions in a datapack.  A custom item there is
  a vanilla item plus a resource pack model, or a datapack item with only
  components.  The texture is the part that cannot be expressed without
  shipping a resource pack.

Everything that cannot be represented in one edition is reported through the
diagnostics of the build, never silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .diagnostics import Category, DiagnosticBag
from .luaparse import (
    Assign,
    Index,
    Name,
    Node,
    Number,
    String,
    Table,
    TrueLit,
    FalseLit,
)


#: The content tables the author may declare.
CONTENT_TABLES = frozenset({"items", "blocks", "recipes", "loot"})

#: Valid values for the creative menu category.
MENU_CATEGORIES = frozenset(
    {"construction", "equipment", "items", "nature", "none"}
)


# --------------------------------------------------------------------------
# Intermediate representation
# --------------------------------------------------------------------------


@dataclass
class ItemDef:
    """A custom item, in a form both editions can work from."""

    name: str
    identifier: str
    line: int
    max_stack: int | None = None
    durability: int | None = None
    texture: str | None = None
    display_name: str | None = None
    category: str = "items"
    food: dict[str, Any] | None = None
    fuel: dict[str, Any] | None = None
    glint: bool | None = None
    hand_equipped: bool | None = None
    #: Extra components, kept verbatim for the Bedrock backend.
    components: dict[str, Any] = field(default_factory=dict)
    #: The base vanilla item Java Edition falls back to.
    base_item: str = "minecraft:stick"


@dataclass
class BlockDef:
    """A custom block."""

    name: str
    identifier: str
    line: int
    hardness: float | None = None
    resistance: float | None = None
    light_emission: int | None = None
    friction: float | None = None
    map_color: str | None = None
    texture: str | None = None
    category: str = "construction"
    #: Block states, name -> list of values.
    states: dict[str, list[Any]] = field(default_factory=dict)
    components: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecipeDef:
    """A crafting recipe."""

    name: str
    identifier: str
    line: int
    type: str = "shaped"
    pattern: list[str] = field(default_factory=list)
    key: dict[str, str] = field(default_factory=dict)
    ingredients: list[str] = field(default_factory=list)
    result: str = ""
    count: int = 1
    group: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class LootDef:
    """A loot table."""

    name: str
    identifier: str
    line: int
    pools: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Content:
    """Everything the source declared."""

    items: dict[str, ItemDef] = field(default_factory=dict)
    blocks: dict[str, BlockDef] = field(default_factory=dict)
    recipes: dict[str, RecipeDef] = field(default_factory=dict)
    loot: dict[str, LootDef] = field(default_factory=dict)

    def summary(self) -> dict[str, int]:
        return {
            "items": len(self.items),
            "blocks": len(self.blocks),
            "recipes": len(self.recipes),
            "loot": len(self.loot),
        }


# --------------------------------------------------------------------------
# Reading the Lua tables
# --------------------------------------------------------------------------


def _value(node: Node) -> Any:
    """Convert a Lua literal into a plain Python value."""
    if isinstance(node, String):
        return node.value
    if isinstance(node, Number):
        return node.value
    if isinstance(node, TrueLit):
        return True
    if isinstance(node, FalseLit):
        return False
    if isinstance(node, Name):
        if node.identifier == "nil":
            return None
        return node.identifier
    if isinstance(node, Table):
        result: dict[str, Any] = {}
        for key_node, value_node in node.hash_part:
            key = _value(key_node)
            result[str(key)] = _value(value_node)
        if node.array_part:
            array = [_value(item) for item in node.array_part]
            if result:
                result["__array__"] = array
            else:
                return array
        return result
    return None


def _table_fields(node: Node) -> tuple[dict[str, Any], list[Any]]:
    """Split a table into its named fields and its positional values."""
    fields: dict[str, Any] = {}
    array: list[Any] = []
    if not isinstance(node, Table):
        return fields, array
    for key_node, value_node in node.hash_part:
        fields[str(_value(key_node))] = _value(value_node)
    for item in node.array_part:
        array.append(_value(item))
    return fields, array


def _qualified(namespace: str, value: str) -> str:
    """Add the pack namespace to an identifier that has none."""
    if ":" in value:
        return value
    return f"{namespace}:{value}"


def read_content(
    statements: list[Node],
    namespace: str,
    diagnostics: DiagnosticBag | None = None,
) -> Content:
    """Extract the declared content from the top level statements."""
    diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()
    content = Content()

    for statement in statements:
        if not isinstance(statement, Assign):
            continue
        target = statement.targets[0]
        if not isinstance(target, Index):
            continue
        if not isinstance(target.obj, Name):
            continue
        table_name = target.obj.identifier
        if table_name not in CONTENT_TABLES:
            continue
        if not isinstance(target.key, String):
            continue
        entry_name = target.key.value
        value = statement.values[0] if statement.values else None
        if value is None:
            continue
        fields, array = _table_fields(value)
        line = statement.line

        if table_name == "items":
            item = _read_item(entry_name, fields, namespace, line, diagnostics)
            if item:
                content.items[entry_name] = item
        elif table_name == "blocks":
            block = _read_block(entry_name, fields, namespace, line, diagnostics)
            if block:
                content.blocks[entry_name] = block
        elif table_name == "recipes":
            recipe = _read_recipe(entry_name, fields, array, namespace, line, diagnostics)
            if recipe:
                content.recipes[entry_name] = recipe
        elif table_name == "loot":
            loot = _read_loot(entry_name, fields, namespace, line, diagnostics)
            if loot:
                content.loot[entry_name] = loot

    return content


def _read_item(
    name: str,
    fields: dict[str, Any],
    namespace: str,
    line: int,
    diagnostics: DiagnosticBag,
) -> ItemDef | None:
    identifier = fields.get("id")
    if not identifier:
        identifier = f"{namespace}:{name}"
    identifier = _qualified(namespace, str(identifier))

    category = str(fields.get("category", "items"))
    if category not in MENU_CATEGORIES:
        diagnostics.warn(
            Category.PARTIAL_SUPPORT,
            f"unknown creative category {category!r}, using 'items' instead",
            line,
            symbol="category",
            hint=f"valid categories are {', '.join(sorted(MENU_CATEGORIES))}",
        )
        category = "items"

    components = fields.get("components")
    if not isinstance(components, dict):
        components = {}

    item = ItemDef(
        name=name,
        identifier=identifier,
        line=line,
        max_stack=_as_int(fields.get("max_stack")),
        durability=_as_int(fields.get("durability")),
        texture=_as_str(fields.get("texture")),
        display_name=_as_str(fields.get("display_name")),
        category=category,
        food=fields.get("food") if isinstance(fields.get("food"), dict) else None,
        fuel=fields.get("fuel") if isinstance(fields.get("fuel"), dict) else None,
        glint=_as_bool(fields.get("glint")),
        hand_equipped=_as_bool(fields.get("hand_equipped")),
        components=components,
        base_item=_qualified(namespace, str(fields.get("base", "minecraft:stick"))),
    )
    return item


def _read_block(
    name: str,
    fields: dict[str, Any],
    namespace: str,
    line: int,
    diagnostics: DiagnosticBag,
) -> BlockDef | None:
    identifier = fields.get("id")
    if not identifier:
        identifier = f"{namespace}:{name}"
    identifier = _qualified(namespace, str(identifier))

    states = fields.get("states")
    if not isinstance(states, dict):
        states = {}

    return BlockDef(
        name=name,
        identifier=identifier,
        line=line,
        hardness=_as_float(fields.get("hardness")),
        resistance=_as_float(fields.get("resistance")),
        light_emission=_as_int(fields.get("light_emission")),
        friction=_as_float(fields.get("friction")),
        map_color=_as_str(fields.get("map_color")),
        texture=_as_str(fields.get("texture")),
        category=str(fields.get("category", "construction")),
        states=states,
        components=(
            fields.get("components") if isinstance(fields.get("components"), dict) else {}
        ),
    )


def _read_recipe(
    name: str,
    fields: dict[str, Any],
    array: list[Any],
    namespace: str,
    line: int,
    diagnostics: DiagnosticBag,
) -> RecipeDef | None:
    identifier = fields.get("id")
    if not identifier:
        identifier = f"{namespace}:{name}"
    identifier = _qualified(namespace, str(identifier))

    recipe_type = str(fields.get("type", "shaped")).lower()
    if recipe_type not in ("shaped", "shapeless"):
        diagnostics.warn(
            Category.PARTIAL_SUPPORT,
            f"unknown recipe type {recipe_type!r}, treating it as 'shapeless'",
            line,
            symbol="type",
            hint="supported recipe types are 'shaped' and 'shapeless'",
        )
        recipe_type = "shapeless"

    pattern = fields.get("pattern")
    if not isinstance(pattern, list):
        pattern = []
    pattern = [str(row) for row in pattern]

    key = fields.get("key")
    if not isinstance(key, dict):
        key = {}
    key = {
        str(k): _qualified(namespace, str(v)) for k, v in key.items()
    }

    ingredients = fields.get("ingredients")
    if not isinstance(ingredients, list):
        ingredients = []
    ingredients = [_qualified(namespace, str(i)) for i in ingredients]

    result = fields.get("result")
    if not result:
        diagnostics.warn(
            Category.PARTIAL_SUPPORT,
            "recipe has no result, so it will not be usable",
            line,
            symbol="result",
            hint="set result to the item the recipe produces",
        )
        return None
    result = _qualified(namespace, str(result))

    if recipe_type == "shaped" and not pattern:
        diagnostics.warn(
            Category.PARTIAL_SUPPORT,
            "shaped recipe has no pattern, so it will not be usable",
            line,
            symbol="pattern",
            hint="set pattern to the rows of the crafting grid",
        )
        return None

    tags = fields.get("tags")
    if not isinstance(tags, list):
        tags = []

    return RecipeDef(
        name=name,
        identifier=identifier,
        line=line,
        type=recipe_type,
        pattern=pattern,
        key=key,
        ingredients=ingredients,
        result=result,
        count=_as_int(fields.get("count")) or 1,
        group=_as_str(fields.get("group")),
        tags=[str(t) for t in tags],
    )


def _read_loot(
    name: str,
    fields: dict[str, Any],
    namespace: str,
    line: int,
    diagnostics: DiagnosticBag,
) -> LootDef | None:
    identifier = fields.get("id")
    if not identifier:
        identifier = f"{namespace}:{name}"
    identifier = _qualified(namespace, str(identifier))
    pools = fields.get("pools")
    if not isinstance(pools, list):
        pools = []
    return LootDef(
        name=name,
        identifier=identifier,
        line=line,
        pools=[p for p in pools if isinstance(p, dict)],
    )


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None
