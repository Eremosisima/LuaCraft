"""Tests for the sandbox pack validator."""

from __future__ import annotations

import json
import zipfile

from luacraft.api import Edition
from luacraft.pipeline import build, write_mcaddon, write_pack
from luacraft.sandbox import validate_build, validate_directory, validate_files


SOURCE = """
items.ruby = { id = "test:ruby", texture = "ruby" }
blocks.lamp = { texture = "lamp" }
recipes.ruby_block = {
  type = "shaped",
  pattern = { "RR", "RR" },
  key = { R = "test:ruby" },
  result = "test:ruby",
}
onTick(function()
  forEachPlayer(function(p)
    score(p, "coins", "add", 1)
  end)
end)
"""


def _build(edition: Edition):
    return build(SOURCE, edition, namespace="test", generate_textures=True)


def test_java_build_passes_the_sandbox() -> None:
    report = validate_build(_build(Edition.JAVA))
    assert report.ok is True
    names = [check.name for check in report.checks]
    assert "json" in names
    assert "java.commands" in names
    assert "java.resource_pack_meta" in names
    assert report.failures == []


def test_bedrock_build_passes_the_sandbox() -> None:
    report = validate_build(_build(Edition.BEDROCK))
    assert report.ok is True
    names = [check.name for check in report.checks]
    assert "bedrock.bp/manifest.json" in names
    assert "bedrock.items" in names


def test_luanti_build_passes_the_sandbox() -> None:
    report = validate_build(_build(Edition.LUANTI))
    assert report.ok is True
    names = [check.name for check in report.checks]
    assert "luanti.init_lua" in names
    assert "luanti.textures" in names


def test_invalid_json_fails_the_sandbox() -> None:
    result = _build(Edition.JAVA)
    result.files["data/test/tags/functions/load.json"] = "{not json"
    report = validate_build(result)
    assert report.ok is False
    assert "json" in report.failures[0].name


def test_non_png_image_fails_the_sandbox() -> None:
    result = _build(Edition.LUANTI)
    result.files["textures/ruby.png"] = b"GIF89a"
    report = validate_build(result)
    assert report.ok is False
    assert any(check.name == "png" for check in report.failures)


def test_unknown_java_command_fails_the_sandbox() -> None:
    result = _build(Edition.JAVA)
    result.files["data/test/functions/main.mcfunction"] = "explode @s\n"
    report = validate_build(result)
    assert report.ok is False
    assert any(check.name == "java.commands" for check in report.failures)


def test_missing_java_function_target_fails_the_sandbox() -> None:
    result = _build(Edition.JAVA)
    result.files["data/test/functions/main.mcfunction"] = "function test:nope\n"
    report = validate_build(result)
    assert report.ok is False
    assert any(
        check.name == "java.function_references" for check in report.failures
    )


def test_broken_luanti_script_fails_the_sandbox() -> None:
    result = _build(Edition.LUANTI)
    result.files["init.lua"] = "this is not lua ("
    report = validate_build(result)
    assert report.ok is False
    assert any(check.name == "luanti.init_lua" for check in report.failures)


def test_validate_zip_of_a_datapack(tmp_path) -> None:
    result = _build(Edition.JAVA)
    output = write_pack(result, tmp_path / "mod.zip")
    from luacraft.sandbox import validate_zip

    report = validate_zip(output)
    assert report.edition is Edition.JAVA
    assert report.ok is True


def test_validate_zip_of_an_addon(tmp_path) -> None:
    result = _build(Edition.BEDROCK)
    output = write_mcaddon(result, tmp_path / "mod.mcaddon", namespace="test")
    from luacraft.sandbox import validate_zip

    report = validate_zip(output)
    assert report.edition is Edition.BEDROCK
    assert report.ok is True


def test_validate_directory(tmp_path) -> None:
    result = _build(Edition.LUANTI)
    pack = tmp_path / "mod"
    for path, body in result.files.items():
        target = pack / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            target.write_bytes(body)
        else:
            target.write_text(body, encoding="utf-8")
    report = validate_directory(pack)
    assert report.edition is Edition.LUANTI
    assert report.ok is True


def test_report_is_json_friendly() -> None:
    report = validate_build(_build(Edition.JAVA))
    payload = json.loads(json.dumps(report.as_dict()))
    assert payload["edition"] == "java"
    assert payload["ok"] is True
    assert payload["limitations"]


def test_report_format_is_human_readable() -> None:
    text = validate_build(_build(Edition.JAVA)).format()
    assert "sandbox: Java Edition" in text
    assert "[ok  ]" in text
