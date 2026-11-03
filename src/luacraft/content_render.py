"""Rendering the content declarations into each edition's JSON.

Bedrock and Java disagree about custom items, so the two renderers here are
not symmetric:

* :func:`render_bedrock_items` writes full item definitions into the
  behaviour pack, which is where Bedrock expects them.
* :func:`render_java_items` writes the datapack side of an item.  Java has
  no item definitions in a datapack, so the behaviour comes from the
  components of a base vanilla item and the appearance needs a resource pack.
  The renderer reports that instead of pretending it did the work.
"""

from __future__ import annotations

import json
from typing import Any

from .content import BlockDef, Content, ItemDef, RecipeDef
from .diagnostics import Category, DiagnosticBag


#: The Bedrock format version the generated files declare.
BEDROCK_FORMAT_VERSION = "1.21.90"

#: The Java pack format the generated files target.
JAVA_PACK_FORMAT = 48


def _dump(data: Any) -> str:
    """Render JSON the way Minecraft files are usually written."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------
# Bedrock
# --------------------------------------------------------------------------


def render_bedrock_item(item: ItemDef) -> dict[str, Any]:
    """Render one item as a Bedrock item definition."""
    description: dict[str, Any] = {
        "identifier": item.identifier,
        "menu_category": {"category": item.category},
    }
    if item.display_name:
        description["menu_category"]["group"] = f"itemGroup.name.{item.name}"

    components: dict[str, Any] = {}
    if item.max_stack is not None:
        components["minecraft:max_stack_size"] = item.max_stack
    if item.durability is not None:
        components["minecraft:durability"] = {"max_durability": item.durability}
    if item.texture:
        components["minecraft:icon"] = {"texture": item.texture}
    if item.display_name:
        components["minecraft:display_name"] = {"value": item.display_name}
    if item.glint is not None:
        components["minecraft:glint"] = item.glint
    if item.hand_equipped is not None:
        components["minecraft:hand_equipped"] = item.hand_equipped
    if item.food:
        food = dict(item.food)
        if "nutrition" in food:
            food["nutrition"] = int(food["nutrition"])
        components["minecraft:food"] = food
    if item.fuel:
        fuel = dict(item.fuel)
        if "duration" in fuel:
            fuel["duration"] = float(fuel["duration"])
        components["minecraft:fuel"] = fuel
    for key, value in item.components.items():
        components[f"minecraft:{key}" if ":" not in key else key] = value

    return {
        "format_version": BEDROCK_FORMAT_VERSION,
        "minecraft:item": {
            "description": description,
            "components": components,
        },
    }


def render_bedrock_block(block: BlockDef) -> dict[str, Any]:
    """Render one block as a Bedrock block definition."""
    description: dict[str, Any] = {
        "identifier": block.identifier,
        "menu_category": {"category": block.category},
    }
    if block.states:
        description["states"] = {
            name: values for name, values in block.states.items()
        }

    components: dict[str, Any] = {}
    if block.hardness is not None:
        components["minecraft:destroy_time"] = block.hardness
    if block.resistance is not None:
        components["minecraft:blast_resistance"] = block.resistance
    if block.light_emission is not None:
        components["minecraft:light_emission"] = block.light_emission
    if block.friction is not None:
        components["minecraft:friction"] = block.friction
    if block.map_color:
        components["minecraft:map_color"] = block.map_color
    if block.texture:
        components["minecraft:material_instances"] = {
            "*": {"texture": block.texture}
        }
    for key, value in block.components.items():
        components[f"minecraft:{key}" if ":" not in key else key] = value

    return {
        "format_version": BEDROCK_FORMAT_VERSION,
        "minecraft:block": {
            "description": description,
            "components": components,
        },
    }


def render_bedrock_recipe(recipe: RecipeDef) -> dict[str, Any]:
    """Render one recipe as a Bedrock recipe definition."""
    if recipe.type == "shaped":
        key: dict[str, Any] = {}
        for character, item in recipe.key.items():
            entry: dict[str, Any] = {"item": item}
            key[character] = entry
        body: dict[str, Any] = {
            "description": {"identifier": recipe.identifier},
            "pattern": recipe.pattern,
            "key": key,
            "result": {"item": recipe.result, "count": recipe.count},
        }
        if recipe.tags:
            body["tags"] = recipe.tags
        if recipe.group:
            body["group"] = recipe.group
        return {
            "format_version": BEDROCK_FORMAT_VERSION,
            "minecraft:recipe_shaped": body,
        }

    body = {
        "description": {"identifier": recipe.identifier},
        "ingredients": [
            {"item": item, "count": 1} for item in recipe.ingredients
        ],
        "result": {"item": recipe.result, "count": recipe.count},
    }
    if recipe.tags:
        body["tags"] = recipe.tags
    if recipe.group:
        body["group"] = recipe.group
    return {
        "format_version": BEDROCK_FORMAT_VERSION,
        "minecraft:recipe_shapeless": body,
    }


def render_bedrock_content(
    content: Content,
    diagnostics: DiagnosticBag,
) -> dict[str, str]:
    """Render every declared item, block and recipe for Bedrock."""
    files: dict[str, str] = {}
    for item in content.items.values():
        files[f"bp/items/{item.name}.json"] = _dump(render_bedrock_item(item))
    for block in content.blocks.values():
        files[f"bp/blocks/{block.name}.json"] = _dump(render_bedrock_block(block))
    for recipe in content.recipes.values():
        files[f"bp/recipes/{recipe.name}.json"] = _dump(render_bedrock_recipe(recipe))
    return files


# --------------------------------------------------------------------------
# Java
# --------------------------------------------------------------------------


def render_java_recipe(recipe: RecipeDef) -> dict[str, Any]:
    """Render one recipe as a Java recipe definition."""
    if recipe.type == "shaped":
        key = {
            character: {"item": item} for character, item in recipe.key.items()
        }
        body: dict[str, Any] = {
            "type": "minecraft:crafting_shaped",
            "pattern": recipe.pattern,
            "key": key,
            "result": {"id": recipe.result, "count": recipe.count},
        }
        if recipe.group:
            body["group"] = recipe.group
        return body

    body = {
        "type": "minecraft:crafting_shapeless",
        "ingredients": [{"id": item} for item in recipe.ingredients],
        "result": {"id": recipe.result, "count": recipe.count},
    }
    if recipe.group:
        body["group"] = recipe.group
    return body


def render_java_content(
    content: Content,
    diagnostics: DiagnosticBag,
    namespace: str = "luacraft",
    *,
    with_textures: bool = False,
) -> dict[str, str]:
    """Render every declared recipe for Java.

    Java Edition has no item or block definitions in a datapack.  A custom
    item there is a vanilla item with extra components, and its appearance
    needs a resource pack.  Both facts are reported so the author knows what
    the Java build is missing, unless the build already ships the generated
    resource pack.
    """
    files: dict[str, str] = {}

    for recipe in content.recipes.values():
        files[f"data/{namespace}/recipe/{recipe.name}.json"] = _dump(
            render_java_recipe(recipe)
        )

    if with_textures:
        return files

    for item in content.items.values():
        diagnostics.warn(
            Category.PARTIAL_SUPPORT,
            (
                f"Java Edition has no item definitions in a datapack, so the "
                f"custom item {item.identifier} needs a resource pack to have "
                f"its own texture and model"
            ),
            item.line,
            symbol=item.identifier,
            edition="java",
            hint=(
                f"use {item.base_item} as the base item and ship a resource "
                f"pack with a model and texture for it"
            ),
        )

    for block in content.blocks.values():
        diagnostics.warn(
            Category.PARTIAL_SUPPORT,
            (
                f"Java Edition has no block definitions in a datapack, so the "
                f"custom block {block.identifier} needs a resource pack and "
                f"cannot be placed by name without one"
            ),
            block.line,
            symbol=block.identifier,
            edition="java",
            hint=(
                "custom blocks in Java require a mod, or a resource pack plus "
                "a datapack that places the block through its block state"
            ),
        )

    return files
