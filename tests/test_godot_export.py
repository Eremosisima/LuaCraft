"""Tests for the Godot 4 project exporter and its sandbox."""

from __future__ import annotations

import json

from luacraft.api import Edition
from luacraft.godot_export import GodotProject, GraphicsBackend
from luacraft.pipeline import build
from luacraft.sandbox import validate_godot

_SOURCE = """
entities.benogrok = {
  id = "faunalibre:benogrok",
  texture = "benogrok",
  model = "benogrok_model",
  display_name = "Benogrok",
  health = 17,
  collision_box = {width = 1.8, height = 0.69},
  movement_speed = 0.22,
  drops = {{item = "faunalibre:fruta_lunar", min = 1, max = 1, chance = 1.0}},
}
"""

_OBJ = """# fake obj
o benogrok_model
v 0 0 0
v 1 0 0
v 1 1 0
f 1 2 3
"""


def _build():
    return build(
        _SOURCE,
        Edition.LUANTI,
        namespace="faunalibre",
        generate_textures=True,
    )


def _project(graphics: str = "vulkan"):
    result = _build()
    exporter = GodotProject(
        project_name="FaunaLibre",
        namespace="faunalibre",
        graphics=GraphicsBackend.parse(graphics),
        models={"benogrok_model": _OBJ},
    )
    return exporter.build(result.content, result.textures)


def test_backend_aliases() -> None:
    assert GraphicsBackend.parse("vulkan") is GraphicsBackend.VULKAN
    assert GraphicsBackend.parse("opengl") is GraphicsBackend.OPENGL
    assert GraphicsBackend.parse("gl_compatibility") is GraphicsBackend.OPENGL
    assert GraphicsBackend.parse("mobile") is GraphicsBackend.VULKAN_MOBILE


def test_vulkan_project_requests_forward_plus() -> None:
    files = _project("vulkan")
    assert 'renderer/rendering_method="forward_plus"' in files["project.godot"]
    assert "Forward Plus" in files["project.godot"]


def test_opengl_project_requests_gl_compatibility() -> None:
    files = _project("opengl")
    assert 'renderer/rendering_method="gl_compatibility"' in files["project.godot"]
    assert "GL Compatibility" in files["project.godot"]


def test_graphics_script_reports_the_active_method() -> None:
    files = _project("vulkan")
    script = files["scripts/graphics.gd"]
    assert "RenderingServer.get_current_rendering_method()" in script
    assert '"forward_plus"' in script


def test_content_json_lists_entities_and_assets() -> None:
    files = _project()
    payload = json.loads(files["data/content.json"])
    assert payload["entities"][0]["identifier"] == "faunalibre:benogrok"
    assert payload["entities"][0]["model"] == "models/benogrok_model.obj"
    assert payload["entities"][0]["texture"] == "textures/benogrok.png"
    assert files["models/benogrok_model.obj"] == _OBJ


def test_scene_has_camera_and_runtime_script() -> None:
    files = _project()
    scene = files["scenes/main.tscn"]
    assert 'path="res://scripts/luacraft_runtime.gd"' in scene
    assert 'type="Camera3D"' in scene
    assert 'type="DirectionalLight3D"' in scene


def test_sandbox_accepts_the_project() -> None:
    report = validate_godot(_project())
    assert report.ok, report.format()
    assert not report.failures


def test_sandbox_rejects_an_unknown_rendering_method() -> None:
    files = _project()
    files["project.godot"] = files["project.godot"].replace(
        'renderer/rendering_method="forward_plus"',
        'renderer/rendering_method="directx"',
    )
    report = validate_godot(files)
    assert not report.ok


def test_sandbox_rejects_a_missing_model() -> None:
    files = _project()
    del files["models/benogrok_model.obj"]
    report = validate_godot(files)
    assert not report.ok
