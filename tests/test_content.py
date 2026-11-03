"""Tests for the declarative content: items, blocks, recipes and loot."""

from __future__ import annotations

import json

from luacraft.api import Edition
from luacraft.content import read_content
from luacraft.diagnostics import Category, DiagnosticBag
from luacraft.luaparse import parse
from luacraft.pipeline import build


def _content(source: str, namespace: str = "test"):
    bag = DiagnosticBag()
    chunk = parse(source)
    return read_content(chunk.body, namespace, bag), bag


# -- Reading --------------------------------------------------------------


def test_reads_an_item() -> None:
    content, _ = _content(
        """
        items.ruby = {
          id = "test:ruby",
          max_stack = 1,
          texture = "ruby",
        }
        """
    )
    assert "ruby" in content.items
    item = content.items["ruby"]
    assert item.identifier == "test:ruby"
    assert item.max_stack == 1
    assert item.texture == "ruby"


def test_qualifies_a_bare_identifier() -> None:
    content, _ = _content('items.ruby = { id = "ruby" }')
    assert content.items["ruby"].identifier == "test:ruby"


def test_defaults_the_identifier_to_the_entry_name() -> None:
    content, _ = _content("items.ruby = { }")
    assert content.items["ruby"].identifier == "test:ruby"


def test_reads_a_recipe() -> None:
    content, _ = _content(
        """
        recipes.sword = {
          type = "shaped",
          pattern = { "R", "R", "S" },
          key = { R = "test:ruby", S = "minecraft:stick" },
          result = "test:sword",
        }
        """
    )
    recipe = content.recipes["sword"]
    assert recipe.type == "shaped"
    assert recipe.pattern == ["R", "R", "S"]
    assert recipe.key["R"] == "test:ruby"
    assert recipe.result == "test:sword"


def test_reads_a_shapeless_recipe() -> None:
    content, _ = _content(
        """
        recipes.mix = {
          type = "shapeless",
          ingredients = { "minecraft:dirt", "minecraft:sand" },
          result = "test:mixed",
        }
        """
    )
    recipe = content.recipes["mix"]
    assert recipe.type == "shapeless"
    assert recipe.ingredients == ["minecraft:dirt", "minecraft:sand"]


def test_reads_a_block() -> None:
    content, _ = _content(
        """
        blocks.lamp = {
          hardness = 2.0,
          light_emission = 15,
          texture = "lamp",
        }
        """
    )
    block = content.blocks["lamp"]
    assert block.hardness == 2.0
    assert block.light_emission == 15


def test_ignores_non_content_tables() -> None:
    content, _ = _content("somethingElse.ruby = { }")
    assert content.summary() == {
        "items": 0, "blocks": 0, "recipes": 0, "loot": 0, "entities": 0
    }


def test_warns_about_an_unknown_recipe_type() -> None:
    _, bag = _content(
        """
        recipes.weird = {
          type = "telepathy",
          result = "test:x",
        }
        """
    )
    assert len(bag) == 1
    assert bag.warnings[0].category is Category.PARTIAL_SUPPORT


def test_warns_about_a_recipe_without_result() -> None:
    _, bag = _content(
        """
        recipes.broken = {
          type = "shaped",
          pattern = { "R" },
          key = { R = "minecraft:stone" },
        }
        """
    )
    assert len(bag) == 1
    assert "no result" in bag.warnings[0].message


def test_warns_about_an_unknown_category() -> None:
    _, bag = _content('items.ruby = { category = "sparkly" }')
    assert len(bag) == 1
    assert "creative category" in bag.warnings[0].message


def test_drops_a_shaped_recipe_without_pattern() -> None:
    content, bag = _content(
        """
        recipes.broken = {
          type = "shaped",
          key = { R = "minecraft:stone" },
          result = "test:x",
        }
        """
    )
    assert "broken" not in content.recipes
    assert len(bag) == 1


# -- Bedrock rendering ---------------------------------------------------


def test_bedrock_writes_an_item_definition() -> None:
    result = build(
        'items.ruby = { id = "test:ruby", max_stack = 1, texture = "ruby" }',
        Edition.BEDROCK,
        namespace="test",
    )
    assert "bp/items/ruby.json" in result.files
    data = json.loads(result.files["bp/items/ruby.json"])
    assert data["minecraft:item"]["description"]["identifier"] == "test:ruby"
    assert data["minecraft:item"]["components"]["minecraft:max_stack_size"] == 1


