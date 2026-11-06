"""Textures for the generated packs.

A mod without textures is a mod nobody can look at, but LuaCraft must never
download anything: no asset is fetched, bundled or copied from a game.  This
module solves the problem in two honest ways.

* The author can drop real ``.png`` files in a folder next to the source.
  LuaCraft copies them into the right place for each edition.
* When no file is supplied, LuaCraft *generates* a deterministic placeholder
  PNG with the Python standard library.  The placeholder is not art, but it
  is unique per texture name, so a build is complete and installable.

The PNG encoder here is intentionally small: 8 bit RGBA, no interlacing.  It
only needs to write what the generator produces, not to read arbitrary
images.

Layout per edition
------------------

Java Edition
    Resource packs hold the image under ``assets/<ns>/textures/item/`` plus a
    model, and a model override on the base vanilla item keyed by
    ``custom_model_data``.  That is how a datapack can show a custom texture
    without a mod.

Bedrock Edition
    The resource pack holds ``textures/items`` and ``textures/blocks``, plus
    the ``item_texture.json`` and ``terrain_texture.json`` atlases and a
    language file.

Luanti
    A mod simply keeps its images in ``textures/``.
"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from .content import Content
from .diagnostics import Category, DiagnosticBag

#: Size of a generated placeholder, in pixels.
PLACEHOLDER_SIZE = 16

#: Java resource pack format that matches the default datapack format (1.21).
JAVA_RESOURCE_PACK_FORMAT = 34

#: First value of ``custom_model_data`` handed to declared items.
CUSTOM_MODEL_DATA_BASE = 1000


# --------------------------------------------------------------------------
# PNG encoding
# --------------------------------------------------------------------------


def encode_png(width: int, height: int, pixels: list[list[tuple[int, int, int, int]]]) -> bytes:
    """Encode RGBA pixels as a PNG file.

    ``pixels`` is a list of rows, each row a list of ``(r, g, b, a)`` tuples.
    """
    if len(pixels) != height or any(len(row) != width for row in pixels):
        raise ValueError("the pixel matrix does not match the given size")

    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filter type 0: none
        for r, g, b, a in row:
            raw.extend((r & 0xFF, g & 0xFF, b & 0xFF, a & 0xFF))

    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        signature
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )


def _chunk(kind: bytes, payload: bytes) -> bytes:
    """Build one PNG chunk with its length and CRC."""
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def is_png(data: bytes) -> bool:
    return data.startswith(b"\x89PNG\r\n\x1a\n")


# --------------------------------------------------------------------------
# Placeholder generation
# --------------------------------------------------------------------------


def _palette(seed: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """A stable pair of colours derived from the texture name."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    base = (
        48 + digest[0] % 160,
        48 + digest[1] % 160,
        48 + digest[2] % 160,
    )
    light = tuple(min(255, channel + 56) for channel in base)
    return base, light  # type: ignore[return-value]


def placeholder_png(name: str, size: int = PLACEHOLDER_SIZE) -> bytes:
    """Generate a deterministic placeholder texture for ``name``.

    The pattern is a rounded square with a lighter bevel, a diagonal band and
    a border.  It is deliberately simple: recognisable, tileable and unique
    enough that two declared items never look the same.
    """
    base, light = _palette(name)
    dark = tuple(max(0, channel - 40) for channel in base)
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    band_width = 2 + digest[3] % 3
    band_offset = digest[4] % size

    pixels: list[list[tuple[int, int, int, int]]] = []
    for y in range(size):
        row: list[tuple[int, int, int, int]] = []
        for x in range(size):
            border = x in (0, size - 1) or y in (0, size - 1)
            bevel = x == 1 or y == 1
            in_band = ((x + y + band_offset) % size) < band_width

            if border:
                colour = dark
            elif bevel:
                colour = light
            elif in_band:
                colour = light if (x + y) % 4 else base
            else:
                colour = base

            # A small transparent corner, so the item does not look like a
            # flat square when the texture is rendered with alpha blending.
            corner = (
                (x < 2 and y < 2)
                or (x < 2 and y >= size - 2)
                or (x >= size - 2 and y < 2)
                or (x >= size - 2 and y >= size - 2)
            )
            alpha = 0 if corner and not border else 255
            row.append((colour[0], colour[1], colour[2], alpha))
        pixels.append(row)
    return encode_png(size, size, pixels)


