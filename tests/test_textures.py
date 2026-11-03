"""Tests for texture generation and rendering."""

from __future__ import annotations

import json
import zipfile

from luacraft.api import Edition
from luacraft.content import read_content
from luacraft.debug import Trace
from luacraft.diagnostics import DiagnosticBag
from luacraft.luaparse import parse
from luacraft.pipeline import build, write_java_resourcepack
from luacraft.sandbox import validate_build, validate_zip
from luacraft.textures import (
    CUSTOM_MODEL_DATA_BASE,
    TextureSet,
    encode_png,
    is_png,
    placeholder_png,
    prepare_textures,
    render_java_textures,
)


def test_placeholder_png_is_valid() -> None:
    data = placeholder_png("ruby")
    assert is_png(data)
    assert len(data) > 100


def test_placeholders_are_deterministic() -> None:
    assert placeholder_png("ruby") == placeholder_png("ruby")
    assert placeholder_png("ruby") != placeholder_png("sapphire")


def test_texture_set_from_directory(tmp_path) -> None:
    png = placeholder_png("ruby")
    (tmp_path / "ruby.png").write_bytes(png)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "emerald.png").write_bytes(placeholder_png("emerald"))
    textures = TextureSet.from_directory(tmp_path)
    assert "ruby" in textures.images
    assert "emerald" in textures.images


def test_prepare_textures_generates_for_declared_content() -> None:
    bag = DiagnosticBag()
    chunk = parse('items.ruby = { texture = "ruby" }\nblocks.lamp = { texture = "lamp" }')
    content = read_content(chunk.body, "test", bag)
    textures = prepare_textures(content, generate=True)
    assert "ruby" in textures.images
    assert "lamp" in textures.images


def test_java_resourcepack_has_models_and_overrides() -> None:
    result = build(
        'items.ruby = { id = "test:ruby", texture = "ruby" }',
        Edition.JAVA,
        namespace="test",
        generate_textures=True,
    )
    assert result.resources
    assert "pack.mcmeta" in result.resources
    assert "assets/test/models/item/ruby.json" in result.resources
    model = json.loads(result.resources["assets/test/models/item/ruby.json"])
    assert model["textures"]["layer0"] == "test:item/ruby"
    base = json.loads(result.resources["assets/minecraft/models/item/stick.json"])
    assert base["overrides"][0]["predicate"]["custom_model_data"] == CUSTOM_MODEL_DATA_BASE
    assert base["overrides"][0]["model"] == "test:item/ruby"


def test_java_resourcepack_written_separately(tmp_path) -> None:
    result = build(
        'items.ruby = { id = "test:ruby", texture = "ruby" }',
        Edition.JAVA,
        namespace="test",
        generate_textures=True,
    )
    path = write_java_resourcepack(result, tmp_path / "rp.zip")
    with zipfile.ZipFile(path) as archive:
        assert "assets/test/textures/item/ruby.png" in archive.namelist()
        assert "assets/minecraft/models/item/stick.json" in archive.namelist()


def test_bedrock_textures_have_atlases_and_lang() -> None:
    result = build(
        'items.ruby = { id = "test:ruby", texture = "ruby" }\nblocks.lamp = { texture = "lamp" }',
        Edition.BEDROCK,
        namespace="test",
        generate_textures=True,
    )
    assert "rp/textures/item_texture.json" in result.files
    assert "rp/textures/terrain_texture.json" in result.files
    assert "rp/texts/en_US.lang" in result.files
    items = json.loads(result.files["rp/textures/item_texture.json"])
    assert "ruby" in items["texture_data"]
    assert "rp/textures/items/ruby.png" in result.files


def test_luanti_textures_in_textures_folder() -> None:
    result = build(
        'items.ruby = { id = "test:ruby", texture = "ruby" }',
        Edition.LUANTI,
        namespace="test",
        generate_textures=True,
    )
    assert "textures/ruby.png" in result.files


def test_render_java_textures_warns_about_datapack_limitation() -> None:
    bag = DiagnosticBag()
    chunk = parse('items.ruby = { }')
    content = read_content(chunk.body, "test", bag)
    textures = TextureSet.generate({"ruby"})
    render_java_textures(content, textures, "test", diagnostics=bag)
    assert any("cannot register new items" in d.message for d in bag.warnings)


def test_debug_trace_records_phases() -> None:
    result = build('say("hola")', Edition.JAVA, namespace="test", debug=True)
    assert result.trace.enabled
    phases = [event.phase for event in result.trace.events]
    assert "parse" in phases
    assert "analyse" in phases
    assert "backend:java" in phases
    assert result.trace.total_ms >= 0
    data = result.trace.as_dict()
    assert data["enabled"] is True
    assert "edition" in data["data"]