def test_bedrock_writes_a_shaped_recipe() -> None:
    result = build(
        """
        recipes.sword = {
          type = "shaped",
          pattern = { "R", "R", "S" },
          key = { R = "test:ruby", S = "minecraft:stick" },
          result = "test:sword",
        }
        """,
        Edition.BEDROCK,
        namespace="test",
    )
    assert "bp/recipes/sword.json" in result.files
    data = json.loads(result.files["bp/recipes/sword.json"])
    body = data["minecraft:recipe_shaped"]
    assert body["pattern"] == ["R", "R", "S"]
    assert body["key"]["R"]["item"] == "test:ruby"
    assert body["result"]["item"] == "test:sword"


def test_bedrock_writes_a_shapeless_recipe() -> None:
    result = build(
        """
        recipes.mix = {
          type = "shapeless",
          ingredients = { "minecraft:dirt" },
          result = "test:mixed",
        }
        """,
        Edition.BEDROCK,
        namespace="test",
    )
    data = json.loads(result.files["bp/recipes/mix.json"])
    assert "minecraft:recipe_shapeless" in data
    assert data["minecraft:recipe_shapeless"]["ingredients"][0]["item"] == "minecraft:dirt"


def test_bedrock_writes_a_block_definition() -> None:
    result = build(
        'blocks.lamp = { hardness = 2.0, light_emission = 15, texture = "lamp" }',
        Edition.BEDROCK,
        namespace="test",
    )
    data = json.loads(result.files["bp/blocks/lamp.json"])
    components = data["minecraft:block"]["components"]
    assert components["minecraft:light_emission"] == 15
    assert components["minecraft:destroy_time"] == 2.0


def test_bedrock_keeps_extra_components() -> None:
    result = build(
        'items.ruby = { components = { glint = true } }',
        Edition.BEDROCK,
        namespace="test",
    )
    data = json.loads(result.files["bp/items/ruby.json"])
    assert data["minecraft:item"]["components"]["minecraft:glint"] is True


# -- Java rendering -------------------------------------------------------


def test_java_writes_a_shaped_recipe() -> None:
    result = build(
        """
        recipes.sword = {
          type = "shaped",
          pattern = { "R", "R", "S" },
          key = { R = "test:ruby", S = "minecraft:stick" },
          result = "test:sword",
        }
        """,
        Edition.JAVA,
        namespace="test",
    )
    assert "data/test/recipe/sword.json" in result.files
    data = json.loads(result.files["data/test/recipe/sword.json"])
    assert data["type"] == "minecraft:crafting_shaped"
    assert data["result"]["id"] == "test:sword"


def test_java_writes_a_shapeless_recipe() -> None:
    result = build(
        """
        recipes.mix = {
          type = "shapeless",
          ingredients = { "minecraft:dirt" },
          result = "test:mixed",
        }
        """,
        Edition.JAVA,
        namespace="test",
    )
    data = json.loads(result.files["data/test/recipe/mix.json"])
    assert data["type"] == "minecraft:crafting_shapeless"


def test_java_warns_that_custom_items_need_a_resource_pack() -> None:
    result = build(
        'items.ruby = { id = "test:ruby", texture = "ruby" }',
        Edition.JAVA,
        namespace="test",
    )
    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert warning.category is Category.PARTIAL_SUPPORT
    assert "no item definitions in a datapack" in warning.message
    assert "resource pack" in warning.message


def test_java_warns_that_custom_blocks_need_more_work() -> None:
    result = build(
        'blocks.lamp = { hardness = 2.0 }',
        Edition.JAVA,
        namespace="test",
    )
    assert len(result.warnings) == 1
    assert "no block definitions in a datapack" in result.warnings[0].message


def test_bedrock_does_not_warn_about_custom_items() -> None:
    result = build(
        'items.ruby = { id = "test:ruby", texture = "ruby" }',
        Edition.BEDROCK,
        namespace="test",
    )
    assert len(result.warnings) == 0


def test_the_content_summary_is_reported() -> None:
    result = build(
        """
        items.ruby = { }
        recipes.sword = { type = "shapeless", ingredients = {}, result = "test:s" }
        """,
        Edition.BEDROCK,
        namespace="test",
    )
    assert result.content.summary()["items"] == 1
    assert result.content.summary()["recipes"] == 1
