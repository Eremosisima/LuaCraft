"""Godot 4 project exporter.

Why Godot
---------
Luanti's client is a C++ application with its own Irrlicht-derived renderer.
Embedding it in Godot would mean building Luanti, writing a GDExtension and
keeping two engines in sync; there is no maintained drop-in module for that.
What LuaCraft can do honestly, and what this module does, is *re-host the
content*: the same converted entities, OBJ meshes, textures, items and recipes
become a native Godot 4 project with a small runtime that spawns them.

Graphics backends
-----------------
Godot 4 already abstracts the graphics API behind its rendering methods:

``vulkan``
    ``forward_plus``, Vulkan on desktop.  The default, and the only backend
    this project targets when the user asks for the "simple" path.
``vulkan-mobile``
    ``mobile``, Vulkan through the mobile renderer.
``opengl``
    ``gl_compatibility``, OpenGL 3.3 / GLES 3, for old integrated GPUs.

The choice is written into ``project.godot`` at generation time because Godot
does not switch rendering method at runtime.  Regenerating with another
``--graphics`` value is the supported way to change it, and ``graphics.gd``
reports at startup which method is actually active.

Everything the exporter cannot carry (Luanti Lua gameplay, Molang animations,
shaders) is reported as a diagnostic, not silently dropped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .content import Content
from .diagnostics import Category, DiagnosticBag
from .textures import TextureSet, texture_names


class GraphicsBackend(str, Enum):
    """The rendering method a generated Godot project requests."""

    VULKAN = "vulkan"
    VULKAN_MOBILE = "vulkan-mobile"
    OPENGL = "opengl"

    @classmethod
    def parse(cls, value: str) -> "GraphicsBackend":
        normalised = (value or "").strip().lower().replace("_", "-")
        aliases = {
            "vulkan": cls.VULKAN,
            "forward-plus": cls.VULKAN,
            "forward_plus": cls.VULKAN,
            "mobile": cls.VULKAN_MOBILE,
            "vulkan-mobile": cls.VULKAN_MOBILE,
            "opengl": cls.OPENGL,
            "gl": cls.OPENGL,
            "compatibility": cls.OPENGL,
            "gl-compatibility": cls.OPENGL,
        }
        if normalised not in aliases:
            raise ValueError(
                f"unknown graphics backend {value!r}; use vulkan, vulkan-mobile or opengl"
            )
        return aliases[normalised]

    @property
    def rendering_method(self) -> str:
        return {
            GraphicsBackend.VULKAN: "forward_plus",
            GraphicsBackend.VULKAN_MOBILE: "mobile",
            GraphicsBackend.OPENGL: "gl_compatibility",
        }[self]

    @property
    def feature(self) -> str:
        return {
            GraphicsBackend.VULKAN: "Forward Plus",
            GraphicsBackend.VULKAN_MOBILE: "Mobile",
            GraphicsBackend.OPENGL: "GL Compatibility",
        }[self]

    @property
    def api(self) -> str:
        return "Vulkan" if self is not GraphicsBackend.OPENGL else "OpenGL"


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _asset_name(value: str) -> str:
    name = value.split(":", 1)[-1]
    cleaned = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in name
    )
    return cleaned.strip("_") or "asset"


@dataclass
class GodotProject:
    """Renders a Godot 4 project from intermediate content."""

    project_name: str = "LuaCraft"
    namespace: str = "luacraft"
    graphics: GraphicsBackend = GraphicsBackend.VULKAN
    godot_version: str = "4.3"
    #: OBJ files keyed by model name, as emitted for Luanti.
    models: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.diagnostics = DiagnosticBag()

    # -- public -----------------------------------------------------------

    def build(
        self, content: Content, textures: TextureSet | None = None
    ) -> dict[str, bytes | str]:
        files: dict[str, bytes | str] = {
            "project.godot": self._project_file(),
            "icon.svg": _ICON_SVG,
            "scenes/main.tscn": self._main_scene(),
            "scripts/luacraft_runtime.gd": _RUNTIME_SCRIPT,
            "scripts/graphics.gd": self._graphics_script(),
            "data/content.json": _json(self._content_data(content)),
            "README.md": self._readme(content),
        }

        for entity in content.entities.values():
            name = _asset_name(entity.model or entity.name)
            if name in self.models:
                files[f"models/{name}.obj"] = self.models[name]

        if textures is not None:
            for texture in texture_names(content):
                files[f"textures/{texture}.png"] = textures.for_name(texture)

        self._warn_limits(content)
        return files

    def report(self, content: Content) -> dict[str, Any]:
        return {
            "target": "godot-project",
            "godot_version": self.godot_version,
            "graphics": self.graphics.value,
            "rendering_method": self.graphics.rendering_method,
            "graphics_api": self.graphics.api,
            "counts": {
                "entities": len(content.entities),
                "items": len(content.items),
                "recipes": len(content.recipes),
                "models": len(self.models),
            },
            "notes": list(self.notes),
            "limitations": [
                "This is a native Godot re-host of the converted content, not "
                "an embedded Luanti client.",
                "Luanti Lua gameplay and Molang animations are not executed; the "
                "runtime provides a small wander animation.",
                "Godot's rendering method is a project setting: regenerate the "
                "project with another --graphics value to switch backend.",
            ],
        }

    # -- documents --------------------------------------------------------

    def _project_file(self) -> str:
        features = ", ".join(
            f'"{value}"' for value in (self.godot_version, self.graphics.feature)
        )
        method = self.graphics.rendering_method
        return (
            "; Generated by LuaCraft.\n"
            "; Graphics backend: "
            f"{self.graphics.value} ({self.graphics.api}, {method}).\n"
            "; Regenerate with `luacraft godot --graphics "
            f"{self.graphics.value}` to change it.\n"
            "config_version=5\n"
            "\n"
            "[application]\n"
            "\n"
            f'config/name="{self.project_name}"\n'
            'run/main_scene="res://scenes/main.tscn"\n'
            f"config/features=PackedStringArray({features})\n"
            'config/icon="res://icon.svg"\n'
            "\n"
            "[autoload]\n"
            "\n"
            'Graphics="*res://scripts/graphics.gd"\n'
            "\n"
            "[display]\n"
            "\n"
            "window/size/viewport_width=1152\n"
            "window/size/viewport_height=648\n"
            "window/stretch/mode=\"canvas_items\"\n"
            "\n"
            "[input]\n"
            "\n"
            "next_entity={\n"
            '"deadzone": 0.5,\n'
            '"events": [Object(InputEventKey,"physical_keycode":4194321)]\n'
            "}\n"
            "previous_entity={\n"
            '"deadzone": 0.5,\n'
            '"events": [Object(InputEventKey,"physical_keycode":4194319)]\n'
            "}\n"
            "\n"
            "[rendering]\n"
            "\n"
            f'renderer/rendering_method="{method}"\n'
            f'renderer/rendering_method.mobile="{method}"\n'
            "environment/defaults/default_clear_color=Color(0.078, 0.086, 0.11, 1)\n"
        )

    def _main_scene(self) -> str:
        return (
            "[gd_scene load_steps=2 format=3]\n"
            "\n"
            '[ext_resource type="Script" path="res://scripts/luacraft_runtime.gd" id="1"]\n'
            "\n"
            "[node name=\"LuaCraftWorld\" type=\"Node3D\"]\n"
            'script = ExtResource("1")\n'
            "\n"
            "[node name=\"Camera3D\" type=\"Camera3D\" parent=\".\"]\n"
            "transform = Transform3D(1, 0, 0, 0, 0.95, 0.31, 0, -0.31, 0.95, 0, 2.2, 6.5)\n"
            "fov = 65.0\n"
            "\n"
            "[node name=\"Sun\" type=\"DirectionalLight3D\" parent=\".\"]\n"
            "transform = Transform3D(1, 0, 0, 0, 0.7, 0.7, 0, -0.7, 0.7, 0, 6, 0)\n"
            "light_energy = 1.2\n"
            "shadow_enabled = true\n"
        )

    def _graphics_script(self) -> str:
        expected = self.graphics.rendering_method
        return (
            "extends Node\n"
            "\n"
            "# Reports which rendering method Godot actually started with.  Godot\n"
            "# picks the backend at project load, so switching it requires\n"
            "# regenerating project.godot (luacraft godot --graphics ...).\n"
            "\n"
            f'const EXPECTED := "{expected}"\n'
            f'const REQUESTED := "{self.graphics.value}"\n'
            "\n"
            "func _ready() -> void:\n"
            '    var active := RenderingServer.get_current_rendering_method()\n'
            '    var adapter := RenderingServer.get_video_adapter_name()\n'
            '    print("[LuaCraft] graphics: requested=%s active=%s adapter=%s" % [REQUESTED, active, adapter])\n'
            "    if active != EXPECTED:\n"
            "        push_warning(\n"
            '            "[LuaCraft] requested %s but Godot started with %s; '
            "regenerate the project with the --graphics flag that matches this machine\"\n"
            "            % [EXPECTED, active]\n"
            "        )\n"
        )

    def _content_data(self, content: Content) -> dict[str, Any]:
        entities: list[dict[str, Any]] = []
        for entity in content.entities.values():
            model = _asset_name(entity.model or entity.name)
            texture = entity.texture or entity.model or entity.name
            entities.append(
                {
                    "name": entity.name,
                    "identifier": entity.identifier,
                    "display_name": entity.display_name or entity.name,
                    "health": entity.health,
                    "width": entity.width,
                    "height": entity.height,
                    "speed": entity.movement_speed,
                    "hostile": bool(entity.hostile),
                    "tameable": bool(entity.tameable),
                    "families": list(entity.families),
                    "model": f"models/{model}.obj" if model in self.models else None,
                    "texture": f"textures/{texture}.png",
                    "drops": [
                        {
                            "item": drop["item"],
                            "min": drop.get("min", 1),
                            "max": drop.get("max", 1),
                            "chance": drop.get("chance", 1.0),
                        }
                        for drop in entity.drops
                    ],
                }
            )
        items = [
            {
                "name": item.name,
                "identifier": item.identifier,
                "display_name": item.display_name or item.name,
                "max_stack": item.max_stack,
                "texture": f"textures/{item.texture or item.name}.png",
                "fallback_for": item.fallback_for,
            }
            for item in content.items.values()
        ]
        recipes = [
            {
                "name": recipe.name,
                "type": recipe.type,
                "result": recipe.result,
                "count": recipe.count,
                "ingredients": list(recipe.ingredients),
                "pattern": list(recipe.pattern),
            }
            for recipe in content.recipes.values()
        ]
        return {
            "namespace": self.namespace,
            "graphics": self.graphics.value,
            "entities": entities,
            "items": items,
            "recipes": recipes,
        }

    def _readme(self, content: Content) -> str:
        return (
            f"# {self.project_name} for Godot 4\n\n"
            "Generated by LuaCraft. Open this folder with Godot "
            f"{self.godot_version} or newer and press F5.\n\n"
            f"Graphics backend: **{self.graphics.value}** "
            f"(`{self.graphics.rendering_method}`, {self.graphics.api}).\n\n"
            "## Running\n\n"
            "```\n"
            "godot --path . \n"
            "```\n\n"
            "## Switching graphics backend\n\n"
            "Godot chooses the rendering method when it loads the project, so the\n"
            "backend is written into `project.godot`. Regenerate the project with\n"
            "the other flag:\n\n"
            "```\n"
            "luacraft godot mod.lua -o out/godot --graphics opengl\n"
            "luacraft godot mod.lua -o out/godot --graphics vulkan\n"
            "```\n\n"
            "`scripts/graphics.gd` prints the adapter and method that actually\n"
            "started, so a mismatch is visible in the output console instead of\n"
            "being a silent performance problem.\n\n"
            "## What is included\n\n"
            f"- {len(content.entities)} entities with OBJ meshes, textures, hitboxes\n"
            f"- {len(content.items)} items and {len(content.recipes)} recipes as data\n"
            "- controls: left/right arrow shows the previous/next entity\n\n"
            "## What is not\n\n"
            "This is a content re-host, not an embedded Luanti client. LuaCraft does\n"
            "not execute Luanti Lua mods, Molang animations or game shaders in\n"
            "Godot. Behaviour is limited to the small wander animation in\n"
            "`scripts/luacraft_runtime.gd`.\n"
        )

    # -- diagnostics ------------------------------------------------------

    def _warn_limits(self, content: Content) -> None:
        self.diagnostics.warn(
            Category.PARTIAL_SUPPORT,
            (
                "the Godot target re-hosts content: it does not embed the Luanti "
                "client or execute Luanti Lua mods. Gameplay is limited to the "
                "generated runtime"
            ),
            0,
            edition="godot",
            hint="extend scripts/luacraft_runtime.gd for project specific behaviour",
        )
        missing = [
            entity.identifier
            for entity in content.entities.values()
            if _asset_name(entity.model or entity.name) not in self.models
        ]
        if missing:
            self.diagnostics.warn(
                Category.PARTIAL_SUPPORT,
                (
                    f"{len(missing)} entities have no OBJ model; the runtime shows "
                    "them as coloured boxes: " + ", ".join(missing[:5])
                ),
                0,
                edition="godot",
            )


# --------------------------------------------------------------------------
# Static assets
# --------------------------------------------------------------------------

#: A minimal Godot icon, so the project opens without a missing resource.
_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" '
    'viewBox="0 0 128 128">'
    '<rect width="128" height="128" rx="20" fill="#14161c"/>'
    '<path d="M32 92 L32 40 L64 24 L96 40 L96 92 L64 108 Z" fill="#4cc2ff"/>'
    '<path d="M64 24 L64 66 L96 40 Z" fill="#2a8fc4"/>'
    '<path d="M32 40 L64 66 L64 108 Z" fill="#1d6f9c"/>'
    "</svg>\n"
)

#: The runtime that turns the converted data into a Godot scene.
_RUNTIME_SCRIPT = '''extends Node3D

## Spawns the converted entities in a ring, adds a ground plane and lets the
## player cycle through the models.  Deliberately small: the exporter does not
## pretend to reproduce a full game.

const CONTENT_PATH := "res://data/content.json"
const BASE_URL := "res://"

var entities: Array = []
var selected := 0
var label: Label
var spawns: Array = []


func _ready() -> void:
	var file := FileAccess.open(CONTENT_PATH, FileAccess.READ)
	if file == null:
		push_error("[LuaCraft] no data/content.json in this project")
		return
	var data: Variant = JSON.parse_string(file.get_as_text())
	if typeof(data) != TYPE_DICTIONARY:
		push_error("[LuaCraft] data/content.json is not a JSON object")
		return
	entities = data.get("entities", [])
	_build_ground()
	_build_hud()
	if entities.is_empty():
		label.text = "No entities were converted."
		return
	var radius := clampf(1.4 + entities.size() * 0.22, 2.0, 6.0)
	for index in entities.size():
		var angle := TAU * float(index) / float(entities.size())
		var position := Vector3(cos(angle) * radius, 0.0, sin(angle) * radius)
		spawns.append(_spawn(entities[index], position))
	_select(0)


func _build_ground() -> void:
	var mesh := PlaneMesh.new()
	mesh.size = Vector2(40.0, 40.0)
	var instance := MeshInstance3D.new()
	instance.name = "Ground"
	instance.mesh = mesh
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(0.17, 0.2, 0.16)
	material.roughness = 1.0
	instance.material_override = material
	add_child(instance)


func _build_hud() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	label = Label.new()
	label.position = Vector2(16, 12)
	layer.add_child(label)


func _spawn(data: Dictionary, position: Vector3) -> Node3D:
	var root := Node3D.new()
	root.position = position
	root.set_meta("home", position)
	root.set_meta("phase", float(position.x) * 1.7)
	add_child(root)

	var model_path: Variant = data.get("model")
	var mesh_instance := MeshInstance3D.new()
	if typeof(model_path) == TYPE_STRING:
		var mesh: Mesh = load(BASE_URL + String(model_path))
		if mesh != null:
			mesh_instance.mesh = mesh
	if mesh_instance.mesh == null:
		var box := BoxMesh.new()
		box.size = Vector3(float(data.get("width", 0.6)), float(data.get("height", 1.8)), float(data.get("width", 0.6)))
		mesh_instance.mesh = box
	root.add_child(mesh_instance)

	var material := StandardMaterial3D.new()
	var texture_path: Variant = data.get("texture")
	if typeof(texture_path) == TYPE_STRING and ResourceLoader.exists(BASE_URL + String(texture_path)):
		material.albedo_texture = load(BASE_URL + String(texture_path))
	material.roughness = 0.9
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	mesh_instance.material_override = material

	var body := StaticBody3D.new()
	var shape := CollisionShape3D.new()
	var box_shape := BoxShape3D.new()
	box_shape.size = Vector3(float(data.get("width", 0.6)), float(data.get("height", 1.8)), float(data.get("width", 0.6)))
	shape.shape = box_shape
	shape.position.y = float(data.get("height", 1.8)) * 0.5
	body.add_child(shape)
	root.add_child(body)

	var name_label := Label3D.new()
	name_label.text = String(data.get("display_name", data.get("name", "entity")))
	name_label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	name_label.position.y = float(data.get("height", 1.8)) + 0.35
	name_label.font_size = 48
	name_label.outline_size = 8
	root.add_child(name_label)
	return root


func _select(index: int) -> void:
	selected = index
	var data: Dictionary = entities[selected]
	label.text = "%s  (%d/%d)\\nLeft/Right: change entity" % [
		String(data.get("display_name", data.get("name", "entity"))),
		selected + 1,
		entities.size(),
	]


func _input(event: InputEvent) -> void:
	if entities.is_empty():
		return
	if event.is_action_pressed("next_entity"):
		_select((selected + 1) % entities.size())
	if event.is_action_pressed("previous_entity"):
		_select((selected - 1 + entities.size()) % entities.size())


func _process(delta: float) -> void:
	var now := Time.get_ticks_msec() / 1000.0
	for index in spawns.size():
		var node: Node3D = spawns[index]
		var home: Vector3 = node.get_meta("home")
		var phase: float = node.get_meta("phase")
		node.position.x = home.x + cos(now * 0.6 + phase) * 0.35
		node.position.z = home.z + sin(now * 0.5 + phase) * 0.35
		node.rotate_y(delta * 0.6)
'''
