"""Tests for the playable world exporter."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from luacraft import nbt, world_export
from luacraft.bedrock_leveldb import crc32c, masked_crc32c, read_manifest_edit, write_empty_db


def _write_zip(path: Path, entries: dict[str, str | bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return path


def _bundle(tmp_path: Path):
    root = tmp_path / "bundle"
    _write_zip(
        root / "dist" / "demo-java.zip",
        {
            "pack.mcmeta": json.dumps(
                {"pack": {"pack_format": 48, "description": "demo"}}
            ),
            "data/demo/function/main.mcfunction": "say hello\n",
            "data/minecraft/tags/function/load.json": json.dumps(
                {"values": ["demo:_objectives"]}
            ),
            "data/demo/function/_objectives.mcfunction": "scoreboard objectives add coins dummy\n",
        },
    )
    _write_zip(
        root / "dist" / "demo-java-resources.zip",
        {
            "pack.mcmeta": json.dumps(
                {"pack": {"pack_format": 34, "description": "demo"}}
            ),
            "assets/demo/items/ruby.json": json.dumps(
                {"model": {"type": "minecraft:model", "model": "demo:item/ruby"}}
            ),
        },
    )
    _write_zip(
        root / "dist" / "demo.mcaddon",
        {
            "demo_bp/manifest.json": json.dumps(
                {
                    "format_version": 2,
                    "header": {
                        "name": "Demo (behavior_pack)",
                        "uuid": "11111111-1111-1111-1111-111111111111",
                        "version": [1, 21, 0],
                    },
                    "modules": [{"type": "data", "uuid": "22222222-2222-2222-2222-222222222222"}],
                }
            ),
            "demo_bp/functions/main.mcfunction": "say hi\n",
            "demo_rp/manifest.json": json.dumps(
                {
                    "format_version": 2,
                    "header": {
                        "name": "Demo (resource_pack)",
                        "uuid": "33333333-3333-3333-3333-333333333333",
                        "version": [1, 21, 0],
                    },
                    "modules": [{"type": "resources", "uuid": "44444444-4444-4444-4444-444444444444"}],
                }
            ),
            "demo_rp/textures/item_texture.json": "{}",
        },
    )
    (root / "conversion.json").write_text(
        json.dumps(
            {
                "namespace": "demo",
                "pack_name": "Demo Pack",
                "source": "demo.mcaddon",
            }
        ),
        encoding="utf-8",
    )
    (root / "mod.lua").write_text(
        'items.ruby = { id = "demo:ruby", base = "minecraft:paper", display_name = "Ruby" }\n',
        encoding="utf-8",
    )
    return root


def test_leveldb_checksum_matches_the_known_vector() -> None:
    assert crc32c(b"123456789") == 0xE3069283
    assert masked_crc32c(b"123456789") != 0


def test_empty_leveldb_has_a_valid_manifest(tmp_path: Path) -> None:
    db = write_empty_db(tmp_path / "db")
    assert (db / "CURRENT").read_text(encoding="ascii") == "MANIFEST-000001\n"
    payload = read_manifest_edit(db / "MANIFEST-000001")
    assert b"leveldb.BytewiseComparator" in payload
    assert (db / "000003.log").is_file()


def test_nbt_round_trips_both_byte_orders(tmp_path: Path) -> None:
    value = nbt.Compound(
        {
            "Name": nbt.String("Mundo"),
            "Seed": nbt.Long(7),
            "Flags": nbt.List((nbt.Byte(1), nbt.Byte(0)), nbt.TAG_BYTE),
        }
    )
    java = nbt.encode_java_level(value)
    bedrock = nbt.encode_bedrock_level(value)
    assert nbt.to_plain(nbt.decode_java_level(java))["Name"] == "Mundo"
    assert nbt.to_plain(nbt.decode_bedrock_level(bedrock))["Seed"] == 7
    assert bedrock[:4] == (10).to_bytes(4, "little")


def test_load_bundle_reads_the_generated_archives(tmp_path: Path) -> None:
    bundle = world_export.load_bundle(_bundle(tmp_path))
    assert bundle.namespace == "demo"
    assert bundle.display_name == "Demo Pack"
    assert bundle.java_datapack is not None
    assert bundle.bedrock_addon is not None
    assert bundle.content is not None
    assert "ruby" in bundle.content.items
    assert bundle.content.items["ruby"].identifier == "demo:ruby"


def test_java_world_contains_level_dat_and_starter_kit(tmp_path: Path) -> None:
    bundle = world_export.load_bundle(_bundle(tmp_path))
    plan = world_export.build_java_world(bundle, tmp_path / "worlds", world_name="Demo")
    world = plan.directory
    assert world == tmp_path / "worlds" / "java" / "Demo"
    assert (world / "level.dat").is_file()
    assert (world / "datapacks" / "demo.zip").is_file()
    assert (world / "resourcepacks" / "demo-resources.zip").is_file()

    data = nbt.to_plain(nbt.decode_java_level((world / "level.dat").read_bytes()))["Data"]
    assert data["LevelName"] == "Demo"
    assert data["DataVersion"] == world_export.JAVA_DATA_VERSION
    assert "file/demo.zip" in data["DataPacks"]["Enabled"]

    with zipfile.ZipFile(world / "datapacks" / "demo.zip") as archive:
        kit = archive.read("data/demo/function/kit.mcfunction").decode("utf-8")
        assert 'minecraft:item_model="demo:ruby"' in kit
        load = json.loads(archive.read("data/minecraft/tags/function/load.json"))
        assert "demo:_luacraft_welcome" in load["values"]
        assert "demo:_objectives" in load["values"]
        meta = json.loads(archive.read("pack.mcmeta"))
        assert meta["pack"]["supported_formats"]["max_inclusive"] == 999


def test_bedrock_world_has_level_dat_packs_and_database(tmp_path: Path) -> None:
    bundle = world_export.load_bundle(_bundle(tmp_path))
    plan = world_export.build_bedrock_world(bundle, tmp_path / "worlds", world_name="Demo")
    world = plan.directory
    assert (world / "level.dat").is_file()
    assert (world / "levelname.txt").read_text(encoding="utf-8").strip() == "Demo"
    assert (world / "db" / "CURRENT").is_file()
    assert (world / "behavior_packs" / "demo_bp" / "manifest.json").is_file()
    assert (world / "resource_packs" / "demo_rp" / "manifest.json").is_file()

    header = (world / "level.dat").read_bytes()[:8]
    assert int.from_bytes(header[:4], "little") == world_export.BEDROCK_STORAGE_VERSION
    data = nbt.to_plain(nbt.decode_bedrock_level((world / "level.dat").read_bytes()))
    assert data["LevelName"] == "Demo"
    assert data["Generator"] == 1

    behaviour = json.loads((world / "world_behavior_packs.json").read_text())
    resource = json.loads((world / "world_resource_packs.json").read_text())
    assert behaviour[0]["pack_id"] == "11111111-1111-1111-1111-111111111111"
    assert resource[0]["pack_id"] == "33333333-3333-3333-3333-333333333333"
    kit = (world / "behavior_packs" / "demo_bp" / "functions" / "kit.mcfunction")
    assert "demo:ruby" in kit.read_text(encoding="utf-8")


def test_install_java_copies_the_world_and_enables_the_pack(tmp_path: Path) -> None:
    bundle = world_export.load_bundle(_bundle(tmp_path))
    plan = world_export.build_java_world(bundle, tmp_path / "worlds", world_name="Demo")
    minecraft = tmp_path / ".minecraft"
    installed = world_export.install_java(plan, minecraft_dir=minecraft)
    assert installed == minecraft / "saves" / "Demo"
    assert (minecraft / "resourcepacks" / "demo-resources.zip").is_file()
    options = (minecraft / "options.txt").read_text(encoding="utf-8")
    assert "file/demo-resources.zip" in options


def test_install_bedrock_copies_the_world(tmp_path: Path) -> None:
    bundle = world_export.load_bundle(_bundle(tmp_path))
    plan = world_export.build_bedrock_world(bundle, tmp_path / "worlds", world_name="Demo")
    game = tmp_path / "com.mojang"
    installed = world_export.install_bedrock(plan, game_dir=game)
    assert installed == game / "minecraftWorlds" / "Demo"
    assert (installed / "level.dat").is_file()


def test_pack_mcworld_has_the_world_at_the_zip_root(tmp_path: Path) -> None:
    bundle = world_export.load_bundle(_bundle(tmp_path))
    plan = world_export.build_bedrock_world(bundle, tmp_path / "worlds", world_name="Demo")
    archive = world_export.pack_mcworld(plan, tmp_path / "Demo.mcworld")
    with zipfile.ZipFile(archive) as opened:
        assert "level.dat" in opened.namelist()
        assert "demo_bp/manifest.json" not in opened.namelist()
        assert "behavior_packs/demo_bp/manifest.json" in opened.namelist()
