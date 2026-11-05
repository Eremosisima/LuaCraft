"""Tests for the Luanti backend."""

from __future__ import annotations

import json
import re
from pathlib import Path

from luacraft.api import Edition
from luacraft.luaparse import LuaSyntaxError, parse
from luacraft.pipeline import build, write_luanti_mod


def _luanti(source: str, namespace: str = "test"):
    return build(source, Edition.LUANTI, namespace=namespace)


def _script(source: str) -> str:
    result = _luanti(source)
    return result.files["init.lua"]


def _parses(lua: str) -> bool:
    """Whether the generated Lua is syntactically valid."""
    try:
        parse(lua)
    except LuaSyntaxError:
        return False
    return True


# -- Layout ---------------------------------------------------------------


def test_luanti_output_has_an_init_lua() -> None:
    result = _luanti('say("hola")')
    assert "init.lua" in result.files


def test_luanti_output_has_a_mod_conf() -> None:
    result = _luanti('say("hola")', namespace="mymod")
    conf = result.files["mod.conf"]
    assert "name = mymod" in conf
    assert "description =" in conf


def test_the_mod_conf_has_the_mod_name() -> None:
    result = _luanti('say("hola")', namespace="mymod")
    assert result.files["mod.conf"].startswith("name = mymod")


# -- Syntax ---------------------------------------------------------------


def test_the_generated_lua_parses() -> None:
    script = _script(
        """
        onTick(function()
          forEachPlayer(function(player)
            score(player, "coins", "add", 1)
            if score(player, "coins") >= 100 then
              give(player, "test:ruby", 1)
              message(player, "ganaste")
            end
          end)
        end)
        """
    )
    assert _parses(script)


def test_the_generated_lua_with_content_parses() -> None:
    result = _luanti(
        """
        items.ruby = { id = "test:ruby", max_stack = 1, texture = "ruby" }
        blocks.lamp = { hardness = 2.0, texture = "lamp" }
        recipes.sword = {
          type = "shaped",
          pattern = { "R", "R", "S" },
          key = { R = "test:ruby", S = "minecraft:stick" },
          result = "test:sword",
        }
        onTick(function()
          say("hola")
        end)
        """
    )
    assert _parses(result.files["init.lua"])


# -- Primitives -----------------------------------------------------------


def test_say_becomes_chat_send_all() -> None:
    assert 'S.chat_send_all("hola")' in _script('say("hola")')


def test_message_becomes_chat_send_player() -> None:
    script = _script('message(player, "hola")')
    assert 'S.chat_send_player("player", "hola")' in script


def test_give_adds_to_the_inventory() -> None:
    script = _script('give(player, "test:ruby", 2)')
    assert "get_inventory():add_item" in script
    assert 'ItemStack("test:ruby" .. " " .. 2)' in script


def test_the_tick_hook_uses_the_global_step() -> None:
    assert "S.register_globalstep(" in _script('onTick(function() say("a") end)')


def test_the_join_hook_uses_join_player() -> None:
    assert "S.register_on_joinplayer(" in _script(
        'onJoin(function() say("a") end)'
    )


def test_iterating_players_uses_connected_players() -> None:
    script = _script(
        'onTick(function() forEachPlayer(function(p) say("a") end) end)'
    )
    assert "S.get_connected_players()" in script
    assert "for _, player in ipairs(" in script


def test_the_score_is_kept_in_the_player_metadata() -> None:
    script = _script('score(player, "coins", "add", 1)')
    assert 'get_meta():set_int("coins"' in script
    assert 'get_meta():get_int("coins")' in script


def test_setting_a_block_uses_set_node() -> None:
    script = _script('setblock(0, 64, 0, "default:stone")')
    assert "S.set_node(vector.new(0, 64, 0)" in script
    assert '"default:stone"' in script


def test_reading_a_block_uses_get_node() -> None:
    script = _script("getBlock(0, 64, 0)")
    assert "S.get_node(vector.new(0, 64, 0)).name" in script


def test_teleporting_uses_set_pos() -> None:
    script = _script("teleport(player, 1, 2, 3)")
    assert 'S.get_player_by_name("player"):set_pos(vector.new(1, 2, 3))' in script


def test_the_condition_uses_the_metadata() -> None:
    script = _script(
        """
        onTick(function()
          forEachPlayer(function(p)
            if score(p, "coins") >= 100 then
              say("muchas monedas")
            end
          end)
        end)
        """
    )
    assert 'get_meta():get_int("coins") >= 100' in script


def test_the_add_objective_call_is_dropped() -> None:
    script = _script('addObjective("coins", "dummy")')
    assert "addObjective" not in script


# -- Content --------------------------------------------------------------


def test_an_item_becomes_a_craft_item() -> None:
    result = _luanti('items.ruby = { id = "test:ruby", max_stack = 1 }')
    script = result.files["init.lua"]
    assert 'S.register_craftitem("test:ruby"' in script
    assert "stack_max = 1" in script


def test_a_block_becomes_a_node() -> None:
    result = _luanti(
        'blocks.lamp = { id = "test:lamp", hardness = 2.0, texture = "lamp" }'
    )
    script = result.files["init.lua"]
    assert 'S.register_node("test:lamp"' in script
    assert "hardness = 2.0" in script


def test_items_and_nodes_only_call_real_luanti_apis() -> None:
    """Luanti has no add_to_creative_inventory; every registered item shows up."""
    result = _luanti(
        """
        items.ruby = { id = "test:ruby" }
        blocks.lamp = { id = "test:lamp", texture = "lamp" }
        """
    )
    script = result.files["init.lua"]
    assert "add_to_creative_inventory" not in script

    allowed = {
        "register_craftitem",
        "register_node",
        "register_craft",
        "register_entity",
        "register_abm",
        "register_chatcommand",
        "register_globalstep",
        "register_on_joinplayer",
        "add_entity",
        "add_item",
        "chat_send_all",
        "chat_send_player",
        "get_connected_players",
        "get_node",
        "get_objects_inside_radius",
        "get_player_by_name",
        "set_node",
    }
    called = set(re.findall(r"\bS\.([a-z_]+)\(", script))
    assert called <= allowed, f"unknown Luanti API: {called - allowed}"


