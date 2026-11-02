"""Tests for the two backends and the build pipeline."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from luacraft.api import Edition
from luacraft.pipeline import build, write_mcaddon, write_pack


def _java(source: str):
    return build(source, Edition.JAVA, namespace="test")


def _bedrock(source: str):
    return build(source, Edition.BEDROCK, namespace="test")


# -- Java backend ---------------------------------------------------------


def test_java_output_has_a_pack_mcmeta() -> None:
    result = _java('say("hola")')
    assert "pack.mcmeta" in result.files
    meta = json.loads(result.files["pack.mcmeta"])
    assert "pack_format" in meta["pack"]


def test_java_output_has_a_main_function() -> None:
    result = _java('say("hola")')
    assert "data/test/functions/main.mcfunction" in result.files


def test_java_say_becomes_a_say_command() -> None:
    result = _java('say("hola")')
    main = result.files["data/test/functions/main.mcfunction"]
    assert 'say {"text":"hola"}' in main


def test_java_scoreboard_objectives_are_declared() -> None:
    result = _java('addObjective("coins", "dummy")')
    objectives = result.files["data/test/functions/_objectives.mcfunction"]
    assert "scoreboard objectives add coins dummy" in objectives


def test_java_objectives_are_loaded_with_the_load_tag() -> None:
    result = _java('addObjective("coins", "dummy")')
    tag = json.loads(result.files["data/test/tags/functions/load.json"])
    assert "test:_objectives" in tag["values"]


def test_java_tick_hook_creates_a_tick_tag() -> None:
    result = _java('onTick(function() say("a") end)')
    tag = json.loads(result.files["data/test/tags/functions/tick.json"])
    assert "test:_tick" in tag["values"]


def test_java_iteration_uses_the_as_selector() -> None:
    result = _java('onTick(function() forEachPlayer(function(p) say("a") end) end)')
    tick = result.files["data/test/functions/_tick.mcfunction"]
    assert "execute as @a run" in tick
    assert 'say {"text":"a"}' in tick


def test_java_multi_statement_callback_becomes_a_function() -> None:
    result = _java(
        """
        onTick(function()
          forEachPlayer(function(p)
            give(p, "minecraft:diamond", 1)
            say("obtuviste un diamante")
          end)
        end)
        """
    )
    loop_files = [
        name for name in result.files if "_loop" in name
    ]
    assert loop_files
    body = result.files[loop_files[0]]
    assert "give @s minecraft:diamond 1" in body
    assert 'say {"text":"obtuviste un diamante"}' in body


def test_java_condition_becomes_an_execute_if_score() -> None:
    result = _java(
        """
        onTick(function()
          forEachPlayer(function(p)
            score(p, "coins", "add", 1)
            if score(p, "coins") >= 100 then
              give(p, "minecraft:diamond", 1)
            end
          end)
        end)
        """
    )
    bodies = [
        content for name, content in result.files.items()
        if name.endswith(".mcfunction")
    ]
    joined = "\n".join(bodies)
    assert "execute if score @s coins matches 100.." in joined


def test_java_target_hook_is_emitted() -> None:
    result = _java('onJavaOnly(function() say("solo java") end)')
    joined = "\n".join(result.files.values())
    assert "solo java" in joined


def test_java_drops_the_bedrock_hook() -> None:
    result = _java('onBedrockOnly(function() say("solo bedrock") end)')
    joined = "\n".join(result.files.values())
    assert "solo bedrock" not in joined


# -- Bedrock backend ------------------------------------------------------


def test_bedrock_output_has_two_manifests() -> None:
    result = _bedrock('say("hola")')
    assert "bp/manifest.json" in result.files
    assert "rp/manifest.json" in result.files


def test_bedrock_manifests_are_valid_json() -> None:
    result = _bedrock('say("hola")')
    for path in ("bp/manifest.json", "rp/manifest.json"):
        manifest = json.loads(result.files[path])
        assert manifest["format_version"] == 2
        assert "uuid" in manifest["header"]


def test_bedrock_manifest_uuids_differ_between_packs() -> None:
    result = _bedrock('say("hola")')
    behaviour = json.loads(result.files["bp/manifest.json"])
    resource = json.loads(result.files["rp/manifest.json"])
    assert behaviour["header"]["uuid"] != resource["header"]["uuid"]


def test_bedrock_output_has_a_script() -> None:
    result = _bedrock('say("hola")')
    assert "bp/scripts/main.ts" in result.files


def test_bedrock_say_uses_world_send_message() -> None:
    result = _bedrock('say("hola")')
    script = result.files["bp/scripts/main.ts"]
    assert 'world.sendMessage("hola")' in script


def test_bedrock_imports_only_what_it_uses() -> None:
    result = _bedrock('say("hola")')
    script = result.files["bp/scripts/main.ts"]
    import_line = script.splitlines()[0]
    assert "world" in import_line
    assert "ItemStack" not in import_line


def test_bedrock_tick_hook_uses_run_interval() -> None:
    result = _bedrock('onTick(function() say("a") end)')
    script = result.files["bp/scripts/main.ts"]
    assert "system.runInterval" in script


def test_bedrock_join_hook_uses_player_spawn() -> None:
    result = _bedrock('onJoin(function() say("a") end)')
    script = result.files["bp/scripts/main.ts"]
    assert "playerSpawn.subscribe" in script


def test_bedrock_iteration_binds_the_loop_variable() -> None:
    result = _bedrock(
        """
        onTick(function()
          forEachPlayer(function(p)
            say("hola")
          end)
        end)
        """
    )
    script = result.files["bp/scripts/main.ts"]
    assert "for (const player of world.getAllPlayers())" in script
    assert "world.getEntity(\"player\")" not in script


def test_bedrock_condition_becomes_a_typescript_if() -> None:
    result = _bedrock(
        """
        onTick(function()
          forEachPlayer(function(p)
            if score(p, "coins") >= 100 then
              say("ganaste")
            end
          end)
        end)
        """
    )
    script = result.files["bp/scripts/main.ts"]
    assert "if (scoreboard.getScore(\"coins\").value >= 100)" in script


def test_bedrock_target_hook_is_emitted() -> None:
    result = _bedrock('onBedrockOnly(function() say("solo bedrock") end)')
    script = result.files["bp/scripts/main.ts"]
    assert "solo bedrock" in script


def test_bedrock_drops_the_java_hook() -> None:
    result = _bedrock('onJavaOnly(function() say("solo java") end)')
    script = result.files["bp/scripts/main.ts"]
    assert "solo java" not in script


# -- Pipeline -------------------------------------------------------------


def test_a_syntax_error_is_reported_not_raised() -> None:
    result = build('say("hola', Edition.JAVA)
    assert result.ok is False
    assert result.parse_error is not None
    assert len(result.errors) == 1


def test_an_unavailable_capability_fails_the_build() -> None:
    result = build('setDynamicProperty(player, "k", 1)', Edition.JAVA)
    assert result.ok is False


def test_a_partial_capability_still_builds() -> None:
    result = build('setblock(0, 64, 0, "minecraft:stone")', Edition.BEDROCK)
    assert result.ok is True
    assert len(result.warnings) == 1


def test_the_report_json_is_machine_readable() -> None:
    from luacraft.pipeline import report_json

    result = build('setblock(0, 64, 0, "minecraft:stone")', Edition.BEDROCK)
    payload = json.loads(report_json(result))
    assert payload["edition"] == "bedrock"
    assert payload["ok"] is True
    assert len(payload["warnings"]) == 1
    assert payload["warnings"][0]["category"] == "partial-support"


def test_writing_a_java_pack_produces_a_zip(tmp_path: Path) -> None:
    result = build('say("hola")', Edition.JAVA, namespace="test")
    output = write_pack(result, tmp_path / "mod.zip")
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
    assert "pack.mcmeta" in names
    assert "data/test/functions/main.mcfunction" in names


def test_writing_an_addon_produces_two_packs(tmp_path: Path) -> None:
    result = build('say("hola")', Edition.BEDROCK, namespace="test")
    output = write_mcaddon(result, tmp_path / "mod.mcaddon", namespace="test")
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
    assert "test_bp/manifest.json" in names
    assert "test_rp/manifest.json" in names
    assert "test_bp/scripts/main.ts" in names


def test_builds_are_reproducible(tmp_path: Path) -> None:
    source = 'say("hola")\nonTick(function() give(player, "minecraft:diamond", 1) end)'
    first = build(source, Edition.BEDROCK, namespace="test")
    second = build(source, Edition.BEDROCK, namespace="test")
    assert first.files == second.files
