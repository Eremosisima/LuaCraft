"""Tests for the command line interface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from luacraft.cli import main


def _write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "mod.lua"
    path.write_text(source, encoding="utf-8")
    return path


def test_build_writes_a_bedrock_addon(tmp_path: Path, capsys) -> None:
    source = _write(tmp_path, 'say("hola")')
    output = tmp_path / "mod.mcaddon"
    code = main(
        ["build", str(source), "--target", "bedrock", "-o", str(output)]
    )
    assert code == 0
    assert output.exists()
    captured = capsys.readouterr()
    assert "Bedrock Edition" in captured.out


def test_build_writes_a_java_datapack(tmp_path: Path, capsys) -> None:
    source = _write(tmp_path, 'say("hola")')
    output = tmp_path / "mod.zip"
    code = main(["build", str(source), "--target", "java", "-o", str(output)])
    assert code == 0
    assert output.exists()


def test_build_fails_on_a_missing_source(tmp_path: Path, capsys) -> None:
    code = main(
        ["build", str(tmp_path / "nope.lua"), "--target", "bedrock"]
    )
    assert code == 2
    assert "does not exist" in capsys.readouterr().err


def test_build_fails_when_a_capability_is_unavailable(
    tmp_path: Path, capsys
) -> None:
    source = _write(tmp_path, 'setDynamicProperty(player, "k", 1)')
    code = main(["build", str(source), "--target", "java"])
    assert code == 1
    assert "not available in Java Edition" in capsys.readouterr().err


def test_strict_mode_fails_on_warnings(tmp_path: Path, capsys) -> None:
    source = _write(
        tmp_path, 'setblock(0, 64, 0, "minecraft:stone")'
    )
    code = main(["build", str(source), "--target", "bedrock", "--strict"])
    assert code == 1
    assert "strict mode" in capsys.readouterr().err


def test_strict_mode_passes_without_warnings(tmp_path: Path) -> None:
    source = _write(tmp_path, 'say("hola")')
    code = main(["build", str(source), "--target", "bedrock", "--strict"])
    assert code == 0


def test_the_json_report_is_valid(tmp_path: Path, capsys) -> None:
    source = _write(
        tmp_path, 'setblock(0, 64, 0, "minecraft:stone")'
    )
    main(["build", str(source), "--target", "bedrock", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["edition"] == "bedrock"
    assert payload["ok"] is True
    assert len(payload["warnings"]) == 1


def test_check_reports_both_editions(tmp_path: Path, capsys) -> None:
    source = _write(tmp_path, 'say("hola")')
    code = main(["check", str(source), "--target", "both"])
    assert code == 0
    captured = capsys.readouterr()
    assert "Java Edition" in captured.out
    assert "Bedrock Edition" in captured.out


def test_check_json_output(tmp_path: Path, capsys) -> None:
    source = _write(tmp_path, 'say("hola")')
    main(["check", str(source), "--target", "both", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert "java" in payload
    assert "bedrock" in payload


def test_api_prints_the_primitive_table(capsys) -> None:
    code = main(["api"])
    assert code == 0
    captured = capsys.readouterr()
    assert "say" in captured.out
    assert "setDynamicProperty" in captured.out


def test_api_json_output(capsys) -> None:
    main(["api", "--json"])
    payload = json.loads(capsys.readouterr().out)
    names = {entry["name"] for entry in payload}
    assert "say" in names
    assert "nbt" in names


def test_content_lists_the_declarations(tmp_path: Path, capsys) -> None:
    source = _write(
        tmp_path,
        """
        items.ruby = { id = "test:ruby" }
        recipes.sword = { type = "shapeless", ingredients = {}, result = "test:s" }
        """,
    )
    code = main(["content", str(source), "-n", "test"])
    assert code == 0
    captured = capsys.readouterr()
    assert "ruby" in captured.out
    assert "test:ruby" in captured.out
    assert "sword" in captured.out


def test_content_json_output(tmp_path: Path, capsys) -> None:
    source = _write(tmp_path, 'items.ruby = { id = "test:ruby" }')
    main(["content", str(source), "-n", "test", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["items"] == 1
    assert payload["items"]["ruby"]["identifier"] == "test:ruby"


def test_content_reports_an_empty_source(tmp_path: Path, capsys) -> None:
    source = _write(tmp_path, 'say("hola")')
    main(["content", str(source)])
    assert "no content declared" in capsys.readouterr().out


def test_an_unknown_target_is_rejected(tmp_path: Path) -> None:
    source = _write(tmp_path, 'say("hola")')
    with pytest.raises(SystemExit):
        main(["build", str(source), "--target", "gamecube"])


# -- Godot and Hytale exporters -------------------------------------------


def test_godot_command_writes_a_project(tmp_path: Path, capsys) -> None:
    source = _write(tmp_path, 'items.ruby = { id = "test:ruby" }')
    output = tmp_path / "godot"
    code = main(
        [
            "godot",
            str(source),
            "-o",
            str(output),
            "--graphics",
            "opengl",
            "--textures",
        ]
    )
    assert code == 0
    project = (output / "project.godot").read_text(encoding="utf-8")
    assert 'renderer/rendering_method="gl_compatibility"' in project
    assert "OpenGL" in capsys.readouterr().out


def test_godot_command_accepts_an_obj_folder(tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        """
        entities.mob = {
          id = "test:mob",
          model = "mob_model",
          texture = "mob",
          display_name = "Mob",
        }
        """,
    )
    models = tmp_path / "models"
    models.mkdir()
    (models / "mob_model.obj").write_text("o mob\nv 0 0 0\n", encoding="utf-8")
    output = tmp_path / "godot"
    code = main(
        ["godot", str(source), "-o", str(output), "--model-dir", str(models)]
    )
    assert code == 0
    assert (output / "models" / "mob_model.obj").exists()


def test_hytale_command_writes_a_pack(tmp_path: Path, capsys) -> None:
    source = _write(tmp_path, 'items.ruby = { id = "test:ruby" }')
    output = tmp_path / "pack.zip"
    code = main(["hytale", str(source), "-o", str(output), "--textures"])
    assert code == 0
    assert output.exists()
    assert "Hytale asset pack" in capsys.readouterr().out


def test_bridge_command_reports_json(tmp_path: Path, capsys) -> None:
    import zipfile

    pack = tmp_path / "demo.mcaddon"
    behaviour = {
        "format_version": "1.21.0",
        "minecraft:entity": {
            "description": {"identifier": "demo:thing", "is_spawnable": True},
            "components": {"minecraft:health": {"value": 5}},
        },
    }
    with zipfile.ZipFile(pack, "w") as archive:
        archive.writestr("BP/entities/thing.json", json.dumps(behaviour))
    output = tmp_path / "out"
    code = main(["bridge", str(pack), "-o", str(output), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["targets"] == ["java", "bedrock", "luanti", "hytale", "godot"]
    assert (output / "bridge.json").exists()