# --------------------------------------------------------------------------
# The texture set
# --------------------------------------------------------------------------


@dataclass
class TextureSet:
    """The images a build may use, keyed by texture name."""

    images: dict[str, bytes] = field(default_factory=dict)
    #: Where the images came from, for the build report.
    source: str = "generated"

    @classmethod
    def from_directory(cls, directory: Path) -> "TextureSet":
        """Load every PNG in a directory, keyed by its file stem."""
        images: dict[str, bytes] = {}
        if directory.is_dir():
            for path in sorted(directory.rglob("*.png")):
                data = path.read_bytes()
                if not is_png(data):
                    continue
                stem = path.stem
                images[stem] = data
                if stem.endswith(".png"):
                    images[stem[: -len(".png")]] = data
        return cls(images=images, source=str(directory))

    @classmethod
    def generate(cls, names: "set[str]") -> "TextureSet":
        """Build a set of placeholders for the given texture names."""
        return cls(images={name: placeholder_png(name) for name in names})

    def for_name(self, name: str) -> bytes:
        """The image for a texture name, generating a placeholder if needed."""
        image = self.images.get(name)
        if image is None:
            image = placeholder_png(name)
            self.images[name] = image
        return image

    def __bool__(self) -> bool:
        return bool(self.images)


def texture_names(content: Content) -> list[str]:
    """Every texture name the declared content refers to, deduplicated."""
    names: list[str] = []
    for item in content.items.values():
        names.append(item.texture or item.name)
    for block in content.blocks.values():
        names.append(block.texture or block.name)
    for entity in content.entities.values():
        names.append(entity.texture or entity.model or entity.name)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def prepare_textures(
    content: Content,
    *,
    directory: Path | None = None,
    generate: bool = True,
) -> TextureSet:
    """Assemble the texture set a build will use."""
    found = TextureSet.from_directory(directory) if directory else TextureSet()
    if not generate:
        return found
    for name in texture_names(content):
        if name not in found.images:
            found.images[name] = placeholder_png(name)
    return found


# --------------------------------------------------------------------------
# Rendering per edition
# --------------------------------------------------------------------------


