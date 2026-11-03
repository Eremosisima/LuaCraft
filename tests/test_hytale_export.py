"""Tests for the Hytale asset pack exporter and its sandbox."""

from __future__ import annotations

import json

from luacraft.api import Edition
from luacraft.hytale_export import HytalePack
from luacraft.pipeline import build
from luacraft.sandbox import validate_hytale

_SOURCE = """
items.fruta_lunar = {
  id = "faunalibre:fruta_lunar",
  texture = "fruta_lunar",
  display_name = "Fruta Lunar",
  max_stack = 64,
  category = "nature",
}

entities.benogrok = {
  id = "faunalibre:benogrok",
  texture = "benogrok",
  model = "benogrok_model",
  display_name = "Benogrok",
  health = 17,
  collision_box = {width = 1.8, height = 0.69},
  movement_speed = 0.22,
  tameable = {probability = 0.33, tame_items = {"faunalibre:fruta_lunar"}},
  drops = {
    {item = "faunalibre:fruta_lunar", min = 1, max = 2, chance = 0.5},
  },
}

recipes.fruta_lunar_recipe = {
  type = "shapeless",
  ingredients = {"minecraft:apple", "minecraft:sugar"},
  result = "faunalibre:fruta_lunar",
  count = 2,
}
"""


def _build():
    return build(
        _SOURCE,
        Edition.LUANTI,
        namespace="faunalibre",
        generate_textures=True,
    )


def _pack(files=None):
    result = _build()
    exporter = HytalePack(
        namespace="faunalibre",
        pack_name="FaunaLibre",
        blockymodels={
            "benogrok_model": {
                "format": "character",
                "lod": "auto",
                "nodes": [{"id": "1", "name": "body", "shape": {"type": "box"}}],
            }
        },
    )
    generated = exporter.build(result.content, result.textures)
    generated.update(files or {})
    return generated


def test_manifest_names_the_pack() -> None:
    files = _pack()
    manifest = json.loads(files["manifest.json"])
    assert manifest["Name"] == "FaunaLibre"
    assert manifest["IncludesAssetPack"] is True


def test_items_become_item_definitions() -> None:
    files = _pack()
    item = json.loads(files["Server/Item/Items/fruta_lunar.json"])
    assert item["TranslationProperties"]["Name"] == (
        "items.faunalibre.fruta_lunar.name"
    )
    assert item["MaxStack"] == 64
    assert item["Icon"].endswith("fruta_lunar.png")


def test_entities_become_npc_roles_and_models() -> None:
    files = _pack()
    role = json.loads(files["Server/NPC/Roles/benogrok.json"])
    assert role["Type"] == "Generic"
    assert role["Appearance"] == "Models/benogrok.json"
    assert role["Stats"]["Health"] == 17.0
    model = json.loads(files["Server/Models/benogrok.json"])
    assert model["Model"] == "NPC/benogrok/Model.blockymodel"
    assert model["Texture"] == "NPC/benogrok/Texture.png"


def test_drops_use_the_container_schema() -> None:
    files = _pack()
    drops = json.loads(files["Server/Drops/benogrok_drops.json"])
    assert drops["Container"]["Type"] == "Multiple"
    entry = drops["Container"]["Entries"][0]
    # A 50% chance becomes a Choice between the item and Empty.
    assert entry["Type"] == "Choice"
    assert {item["Type"] for item in entry["Entries"]} == {"Single", "Empty"}


def test_blockymodel_and_textures_are_written() -> None:
    files = _pack()
    assert "Common/NPC/benogrok/Model.blockymodel" in files
    assert "Common/NPC/benogrok/Texture.png" in files
    assert "Common/Icons/ItemsGenerated/fruta_lunar.png" in files
    assert all(
        files[path].startswith(b"\x89PNG")
        for path in files
        if path.endswith(".png")
    )


def test_localisation_has_item_and_npc_names() -> None:
    files = _pack()
    lang = json.loads(files["Server/Lang/en-US.json"])
    assert lang["items.faunalibre.fruta_lunar.name"] == "Fruta Lunar"
    assert lang["npcs.faunalibre.benogrok.name"] == "Benogrok"


def test_sandbox_accepts_the_generated_pack() -> None:
    report = validate_hytale(_pack())
    assert report.ok, report.format()


def test_sandbox_rejects_a_role_without_a_model() -> None:
    files = _pack()
    files["Server/NPC/Roles/benogrok.json"] = json.dumps(
        {"Type": "Generic", "Appearance": "Models/missing.json"}
    )
    report = validate_hytale(files)
    assert not report.ok
    assert any(check.name == "hytale.roles" for check in report.failures)


def test_sandbox_rejects_invalid_block_json() -> None:
    files = _pack()
    files["Server/Models/broken.json"] = "{not json"
    report = validate_hytale(files)
    assert not report.ok
