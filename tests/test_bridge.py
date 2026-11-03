"""Tests for the Bedrock converter entities and the bridge CLI."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

from luacraft.api import Edition
from luacraft.bridge import run_bridge
from luacraft.pipeline import build
from luacraft.textures import TextureSet

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from convert_bedrock import convert_pack, render_lua  # noqa: E402


def _pack(tmp_path: Path) -> Path:
    path = tmp_path / "test.mcaddon"
    behaviour = {
        "format_version": "1.21.0",
        "minecraft:entity": {
            "description": {
                "identifier": "demo:benogrok",
                "is_spawnable": True,
                "is_summonable": True,
            },
            "components": {
                "minecraft:type_family": {"family": ["fauna", "benogrok"]},
                "minecraft:health": {"value": 17, "max": 17},
                "minecraft:collision_box": {"width": 1.8, "height": 0.69},
                "minecraft:movement": {"value": 0.22},
                "minecraft:tameable": {
                    "probability": 0.33,
                    "tame_items": ["demo:fruta_lunar"],
                },
                "minecraft:loot": {"table": "loot_tables/entities/benogrok.json"},
                "minecraft:behavior.random_stroll": {"priority": 6},
            },
        },
    }
    client = {
        "format_version": "1.10.0",
        "minecraft:client_entity": {
            "description": {
                "identifier": "demo:benogrok",
                "textures": {"default": "textures/entity/benogrok"},
                "geometry": {"default": "geometry.benogrok"},
            }
        },
    }
    geometry = {
        "format_version": "1.16.0",
        "minecraft:geometry": [
            {
                "description": {
                    "identifier": "geometry.benogrok",
                    "texture_width": 64,
                    "texture_height": 64,
                },
                "bones": [
                    {
                        "name": "body",
                        "cubes": [
                            {
                                "origin": [-8, 0, -8],
                                "size": [16, 16, 16],
                                "uv": [0, 0],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    loot = {
        "pools": [
            {
                "rolls": 1,
                "entries": [
                    {
                        "type": "item",
                        "name": "demo:fruta_lunar",
                        "weight": 1,
                        "functions": [
                            {
                                "function": "set_count",
                                "count": {"min": 1, "max": 2},
                            }
                        ],
                    }
                ],
            }
        ]
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("BP/entities/benogrok.json", json.dumps(behaviour))
        archive.writestr("RP/entity/benogrok.entity.json", json.dumps(client))
        archive.writestr("RP/models/entity/benogrok.geo.json", json.dumps(geometry))
        archive.writestr(
            "BP/loot_tables/entities/benogrok.json", json.dumps(loot)
        )
        archive.writestr("RP/textures/entity/benogrok.png", b"\x89PNG\r\n\x1a\nrest")
    return path


def test_converter_reads_entities_models_loot_and_taming(tmp_path: Path) -> None:
    conversion = convert_pack(_pack(tmp_path))
    assert len(conversion.entities) == 1
    entity = conversion.entities[0]
    assert entity.identifier == "demo:benogrok"
    assert entity.width == 1.8
    assert entity.height == 0.69
    assert entity.health == 17
    assert entity.movement_speed == 0.22
    assert entity.tameable is not None
    assert entity.tameable["tame_items"] == ["demo:fruta_lunar"]
    assert entity.drops and entity.drops[0]["item"] == "demo:fruta_lunar"
    assert entity.drops[0]["max"] == 2
    assert entity.model in conversion.models
    assert entity.model in conversion.blockymodels
    assert any("behavior" in item for item in entity.unsupported)


def test_converter_marks_the_species_card_as_a_fallback(tmp_path: Path) -> None:
    conversion = convert_pack(_pack(tmp_path))
    cards = [item for item in conversion.items if item.kind == "species_card"]
    assert len(cards) == 1
    assert f'fallback_for = "{cards[0].source}"' in render_lua(conversion)


def test_luanti_skips_the_fallback_card_and_registers_the_entity(
    tmp_path: Path,
) -> None:
    conversion = convert_pack(_pack(tmp_path))
    result = build(
        render_lua(conversion),
        Edition.LUANTI,
        namespace="demo",
        models=dict(conversion.models),
        generate_textures=True,
    )
    script = result.files["init.lua"]
    assert 'S.register_entity("demo:benogrok"' in script
    assert "models/benogrok_model.obj" in script
    assert "collisionbox" in script
    assert "register_craftitem" not in script  # the only item was the card
    assert "models/benogrok_model.obj" in result.files


def test_bridge_writes_every_target(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = run_bridge(
        _pack(tmp_path),
        out,
        graphics="opengl",
        targets="bedrock,luanti,hytale,godot",
    )
    assert result.ok
    assert (out / "mod.lua").exists()
    assert (out / "conversion.json").exists()
    assert (out / "bridge.json").exists()
    report = json.loads((out / "bridge.json").read_text(encoding="utf-8"))
    assert report["targets"] == ["bedrock", "luanti", "hytale", "godot"]
    assert report["capabilities"]["luanti"].startswith("real entities")
    assert (out / "godot" / "project.godot").exists()
    assert 'renderer/rendering_method="gl_compatibility"' in (
        out / "godot" / "project.godot"
    ).read_text(encoding="utf-8")
    assert any(path.suffix == ".zip" for path in out.iterdir())


def test_bridge_rejects_an_unknown_target(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        run_bridge(_pack(tmp_path), tmp_path / "out", targets="nintendo")


def test_texture_set_is_shared_between_builds(tmp_path: Path) -> None:
    conversion = convert_pack(_pack(tmp_path))
    textures = TextureSet.generate({"benogrok"})
    result = build(
        render_lua(conversion),
        Edition.BEDROCK,
        namespace="demo",
        textures=textures,
        models=dict(conversion.models),
    )
    assert result.ok
