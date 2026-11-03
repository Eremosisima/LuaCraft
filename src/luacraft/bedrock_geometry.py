"""Bedrock geometry (``.geo.json``) reading and conversion.

Bedrock entity models are JSON files that describe a hierarchy of **bones**,
each holding axis aligned **cubes** with a UV region in a texture atlas.  They
are the closest thing Bedrock has to a portable model: no shaders, no compiled
meshes, just numbers.

This module turns that hierarchy into two targets:

``OBJ``
    What Luanti's mesh entities can load.  Bones are baked into world space,
    cubes become triangles, and the per-face UV region of the atlas becomes
    ``vt`` coordinates.

``.blockymodel``
    Hytale's JSON model format.  The hierarchy is preserved as nodes (so it can
    be animated later), cubes become centred box shapes, and the atlas region
    becomes a per-face ``textureLayout``.

Coordinate systems
------------------
Bedrock uses **16 units per block**, Y up and -Z forward.  Luanti meshes use
blocks, so the OBJ exporter divides by 16 and rotates 180 degrees around Y so
the model faces +Z, the direction a Luanti entity faces at yaw 0.

Hytale uses **32 units per block** and a hierarchy whose node position is
relative to the parent.  The block model exporter multiplies by 2, which keeps
the proportions and matches Hytale's documented ``BLOCK_SCALE`` of 1/32.

Rotations are Euler angles in degrees.  Bedrock applies them in Z, Y, X order
(the same order Blockbench exports), so the matrix is ``Rz * Ry * Rx``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

#: Bedrock model units per block.
BEDROCK_UNITS = 16.0

#: Hytale model units per block.
HYTALE_UNITS = 32.0

#: Number of texture pixels that a Bedrock cube face uses per model unit.
#: Bedrock atlases are authored at one texel per model unit.
UV_DENSITY = 1.0

_Vector = tuple[float, float, float]
_Matrix = tuple[tuple[float, float, float, float], ...]


# --------------------------------------------------------------------------
# Parsed representation
# --------------------------------------------------------------------------


@dataclass
class Cube:
    """One axis aligned (optionally rotated) box of a bone."""

    origin: _Vector
    size: _Vector
    uv: tuple[float, float] | None = None
    #: Per face UV overrides: face -> (u, v, width, height).
    face_uv: dict[str, tuple[float, float, float, float]] = field(
        default_factory=dict
    )
    pivot: _Vector | None = None
    rotation: _Vector = (0.0, 0.0, 0.0)
    inflate: float = 0.0
    mirror: bool = False


@dataclass
class Bone:
    name: str
    parent: str | None = None
    pivot: _Vector = (0.0, 0.0, 0.0)
    rotation: _Vector = (0.0, 0.0, 0.0)
    cubes: list[Cube] = field(default_factory=list)
    mirror: bool = False


@dataclass
class Geometry:
    """A parsed Bedrock model."""

    identifier: str = "geometry.unknown"
    texture_width: int = 64
    texture_height: int = 64
    visible_width: float = 1.0
    visible_height: float = 1.0
    visible_offset: _Vector = (0.0, 0.0, 0.0)
    bones: list[Bone] = field(default_factory=list)
    source: str = ""

    def bone(self, name: str) -> Bone | None:
        for bone in self.bones:
            if bone.name == name:
                return bone
        return None


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def parse_geometry(data: Any, *, source: str = "") -> Geometry | None:
    """Parse a ``.geo.json`` payload or a single geometry entry.

    Returns ``None`` when the payload does not look like Bedrock geometry, so a
    caller can report the file without crashing on a stray JSON document.
    """
    geometries = parse_geometries(data, source=source)
    return geometries[0] if geometries else None


def parse_geometries(data: Any, *, source: str = "") -> list[Geometry]:
    """Parse every geometry in a ``.geo.json`` payload.

    A single file can hold several geometry entries; the converter indexes all
    of them by identifier.
    """
    if not isinstance(data, dict):
        return []

    entries: list[Any] = []
    if "minecraft:geometry" in data:
        geometries = data.get("minecraft:geometry")
        if isinstance(geometries, list):
            entries = list(geometries)
        elif isinstance(geometries, dict):
            entries = [geometries]
    else:
        entries = [data]

    parsed: list[Geometry] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if "bones" not in entry and "description" not in entry:
            continue

        description = entry.get("description") or {}
        if not isinstance(description, dict):
            description = {}

        bones: list[Bone] = []
        raw_bones = entry.get("bones") or []
        if isinstance(raw_bones, list):
            for raw in raw_bones:
                bone = _parse_bone(raw)
                if bone is not None:
                    bones.append(bone)

        parsed.append(
            Geometry(
                identifier=str(description.get("identifier") or "geometry.unknown"),
                texture_width=_positive_int(description.get("texture_width"), 64),
                texture_height=_positive_int(description.get("texture_height"), 64),
                visible_width=_positive_float(
                    description.get("visible_bounds_width"), 1.0
                ),
                visible_height=_positive_float(
                    description.get("visible_bounds_height"), 1.0
                ),
                visible_offset=_vec3(
                    description.get("visible_bounds_offset"), (0.0, 0.0, 0.0)
                ),
                bones=bones,
                source=source,
            )
        )
    return parsed


def _parse_bone(raw: Any) -> Bone | None:
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        return None
    cubes: list[Cube] = []
    raw_cubes = raw.get("cubes") or []
    if isinstance(raw_cubes, list):
        for raw_cube in raw_cubes:
            cube = _parse_cube(raw_cube)
            if cube is not None:
                cubes.append(cube)
    return Bone(
        name=name,
        parent=raw.get("parent") if isinstance(raw.get("parent"), str) else None,
        pivot=_vec3(raw.get("pivot"), (0.0, 0.0, 0.0)),
        rotation=_vec3(raw.get("rotation"), (0.0, 0.0, 0.0)),
        cubes=cubes,
        mirror=bool(raw.get("mirror", False)),
    )


def _parse_cube(raw: Any) -> Cube | None:
    if not isinstance(raw, dict):
        return None
    origin = _vec3(raw.get("origin"), None)  # type: ignore[arg-type]
    size = _vec3(raw.get("size"), None)  # type: ignore[arg-type]
    if origin is None or size is None:
        return None
    if any(component == 0 for component in size):
        # Zero-thickness cubes are used for flat spikes and plants.  Bedrock
        # renders them, but a zero-area triangle is invisible and some
        # importers reject it, so give them a hair of thickness.
        size = tuple(component if component != 0 else 0.01 for component in size)

    face_uv: dict[str, tuple[float, float, float, float]] = {}
    uv_value = raw.get("uv")
    uv: tuple[float, float] | None = None
    if isinstance(uv_value, list) and len(uv_value) >= 2:
        uv = (_number(uv_value[0]), _number(uv_value[1]))
    elif isinstance(uv_value, dict):
        for face, entry in uv_value.items():
            if not isinstance(entry, dict):
                continue
            face_uv_point = entry.get("uv")
            face_size = entry.get("uv_size")
            if not isinstance(face_uv_point, list) or len(face_uv_point) < 2:
                continue
            width = _number(face_size[0]) if isinstance(face_size, list) and face_size else 0.0
            height = _number(face_size[1]) if isinstance(face_size, list) and len(face_size) > 1 else 0.0
            face_uv[str(face)] = (
                _number(face_uv_point[0]),
                _number(face_uv_point[1]),
                width,
                height,
            )

    pivot_value = raw.get("pivot")
    pivot = _vec3(pivot_value, (0.0, 0.0, 0.0)) if pivot_value is not None else None

    return Cube(
        origin=origin,
        size=size,
        uv=uv,
        face_uv=face_uv,
        pivot=pivot,
        rotation=_vec3(raw.get("rotation"), (0.0, 0.0, 0.0)),
        inflate=_number(raw.get("inflate", 0.0)),
        mirror=bool(raw.get("mirror", False)),
    )


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _vec3(value: Any, default: _Vector | None) -> _Vector:
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return (_number(value[0]), _number(value[1]), _number(value[2]))
    if default is None:  # type: ignore[return-value]
        raise ValueError("a vector is required")
    return default


def _positive_int(value: Any, default: int) -> int:
    number = int(_number(value, float(default)))
    return number if number > 0 else default


def _positive_float(value: Any, default: float) -> float:
    number = _number(value, default)
    return number if number > 0 else default


# --------------------------------------------------------------------------
# Matrices and rotations
# --------------------------------------------------------------------------


def _identity() -> _Matrix:
    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def multiply(left: _Matrix, right: _Matrix) -> _Matrix:
    """Multiply two 4x4 row-major matrices."""
    rows = []
    for row in range(4):
        cells = []
        for column in range(4):
            cells.append(
                sum(left[row][index] * right[index][column] for index in range(4))
            )
        rows.append(tuple(cells))
    return tuple(rows)


def translation(vector: _Vector) -> _Matrix:
    x, y, z = vector
    return (
        (1.0, 0.0, 0.0, x),
        (0.0, 1.0, 0.0, y),
        (0.0, 0.0, 1.0, z),
        (0.0, 0.0, 0.0, 1.0),
    )


def rotation_matrix(degrees: _Vector) -> _Matrix:
    """Euler XYZ degrees, applied in Z, Y, X order (Bedrock convention)."""
    import math

    rx, ry, rz = (math.radians(component) for component in degrees)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)

    # Rx
    rx_m: _Matrix = (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, cx, -sx, 0.0),
        (0.0, sx, cx, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    # Ry
    ry_m: _Matrix = (
        (cy, 0.0, sy, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (-sy, 0.0, cy, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    # Rz
    rz_m: _Matrix = (
        (cz, -sz, 0.0, 0.0),
        (sz, cz, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    return multiply(rz_m, multiply(ry_m, rx_m))


def apply(matrix: _Matrix, point: _Vector) -> _Vector:
    x, y, z = point
    return (
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3],
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3],
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z + matrix[2][3],
    )


def _around_pivot(pivot: _Vector, rotation: _Vector) -> _Matrix:
    return multiply(
        translation(pivot),
        multiply(rotation_matrix(rotation), translation((-pivot[0], -pivot[1], -pivot[2]))),
    )


def _cube_pivot(cube: Cube) -> _Vector:
    if cube.pivot is not None:
        return cube.pivot
    return (
        cube.origin[0] + cube.size[0] / 2.0,
        cube.origin[1] + cube.size[1] / 2.0,
        cube.origin[2] + cube.size[2] / 2.0,
    )


def bone_matrices(geometry: Geometry) -> dict[str, _Matrix]:
    """Bake every bone into model space, honouring the parent chain."""
    by_name = {bone.name: bone for bone in geometry.bones}
    matrices: dict[str, _Matrix] = {}

    def resolve(bone: Bone, seen: frozenset[str] = frozenset()) -> _Matrix:
        if bone.name in matrices:
            return matrices[bone.name]
        if bone.name in seen:
            matrices[bone.name] = _identity()
            return matrices[bone.name]
        local = _around_pivot(bone.pivot, bone.rotation)
        parent = by_name.get(bone.parent or "")
        if parent is not None:
            local = multiply(resolve(parent, seen | {bone.name}), local)
        matrices[bone.name] = local
        return local

    for bone in geometry.bones:
        resolve(bone)
    return matrices


def _cube_world(geometry: Geometry, cube: Cube, bone_world: _Matrix) -> _Matrix:
    pivot = _cube_pivot(cube)
    return multiply(
        bone_world,
        multiply(translation(pivot), multiply(rotation_matrix(cube.rotation), translation((-pivot[0], -pivot[1], -pivot[2])))),
    )


# --------------------------------------------------------------------------
# Faces and UVs
# --------------------------------------------------------------------------

#: Face order is fixed so the OBJ and blockymodel exporters agree.
FACES = ("north", "south", "east", "west", "up", "down")


def _face_layout(cube: Cube) -> dict[str, tuple[float, float, float, float]]:
    """Return ``face -> (u, v, width, height)`` in texture pixels."""
    size_x, size_y, size_z = cube.size
    layout: dict[str, tuple[float, float, float, float]] = {}

    if cube.uv is not None:
        u, v = cube.uv
        d, w, h = size_z, size_x, size_y
        layout = {
            "north": (u + d, v + d, w, h),
            "south": (u + 2 * d + w, v + d, w, h),
            "east": (u + d + w, v + d, d, h),
            "west": (u, v + d, d, h),
            "up": (u + d, v, w, d),
            "down": (u + d + w, v, w, d),
        }
    else:
        for face, (u, v, width, height) in cube.face_uv.items():
            face_name = face.lower()
            if face_name not in FACES:
                continue
            layout[face_name] = (u, v, abs(width), abs(height))

    if cube.mirror:
        layout = {
            face: (u + width, v, -width, height)
            for face, (u, v, width, height) in layout.items()
        }
    return layout


def _face_corners(cube: Cube, face: str) -> list[_Vector]:
    """Cube-local corners of a face, counter-clockwise from outside."""
    x0, y0, z0 = cube.origin
    sx, sy, sz = cube.size
    x1, y1, z1 = x0 + sx, y0 + sy, z0 + sz
    if face == "north":
        return [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)]
    if face == "south":
        return [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    if face == "west":
        return [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)]
    if face == "east":
        return [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)]
    if face == "up":
        return [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)]
    return [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)]


def _face_uvs(
    face: str, width: float, height: float, texture_width: int, texture_height: int
) -> list[tuple[float, float]]:
    """OBJ ``vt`` coordinates for the four corners, in image orientation."""
    u0, u1 = 0.0, width
    v0, v1 = 0.0, height
    # The caller adds the atlas offset.  Return the corners in the same order
    # as `_face_corners`, and flip V because OBJ's origin is bottom-left.
    corners = {
        "north": [(u0, v1), (u0, v0), (u1, v0), (u1, v1)],
        "south": [(u0, v1), (u1, v1), (u1, v0), (u0, v0)],
        "west": [(u0, v1), (u1, v1), (u1, v0), (u0, v0)],
        "east": [(u1, v1), (u1, v0), (u0, v0), (u0, v1)],
        "up": [(u0, v0), (u0, v1), (u1, v1), (u1, v0)],
        "down": [(u0, v0), (u1, v0), (u1, v1), (u0, v1)],
    }[face]
    return [(u / texture_width, v / texture_height) for u, v in corners]


def _face_normal(cube: Cube, face: str) -> _Vector:
    if face == "north":
        return (0.0, 0.0, -1.0)
    if face == "south":
        return (0.0, 0.0, 1.0)
    if face == "west":
        return (-1.0, 0.0, 0.0)
    if face == "east":
        return (1.0, 0.0, 0.0)
    if face == "up":
        return (0.0, 1.0, 0.0)
    return (0.0, -1.0, 0.0)


# --------------------------------------------------------------------------
# OBJ export (Luanti)
# --------------------------------------------------------------------------


def _to_mesh_space(point: _Vector) -> _Vector:
    """Bedrock model units -> Luanti blocks, facing +Z."""
    return (-point[0] / BEDROCK_UNITS, point[1] / BEDROCK_UNITS, -point[2] / BEDROCK_UNITS)


def to_obj(geometry: Geometry, *, name: str = "luacraft_entity") -> str:
    """Render the geometry as a Wavefront OBJ file.

    Vertices are expanded per face because every face has its own UV region;
    sharing vertices would not allow that.  Faces are triangulated, which every
    OBJ importer understands.
    """
    matrices = bone_matrices(geometry)
    lines = [f"# Generated by LuaCraft from {geometry.identifier}", f"o {name}"]
    vertices: list[_Vector] = []
    uvs: list[tuple[float, float]] = []
    normals: list[_Vector] = []
    triangles: list[tuple[tuple[int, int], tuple[int, int], tuple[int, int], int]] = []

    for bone in geometry.bones:
        world = matrices[bone.name]
        for cube in bone.cubes:
            cube_world = _cube_world(geometry, cube, world)
            layout = _face_layout(cube)
            for face in FACES:
                if face not in layout:
                    continue
                region_u, region_v, region_w, region_h = layout[face]
                corners = _face_corners(cube, face)
                screen_uvs = _face_uvs(
                    face,
                    abs(region_w),
                    abs(region_h),
                    geometry.texture_width,
                    geometry.texture_height,
                )
                sign_u = 1.0 if region_w >= 0 else -1.0
                sign_v = 1.0 if region_h >= 0 else -1.0
                base_index = len(vertices) + 1
                uv_base = len(uvs) + 1
                transformed: list[_Vector] = []
                for corner, (u, v) in zip(corners, screen_uvs):
                    point = apply(cube_world, corner)
                    transformed.append(point)
                    vertices.append(_to_mesh_space(point))
                    atlas_u = (
                        region_u + u * abs(region_w) * sign_u
                    ) / geometry.texture_width
                    atlas_v = (
                        region_v + v * abs(region_h) * sign_v
                    ) / geometry.texture_height
                    uvs.append((atlas_u, 1.0 - atlas_v))

                # The normal comes from the transformed triangle, so no
                # separate rotation bookkeeping can drift from the vertices.
                edge_a = (
                    transformed[1][0] - transformed[0][0],
                    transformed[1][1] - transformed[0][1],
                    transformed[1][2] - transformed[0][2],
                )
                edge_b = (
                    transformed[2][0] - transformed[0][0],
                    transformed[2][1] - transformed[0][1],
                    transformed[2][2] - transformed[0][2],
                )
                normal = (
                    edge_a[1] * edge_b[2] - edge_a[2] * edge_b[1],
                    edge_a[2] * edge_b[0] - edge_a[0] * edge_b[2],
                    edge_a[0] * edge_b[1] - edge_a[1] * edge_b[0],
                )
                length = (normal[0] ** 2 + normal[1] ** 2 + normal[2] ** 2) ** 0.5
                if length:
                    normal = (
                        normal[0] / length,
                        normal[1] / length,
                        normal[2] / length,
                    )
                normal_index = len(normals) + 1
                normals.append(_to_mesh_space(normal))
                triangles.append(
                    (
                        (base_index, uv_base, normal_index),
                        (base_index + 1, uv_base + 1, normal_index),
                        (base_index + 2, uv_base + 2, normal_index),
                        normal_index,
                    )
                )
                triangles.append(
                    (
                        (base_index, uv_base, normal_index),
                        (base_index + 2, uv_base + 2, normal_index),
                        (base_index + 3, uv_base + 3, normal_index),
                        normal_index,
                    )
                )

    for x, y, z in vertices:
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    for u, v in uvs:
        lines.append(f"vt {u:.6f} {v:.6f}")
    for x, y, z in normals:
        lines.append(f"vn {x:.6f} {y:.6f} {z:.6f}")
    for triangle in triangles:
        a, b, c, _ = triangle
        lines.append(
            "f "
            + " ".join(f"{v}/{vt}/{vn}" for v, vt, vn in (a, b, c))
        )

    return "\n".join(lines) + "\n"


def bounds(geometry: Geometry) -> tuple[_Vector, _Vector]:
    """Axis aligned bounds of the baked model, in Bedrock units."""
    matrices = bone_matrices(geometry)
    lower = [float("inf")] * 3
    upper = [float("-inf")] * 3
    for bone in geometry.bones:
        world = matrices[bone.name]
        for cube in bone.cubes:
            cube_world = _cube_world(geometry, cube, world)
            for face in FACES:
                for corner in _face_corners(cube, face):
                    point = apply(cube_world, corner)
                    for axis in range(3):
                        lower[axis] = min(lower[axis], point[axis])
                        upper[axis] = max(upper[axis], point[axis])
    if lower[0] == float("inf"):
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    return (lower[0], lower[1], lower[2]), (upper[0], upper[1], upper[2])


def bounds_blocks(geometry: Geometry) -> tuple[_Vector, _Vector]:
    """Baked bounds divided by 16, ready for collision boxes."""
    lower, upper = bounds(geometry)
    return (
        tuple(component / BEDROCK_UNITS for component in lower),  # type: ignore[return-value]
        tuple(component / BEDROCK_UNITS for component in upper),  # type: ignore[return-value]
    )


# --------------------------------------------------------------------------
# blockymodel export (Hytale)
# --------------------------------------------------------------------------


def _quaternion(degrees: _Vector) -> dict[str, float]:
    """Euler XYZ degrees (Z, Y, X application) to a Hytale quaternion."""
    import math

    rx, ry, rz = (math.radians(component) / 2.0 for component in degrees)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)

    # q = qz * qy * qx
    w1 = cz * cy * cx + sz * sy * sx
    x1 = cz * cy * sx - sz * sy * cx
    y1 = cz * sy * cx + sz * cy * sx
    z1 = sz * cy * cx - cz * sy * sx
    return {"x": round(x1, 6), "y": round(y1, 6), "z": round(z1, 6), "w": round(w1, 6)}


def _hytale_point(point: _Vector) -> dict[str, float]:
    factor = HYTALE_UNITS / BEDROCK_UNITS
    return {
        "x": round(point[0] * factor, 4),
        "y": round(point[1] * factor, 4),
        "z": round(point[2] * factor, 4),
    }


def _hytale_texture_layout(cube: Cube) -> dict[str, Any]:
    layout = _face_layout(cube)
    # Hytale and Bedrock share the same face naming for front/back/left/right
    # relative to the model, so the mapping is direct.
    result: dict[str, Any] = {}
    for face in ("up", "down", "north", "south", "east", "west"):
        if face not in layout:
            continue
        u, v, width, height = layout[face]
        result[face] = {
            "offset": {"x": round(abs(u), 2), "y": round(abs(v), 2)},
            "mirror": {"x": width < 0, "y": height < 0},
            "angle": 0,
        }
    return result


def _hytale_cube_node(cube: Cube, identifier: str, bone_pivot: _Vector) -> dict[str, Any]:
    pivot = _cube_pivot(cube)
    center = (
        cube.origin[0] + cube.size[0] / 2.0,
        cube.origin[1] + cube.size[1] / 2.0,
        cube.origin[2] + cube.size[2] / 2.0,
    )
    offset = (
        center[0] - pivot[0],
        center[1] - pivot[1],
        center[2] - pivot[2],
    )
    layout = _hytale_texture_layout(cube)
    return {
        "id": identifier,
        "name": "cube",
        "position": _hytale_point(
            (pivot[0] - bone_pivot[0], pivot[1] - bone_pivot[1], pivot[2] - bone_pivot[2])
        ),
        "orientation": _quaternion(cube.rotation),
        "shape": {
            "type": "box",
            "offset": _hytale_point(offset),
            "stretch": {"x": 1.0, "y": 1.0, "z": 1.0},
            "settings": {"size": _hytale_point(cube.size)},
            "visible": True,
            "doubleSided": False,
            "shadingMode": "standard",
            "unwrapMode": "custom" if layout else "full",
            "textureLayout": layout,
        },
    }


def to_blockymodel(geometry: Geometry, *, name: str = "Model") -> dict[str, Any]:
    """Render the geometry as a Hytale ``.blockymodel`` document."""
    counter = [0]

    def next_id() -> str:
        counter[0] += 1
        return str(counter[0])

    def none_shape() -> dict[str, Any]:
        return {
            "type": "none",
            "offset": {"x": 0.0, "y": 0.0, "z": 0.0},
            "stretch": {"x": 1.0, "y": 1.0, "z": 1.0},
            "settings": {"isPiece": True},
            "visible": True,
            "doubleSided": False,
            "shadingMode": "standard",
            "unwrapMode": "custom",
            "textureLayout": {},
        }

    bones = {bone.name: bone for bone in geometry.bones}
    children: dict[str, list[dict[str, Any]]] = {name: [] for name in bones}
    roots: list[str] = []

    def bone_node(bone: Bone) -> dict[str, Any]:
        parent = bones.get(bone.parent or "")
        parent_pivot = parent.pivot if parent is not None else (0.0, 0.0, 0.0)
        node: dict[str, Any] = {
            "id": next_id(),
            "name": bone.name,
            "position": _hytale_point(
                (bone.pivot[0] - parent_pivot[0], bone.pivot[1] - parent_pivot[1], bone.pivot[2] - parent_pivot[2])
            ),
            "orientation": _quaternion(bone.rotation),
            "shape": none_shape(),
            "children": [],
        }
        for cube in bone.cubes:
            node["children"].append(_hytale_cube_node(cube, next_id(), bone.pivot))
        for child_name in children.get(bone.name, []):
            node["children"].append(bone_node(bones[child_name]))
        if not node["children"]:
            node.pop("children")
        return node

    for bone in geometry.bones:
        parent = bones.get(bone.parent or "")
        if parent is None:
            roots.append(bone.name)
        else:
            children[parent.name].append(bone.name)

    return {
        "lod": "auto",
        "format": "character",
        "nodes": [bone_node(bones[root]) for root in roots] or [],
    }


# --------------------------------------------------------------------------
# Small helpers shared with the converter
# --------------------------------------------------------------------------


def texture_size(geometry: Geometry) -> tuple[int, int]:
    return geometry.texture_width, geometry.texture_height


def placeholder_geometry(
    width: float = 0.6, height: float = 1.8, *, name: str = "entity"
) -> Geometry:
    """A single box with a sensible atlas, for entities without a model."""
    size_x = max(1.0, round(width * BEDROCK_UNITS))
    size_y = max(1.0, round(height * BEDROCK_UNITS))
    size_z = size_x
    return Geometry(
        identifier=f"geometry.{name}",
        texture_width=64,
        texture_height=64,
        visible_width=max(1.0, width),
        visible_height=max(1.0, height),
        bones=[
            Bone(
                name="body",
                cubes=[
                    Cube(
                        origin=(-size_x / 2.0, 0.0, -size_z / 2.0),
                        size=(size_x, size_y, size_z),
                        uv=(0.0, 0.0),
                    )
                ],
            )
        ],
    )


def all_bone_names(geometry: Geometry) -> Iterable[str]:
    return (bone.name for bone in geometry.bones)