def test_a_recipe_becomes_a_craft() -> None:
    result = _luanti(
        """
        recipes.sword = {
          type = "shaped",
          pattern = { "R", "R", "S" },
          key = { R = "test:ruby", S = "minecraft:stick" },
          result = "test:sword",
        }
        """
    )
    script = result.files["init.lua"]
    assert "S.register_craft({" in script
    assert 'type = "shaped"' in script
    assert 'output = "test:sword"' in script


def test_the_pattern_becomes_a_matrix() -> None:
    result = _luanti(
        """
        recipes.sword = {
          type = "shaped",
          pattern = { "R", "R", "S" },
          key = { R = "test:ruby", S = "minecraft:stick" },
          result = "test:sword",
        }
        """
    )
    script = result.files["init.lua"]
    assert '{ "test:ruby" }' in script
    assert '{ "minecraft:stick" }' in script


def test_a_shapeless_recipe_lists_the_ingredients() -> None:
    result = _luanti(
        """
        recipes.mix = {
          type = "shapeless",
          ingredients = { "minecraft:dirt", "minecraft:sand" },
          result = "test:mixed",
        }
        """
    )
    script = result.files["init.lua"]
    assert 'type = "shapeless"' in script
    assert '"minecraft:dirt", "minecraft:sand"' in script


def test_the_recipe_count_is_appended_to_the_output() -> None:
    result = _luanti(
        """
        recipes.mix = {
          type = "shapeless",
          ingredients = { "minecraft:dirt" },
          result = "test:mixed",
          count = 3,
        }
        """
    )
    assert 'output = "test:mixed 3"' in result.files["init.lua"]


def test_the_texture_folder_gets_a_note() -> None:
    result = _luanti('items.ruby = { id = "test:ruby", texture = "ruby" }')
    assert "textures/README.txt" in result.files
    assert "ruby.png" in result.files["textures/README.txt"]


# -- Identifiers ----------------------------------------------------------


def test_identifiers_keep_the_mod_namespace() -> None:
    result = _luanti(
        'items.ruby = { id = "mymod:ruby" }', namespace="mymod"
    )
    assert 'S.register_craftitem("mymod:ruby"' in result.files["init.lua"]


def test_a_foreign_namespace_is_rewritten() -> None:
    result = _luanti(
        'items.ruby = { id = "other:ruby" }', namespace="mymod"
    )
    assert 'S.register_craftitem("mymod:ruby"' in result.files["init.lua"]


def test_the_bare_namespace_is_added() -> None:
    result = _luanti('items.ruby = { id = "ruby" }', namespace="mymod")
    assert 'S.register_craftitem("mymod:ruby"' in result.files["init.lua"]


# -- Diagnostics ----------------------------------------------------------


def test_luanti_reports_that_effects_are_missing() -> None:
    result = _luanti('effect(player, "speed", 30)')
    assert len(result.errors) == 1
    assert "not available in Luanti" in result.errors[0].message


def test_luanti_reports_that_scores_are_partial() -> None:
    result = _luanti('score(player, "coins", "add", 1)')
    assert len(result.warnings) == 1
    assert "partially supported in Luanti" in result.warnings[0].message


def test_the_luanti_hook_is_silent_in_luanti() -> None:
    result = _luanti('onLuantiOnly(function() say("a") end)')
    assert len(result.diagnostics) == 0


def test_the_luanti_hook_warns_in_java() -> None:
    result = build(
        'onLuantiOnly(function() say("a") end)', Edition.JAVA, namespace="test"
    )
    assert len(result.warnings) == 1
    assert "Luanti only hook" in result.warnings[0].message


def test_the_luanti_hook_warns_in_bedrock() -> None:
    result = build(
        'onLuantiOnly(function() say("a") end)', Edition.BEDROCK, namespace="test"
    )
    assert len(result.warnings) == 1


def test_the_luanti_hook_body_is_kept_in_luanti() -> None:
    script = _script('onLuantiOnly(function() say("solo luanti") end)')
    assert "solo luanti" in script


def test_the_luanti_hook_body_is_dropped_in_java() -> None:
    result = build(
        'onLuantiOnly(function() say("solo luanti") end)',
        Edition.JAVA,
        namespace="test",
    )
    joined = "\n".join(result.files.values())
    assert "solo luanti" not in joined


# -- Packaging ------------------------------------------------------------


def test_writing_a_mod_produces_a_zip(tmp_path: Path) -> None:
    result = _luanti('say("hola")', namespace="mymod")
    output = write_luanti_mod(result, tmp_path / "mod.zip", namespace="mymod")
    import zipfile

    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
    assert "mymod/init.lua" in names
    assert "mymod/mod.conf" in names


def test_an_entity_mesh_uses_the_bare_media_name() -> None:
    """Luanti announces media by file name, not by its models/ path."""
    result = _luanti(
        """
        entities.benogrok = {
          id = "faunalibre:benogrok",
          texture = "benogrok",
          model = "benogrok_model",
          health = 17,
          collision_box = {width = 1.8, height = 0.69},
        }
        """
    )
    script = result.files["init.lua"]
    assert 'mesh = "benogrok_model.obj"' in script
    assert "models/benogrok_model.obj" not in script
    assert "models/benogrok_model.obj" in result.files