def _json(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def render_java_textures(
    content: Content,
    textures: TextureSet,
    namespace: str,
    pack_format: int = JAVA_RESOURCE_PACK_FORMAT,
    diagnostics: DiagnosticBag | None = None,
) -> dict[str, bytes | str]:
    """Render a Java resource pack for the declared content.

    The pack holds the images, a generated model per item and block, and a
    model override on the base vanilla item.  The override is keyed by the
    ``custom_model_data`` value that the datapack puts on the item, which is
    the only way vanilla Java can show a custom texture.
    """
    diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()
    files: dict[str, bytes | str] = {}

    files["pack.mcmeta"] = _json(
        {
            "pack": {
                "pack_format": pack_format,
                "description": "LuaCraft resource pack",
                "supported_formats": {
                    "min_inclusive": 15,
                    "max_inclusive": 999,
                },
            }
        }
    )

    overrides: dict[str, list[dict]] = {}
    lang: dict[str, str] = {}

    for index, item in enumerate(content.items.values()):
        texture = item.texture or item.name
        files[f"assets/{namespace}/textures/item/{texture}.png"] = textures.for_name(texture)
        files[f"assets/{namespace}/models/item/{item.name}.json"] = _json(
            {
                "parent": "minecraft:item/generated",
                "textures": {"layer0": f"{namespace}:item/{texture}"},
            }
        )
        # Minecraft 1.21.4 replaced model overrides with item model
        # definitions in assets/<ns>/items/.  Both layouts are emitted so the
        # pack keeps working on old and new clients.
        files[f"assets/{namespace}/items/{item.name}.json"] = _json(
            {
                "model": {
                    "type": "minecraft:model",
                    "model": f"{namespace}:item/{item.name}",
                }
            }
        )
        base = item.base_item
        if base.startswith("minecraft:"):
            override = {"predicate": {"custom_model_data": CUSTOM_MODEL_DATA_BASE + index},
                        "model": f"{namespace}:item/{item.name}"}
            overrides.setdefault(base, []).append(override)
        if item.display_name:
            lang[f"item.{namespace}.{item.name}"] = item.display_name

    for block in content.blocks.values():
        texture = block.texture or block.name
        files[f"assets/{namespace}/textures/block/{texture}.png"] = textures.for_name(texture)
        files[f"assets/{namespace}/models/block/{block.name}.json"] = _json(
            {
                "parent": "minecraft:block/cube_all",
                "textures": {"all": f"{namespace}:block/{texture}"},
            }
        )

    for base, entries in overrides.items():
        base_name = base.split(":", 1)[1]
        files[f"assets/minecraft/models/item/{base_name}.json"] = _json(
            {
                "parent": "minecraft:item/generated",
                "textures": {"layer0": f"minecraft:item/{base_name}"},
                "overrides": entries,
            }
        )

    if lang:
        files[f"assets/{namespace}/lang/en_us.json"] = _json(lang)

    if content.items or content.blocks:
        diagnostics.warn(
            Category.PARTIAL_SUPPORT,
            (
                "Java Edition cannot register new items or blocks from a "
                "datapack. The resource pack retextures the base vanilla item "
                "through minecraft:item_model (and legacy custom_model_data); "
                "the item keeps its vanilla name and behaviour"
            ),
            0,
            edition="java",
            hint=(
                "install both the datapack and the resource pack, then give "
                "the item with the minecraft:item_model component"
            ),
        )

    return files


def render_bedrock_textures(
    content: Content,
    textures: TextureSet,
    namespace: str,
    diagnostics: DiagnosticBag | None = None,
) -> dict[str, bytes | str]:
    """Render the resource pack side of the Bedrock textures."""
    files: dict[str, bytes | str] = {}

    item_entries: dict[str, dict] = {}
    block_entries: dict[str, dict] = {}
    lang: dict[str, str] = {}

    for item in content.items.values():
        texture = item.texture or item.name
        files[f"rp/textures/items/{texture}.png"] = textures.for_name(texture)
        item_entries[texture] = {"textures": f"textures/items/{texture}"}
        lang[f"item.{item.identifier}"] = item.display_name or item.name

    for block in content.blocks.values():
        texture = block.texture or block.name
        files[f"rp/textures/blocks/{texture}.png"] = textures.for_name(texture)
        block_entries[texture] = {"textures": f"textures/blocks/{texture}"}
        lang[f"tile.{block.identifier}.name"] = block.name

    if item_entries:
        files["rp/textures/item_texture.json"] = _json(
            {
                "resource_pack_name": namespace,
                "texture_name": "atlas.items",
                "texture_data": item_entries,
            }
        )
    if block_entries:
        files["rp/textures/terrain_texture.json"] = _json(
            {
                "resource_pack_name": namespace,
                "texture_name": "atlas.terrain",
                "padding": 8,
                "num_mip_levels": 4,
                "texture_data": block_entries,
            }
        )
    if lang:
        files["rp/texts/en_US.lang"] = "".join(
            f"{key}={value}\n" for key, value in sorted(lang.items())
        )
        files["rp/texts/languages.json"] = _json(["en_US"])

    return files


def render_luanti_textures(
    content: Content,
    textures: TextureSet,
    diagnostics: DiagnosticBag | None = None,
) -> dict[str, bytes | str]:
    """Render the texture folder of a Luanti mod."""
    files: dict[str, bytes | str] = {}
    for item in content.items.values():
        texture = item.texture or item.name
        files[f"textures/{texture}.png"] = textures.for_name(texture)
    for block in content.blocks.values():
        texture = block.texture or block.name
        files[f"textures/{texture}.png"] = textures.for_name(texture)
    for entity in content.entities.values():
        texture = entity.texture or entity.model or entity.name
        files[f"textures/{texture}.png"] = textures.for_name(texture)
    return files


def render_textures(
    edition,
    content: Content,
    textures: TextureSet,
    namespace: str,
    diagnostics: DiagnosticBag | None = None,
) -> dict[str, bytes | str]:
    """Render the textures for one edition."""
    from .api import Edition

    if edition is Edition.JAVA:
        return render_java_textures(content, textures, namespace, diagnostics=diagnostics)
    if edition is Edition.BEDROCK:
        return render_bedrock_textures(content, textures, namespace, diagnostics=diagnostics)
    return render_luanti_textures(content, textures, diagnostics=diagnostics)
