"""Build, install and launch playable Minecraft worlds.

The bridge produces packs; a pack alone is not a world.  This module turns a
converted bundle (or a ``.mcaddon``) into world folders that the Java and
Bedrock clients can open, then installs and launches them when asked.

Everything stays local: the world files are assembled from the generated
arches, the NBT encoder in :mod:`luacraft.nbt` and an empty LevelDB database
for Bedrock.  No game files are downloaded and no third party runtime is
needed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import nbt
from .bedrock_leveldb import write_empty_db
from .content import Content, read_content
from .diagnostics import DiagnosticBag
from .luaparse import parse

#: Java Edition data version of 1.21.5.  Newer clients upgrade the world on
#: first load, which is safer than writing an unknown future data version.
JAVA_DATA_VERSION = 4325
JAVA_VERSION_NAME = "1.21.5"
JAVA_PACK_FORMAT = 48

#: Bedrock storage version used by every modern world.
BEDROCK_STORAGE_VERSION = 10
BEDROCK_VERSION = (1, 21, 0)

#: AppUserModelIds used to start the store editions from the command line.
JAVA_AUMID = "Microsoft.MinecraftJavaEdition_8wekyb3d8bbwe!Game"
BEDROCK_AUMID = "Microsoft.MinecraftUWP_8wekyb3d8bbwe!Game"

_BEDROCK_MANIFEST_TYPES = {"data": "behavior_pack", "resources": "resource_pack"}


# --------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------


@dataclass
class WorldBundle:
    """The archives and metadata a world is assembled from."""

    source: Path
    namespace: str
    display_name: str
    java_datapack: Path | None = None
    java_resources: Path | None = None
    bedrock_addon: Path | None = None
    content: Content | None = None
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        return {
            "source": str(self.source),
            "namespace": self.namespace,
            "name": self.display_name,
            "java_datapack": str(self.java_datapack) if self.java_datapack else None,
            "java_resources": str(self.java_resources) if self.java_resources else None,
            "bedrock_addon": str(self.bedrock_addon) if self.bedrock_addon else None,
            "items": len(self.content.items) if self.content else 0,
            "entities": len(self.content.entities) if self.content else 0,
            "warnings": list(self.warnings),
            "notes": list(self.notes),
        }


@dataclass
class WorldPlan:
    """A world folder that was written to disk."""

    edition: str
    name: str
    folder: str
    directory: Path
    namespace: str
    archives: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    installed_at: Path | None = None
    launched: bool = False
    launch_message: str = ""

    def summary(self) -> dict[str, object]:
        return {
            "edition": self.edition,
            "name": self.name,
            "folder": self.folder,
            "directory": str(self.directory),
            "namespace": self.namespace,
            "archives": [str(path) for path in self.archives],
            "warnings": list(self.warnings),
            "notes": list(self.notes),
            "installed_at": str(self.installed_at) if self.installed_at else None,
            "launched": self.launched,
            "launch_message": self.launch_message,
        }


# --------------------------------------------------------------------------
# Loading a bundle
# --------------------------------------------------------------------------


def load_bundle(
    source: Path,
    *,
    work_dir: Path | None = None,
    copy_textures: bool = False,
) -> WorldBundle:
    """Read a bridge output folder or convert a pack into one."""
    source = Path(source).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"{source} does not exist")

    if source.is_dir():
        root = source
        generated: list[str] = []
    else:
        root = work_dir or (source.parent / "_luacraft_world_source" / _stem(source))
        generated = _ensure_bridge(source, root, copy_textures=copy_textures)

    java = _find_java_pack(root)
    resources = _find_java_resources(root)
    addon = _find_addon(root)
    if java is None and addon is None:
        raise FileNotFoundError(
            f"{source} does not contain a Java datapack or a Bedrock add-on; "
            "point the command at a bridge output folder, a .mcaddon or a .mcpack"
        )

    metadata = _read_metadata(root)
    namespace = str(metadata.get("namespace") or "").strip()
    display = str(metadata.get("pack_name") or metadata.get("name") or "").strip()

    if not namespace:
        namespace = _infer_namespace(java, addon) or "luacraft"
    namespace = _safe_namespace(namespace)
    if not display:
        display = _display_from_archives(java, addon) or "LuaCraft World"

    content, warnings = _read_content(root, namespace)
    bundle = WorldBundle(
        source=source,
        namespace=namespace,
        display_name=display,
        java_datapack=java,
        java_resources=resources,
        bedrock_addon=addon,
        content=content,
        warnings=warnings,
    )
    if generated:
        bundle.notes.extend(generated)
    return bundle


def _ensure_bridge(pack: Path, root: Path, *, copy_textures: bool) -> list[str]:
    """Convert ``pack`` with the bridge when no sibling archives exist."""
    if _find_java_pack(root) is not None and _find_addon(root) is not None:
        return []
    from .bridge import run_bridge

    result = run_bridge(
        pack,
        root,
        targets="java,bedrock",
        copy_textures=copy_textures,
    )
    return [f"converted {pack.name} into {root}" for _ in [result]]


def _stem(path: Path) -> str:
    name = path.name
    for suffix in (".mcaddon", ".mcpack", ".zip"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return path.stem or "pack"


def _find_java_pack(root: Path) -> Path | None:
    candidates = [
        path
        for path in root.rglob("*.zip")
        if path.name.lower().endswith(".zip")
        and "resource" not in path.name.lower()
        and ("-java" in path.name.lower() or path.name.lower() == "datapack.zip")
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: (len(p.parts), p.name))[0]


def _find_java_resources(root: Path) -> Path | None:
    candidates = [
        path
        for path in root.rglob("*.zip")
        if "resource" in path.name.lower() and "-java" in path.name.lower()
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: (len(p.parts), p.name))[0]


def _find_addon(root: Path) -> Path | None:
    candidates = list(root.rglob("*.mcaddon"))
    if candidates:
        return sorted(candidates, key=lambda p: (len(p.parts), p.name))[0]
    candidates = list(root.rglob("*.mcpack"))
    return sorted(candidates, key=lambda p: (len(p.parts), p.name))[0] if candidates else None


def _read_metadata(root: Path) -> dict:
    for name in ("bridge.json", "conversion.json"):
        for path in (root / name, root / "dist" / name):
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(data, dict):
                    return data
    return {}


def _infer_namespace(java: Path | None, addon: Path | None) -> str | None:
    if java is not None:
        try:
            with zipfile.ZipFile(java) as archive:
                for name in archive.namelist():
                    match = re.match(r"data/([^/]+)/function/", name)
                    if match:
                        return match.group(1)
        except zipfile.BadZipFile:
            pass
    if addon is not None:
        try:
            with zipfile.ZipFile(addon) as archive:
                for name in archive.namelist():
                    if name.endswith("manifest.json") and "/" in name:
                        return name.split("/", 1)[0].removesuffix("_bp").removesuffix("_rp")
        except zipfile.BadZipFile:
            pass
    return None


def _display_from_archives(java: Path | None, addon: Path | None) -> str | None:
    if addon is None:
        return None
    try:
        with zipfile.ZipFile(addon) as archive:
            for name in archive.namelist():
                if name.endswith("manifest.json") and "/" in name:
                    manifest = json.loads(archive.read(name))
                    header = manifest.get("header", {})
                    title = header.get("name")
                    if title:
                        return str(title).split(" (")[0]
    except (zipfile.BadZipFile, ValueError, KeyError):
        pass
    return None


def _read_content(root: Path, namespace: str) -> tuple[Content | None, list[str]]:
    source = root / "mod.lua"
    if not source.is_file():
        return None, []
    try:
        chunk = parse(source.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        return None, [f"could not read {source}: {exc}"]
    content = read_content(chunk.body, namespace, DiagnosticBag())
    return content, []


# --------------------------------------------------------------------------
# Java world
# --------------------------------------------------------------------------


def build_java_world(
    bundle: WorldBundle,
    output_dir: Path,
    *,
    world_name: str | None = None,
    seed: int | None = None,
    force: bool = False,
) -> WorldPlan:
    """Create ``<output>/<world>`` with a level.dat and the packs."""
    if bundle.java_datapack is None:
        raise ValueError(
            "the bundle has no Java datapack; run the bridge with the java target"
        )
    name = world_name or bundle.display_name or "LuaCraft World"
    folder = _safe_folder(name, fallback="LuaCraft World")
    directory = Path(output_dir) / "java" / folder
    _prepare_directory(directory, force=force)

    datapacks = directory / "datapacks"
    datapacks.mkdir(parents=True)
    datapack_name = f"{bundle.namespace}.zip"
    _augment_java_datapack(
        bundle.java_datapack,
        datapacks / datapack_name,
        namespace=bundle.namespace,
        content=bundle.content,
        title=name,
    )

    resource_name: str | None = None
    if bundle.java_resources is not None:
        resource_name = f"{bundle.namespace}-resources.zip"
        resource_dir = directory / "resourcepacks"
        resource_dir.mkdir()
        shutil.copy2(bundle.java_resources, resource_dir / resource_name)

    level = java_level_dat(
        name,
        seed=seed,
        datapack=datapack_name,
        resourcepack=resource_name,
    )
    (directory / "level.dat").write_bytes(nbt.encode_java_level(level))

    plan = WorldPlan(
        edition="java",
        name=name,
        folder=folder,
        directory=directory,
        namespace=bundle.namespace,
        archives=[archive for archive in (bundle.java_datapack, bundle.java_resources) if archive],
        warnings=list(bundle.warnings),
    )
    plan.notes.append(f"datapack: {datapack_name}")
    if resource_name:
        plan.notes.append(f"resource pack: {resource_name}")
    plan.notes.append(
        "Minecraft Java creates the region files on first load; DataVersion "
        f"{JAVA_DATA_VERSION} ({JAVA_VERSION_NAME}) is upgraded by newer clients"
    )
    _write_world_json(directory, plan)
    return plan


def java_level_dat(
    name: str,
    *,
    seed: int | None = None,
    datapack: str | None = None,
    resourcepack: str | None = None,
) -> nbt.Compound:
    """Build the ``Data`` compound of a Java ``level.dat``."""
    seed = seed if seed is not None else _default_seed()
    enabled = [nbt.String("vanilla")]
    if datapack:
        enabled.append(nbt.String(f"file/{datapack}"))

    dimensions = nbt.Compound(
        {
            "minecraft:overworld": nbt.Compound(
                {
                    "type": nbt.String("minecraft:overworld"),
                    "generator": _noise_generator("overworld", "overworld"),
                }
            ),
            "minecraft:the_nether": nbt.Compound(
                {
                    "type": nbt.String("minecraft:the_nether"),
                    "generator": _noise_generator("nether", "nether"),
                }
            ),
            "minecraft:the_end": nbt.Compound(
                {
                    "type": nbt.String("minecraft:the_end"),
                    "generator": nbt.Compound(
                        {
                            "type": nbt.String("minecraft:noise"),
                            "settings": nbt.String("minecraft:end"),
                            "biome_source": nbt.Compound(
                                {"type": nbt.String("minecraft:the_end")}
                            ),
                        }
                    ),
                }
            ),
        }
    )

    data = nbt.Compound(
        {
            "DataVersion": nbt.Int(JAVA_DATA_VERSION),
            "version": nbt.Int(19133),
            "Version": nbt.Compound(
                {
                    "Id": nbt.Int(JAVA_DATA_VERSION),
                    "Name": nbt.String(JAVA_VERSION_NAME),
                    "Series": nbt.String("main"),
                    "Snapshot": nbt.Byte(0),
                }
            ),
            "LevelName": nbt.String(name),
            "LastPlayed": nbt.Long(int(time.time() * 1000)),
            "GameType": nbt.Int(1),
            "hardcore": nbt.Byte(0),
            "Difficulty": nbt.Byte(1),
            "DifficultyLocked": nbt.Byte(0),
            "allowCommands": nbt.Byte(1),
            "initialized": nbt.Byte(1),
            "Time": nbt.Long(0),
            "DayTime": nbt.Long(6000),
            "clearWeatherTime": nbt.Int(0),
            "rainTime": nbt.Int(0),
            "raining": nbt.Byte(0),
            "thunderTime": nbt.Int(0),
            "thundering": nbt.Byte(0),
            "SpawnX": nbt.Int(0),
            "SpawnY": nbt.Int(100),
            "SpawnZ": nbt.Int(0),
            "SpawnAngle": nbt.Float(0.0),
            "BorderCenterX": nbt.Double(0.0),
            "BorderCenterZ": nbt.Double(0.0),
            "BorderSize": nbt.Double(59999968.0),
            "BorderSizeLerpTarget": nbt.Double(59999968.0),
            "BorderSizeLerpTime": nbt.Long(0),
            "BorderDamagePerBlock": nbt.Double(0.2),
            "BorderSafeZone": nbt.Double(5.0),
            "BorderWarningBlocks": nbt.Double(5.0),
            "BorderWarningTime": nbt.Double(15.0),
            "WanderingTraderSpawnChance": nbt.Int(25),
            "WanderingTraderSpawnDelay": nbt.Int(24000),
            "ServerBrands": nbt.List((nbt.String("vanilla"),), nbt.TAG_STRING),
            "DataPacks": nbt.Compound(
                {
                    "Enabled": nbt.List(tuple(enabled), nbt.TAG_STRING),
                    "Disabled": nbt.List((), nbt.TAG_STRING),
                }
            ),
            "WorldGenSettings": nbt.Compound(
                {
                    "seed": nbt.Long(seed),
                    "generate_features": nbt.Byte(1),
                    "bonus_chest": nbt.Byte(0),
                    "dimensions": dimensions,
                }
            ),
            "Player": _java_player(),
        }
    )
    return nbt.Compound({"Data": data})


def _noise_generator(settings: str, preset: str) -> nbt.Compound:
    return nbt.Compound(
        {
            "type": nbt.String("minecraft:noise"),
            "settings": nbt.String(f"minecraft:{settings}"),
            "biome_source": nbt.Compound(
                {
                    "type": nbt.String("minecraft:multi_noise"),
                    "preset": nbt.String(f"minecraft:{preset}"),
                }
            ),
        }
    )


def _java_player() -> nbt.Compound:
    return nbt.Compound(
        {
            "Health": nbt.Float(20.0),
            "foodLevel": nbt.Int(20),
            "foodSaturationLevel": nbt.Float(5.0),
            "foodExhaustionLevel": nbt.Float(0.0),
            "Dimension": nbt.String("minecraft:overworld"),
            "Pos": nbt.List(
                (nbt.Double(0.5), nbt.Double(100.0), nbt.Double(0.5)),
                nbt.TAG_DOUBLE,
            ),
            "Rotation": nbt.List(
                (nbt.Float(0.0), nbt.Float(0.0)), nbt.TAG_FLOAT
            ),
            "Motion": nbt.List(
                (nbt.Double(0.0), nbt.Double(0.0), nbt.Double(0.0)),
                nbt.TAG_DOUBLE,
            ),
            "OnGround": nbt.Byte(0),
            "Inventory": nbt.List((), nbt.TAG_END),
            "SelectedItemSlot": nbt.Int(0),
            "XpLevel": nbt.Int(0),
            "XpP": nbt.Float(0.0),
            "XpTotal": nbt.Int(0),
            "Score": nbt.Int(0),
            "playerGameType": nbt.Int(1),
        }
    )


def _augment_java_datapack(
    source: Path,
    destination: Path,
    *,
    namespace: str,
    content: Content | None,
    title: str,
) -> None:
    """Copy the datapack and add the world-only welcome and starter kit."""
    with zipfile.ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}

    meta = _read_json_bytes(entries.get("pack.mcmeta"))
    if isinstance(meta, dict):
        pack = meta.setdefault("pack", {})
        if isinstance(pack, dict):
            pack.setdefault("supported_formats", {"min_inclusive": 15, "max_inclusive": 999})
            entries["pack.mcmeta"] = _json_bytes(meta)

    function_dir = f"data/{namespace}/function"
    welcome = [
        'tellraw @a {"text":' + json.dumps(title) + ',"color":"gold"}',
        'tellraw @a {"text":"LuaCraft world ready. Run /function '
        + namespace
        + ':kit for the generated item set.","color":"gray"}',
    ]
    entries[f"{function_dir}/_luacraft_welcome.mcfunction"] = (
        "\n".join(welcome) + "\n"
    ).encode("utf-8")

    kit = _kit_commands(content, namespace)
    if kit:
        entries[f"{function_dir}/kit.mcfunction"] = ("\n".join(kit) + "\n").encode("utf-8")

    tag_path = "data/minecraft/tags/function/load.json"
    tag = _read_json_bytes(entries.get(tag_path))
    values: list[str] = []
    if isinstance(tag, dict) and isinstance(tag.get("values"), list):
        values = [value for value in tag["values"] if isinstance(value, str)]
    for value in (f"{namespace}:_luacraft_welcome", f"{namespace}:_objectives"):
        if value not in values and (value.endswith("_welcome") or _entry_exists(entries, value, namespace)):
            values.append(value)
    entries[tag_path] = _json_bytes({"values": values})

    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(entries):
            archive.writestr(name, entries[name])


def _entry_exists(entries: dict[str, bytes], value: str, namespace: str) -> bool:
    function = value.split(":", 1)[-1]
    return f"data/{namespace}/function/{function}.mcfunction" in entries


def _kit_commands(content: Content | None, namespace: str) -> list[str]:
    if content is None:
        return []
    commands: list[str] = []
    for item in content.items.values():
        base = item.base_item or "minecraft:stick"
        commands.append(
            f'give @s {base}[minecraft:item_model="{namespace}:{item.name}"] 1'
        )
    return commands


# --------------------------------------------------------------------------
# Bedrock world
# --------------------------------------------------------------------------


def build_bedrock_world(
    bundle: WorldBundle,
    output_dir: Path,
    *,
    world_name: str | None = None,
    seed: int | None = None,
    force: bool = False,
) -> WorldPlan:
    """Create ``<output>/<world>`` with level.dat, packs and a LevelDB."""
    if bundle.bedrock_addon is None:
        raise ValueError(
            "the bundle has no Bedrock add-on; run the bridge with the bedrock target"
        )
    name = world_name or bundle.display_name or "LuaCraft World"
    folder = _safe_folder(name, fallback="LuaCraft World")
    directory = Path(output_dir) / "bedrock" / folder
    _prepare_directory(directory, force=force)

    packs = _extract_bedrock_packs(
        bundle.bedrock_addon, directory, namespace=bundle.namespace
    )
    if not packs:
        raise ValueError(f"{bundle.bedrock_addon} has no behaviour or resource pack")

    for kind, extracted in packs.items():
        if kind == "behavior_pack":
            _add_bedrock_kit(extracted, bundle.content)
        manifest = _read_json(extracted / "manifest.json")
        header = manifest.get("header", {}) if isinstance(manifest, dict) else {}
        pack_id = header.get("uuid")
        version = header.get("version") or list(BEDROCK_VERSION)
        if not pack_id:
            continue
        activation = {"pack_id": pack_id, "version": version}
        target = "world_behavior_packs.json" if kind == "behavior_pack" else "world_resource_packs.json"
        (directory / target).write_text(
            json.dumps([activation], indent=2) + "\n", encoding="utf-8"
        )

    level = bedrock_level_dat(name, seed=seed if seed is not None else _default_seed())
    (directory / "level.dat").write_bytes(
        nbt.encode_bedrock_level(level, storage_version=BEDROCK_STORAGE_VERSION)
    )
    (directory / "levelname.txt").write_text(name + "\n", encoding="utf-8")
    write_empty_db(directory / "db")

    plan = WorldPlan(
        edition="bedrock",
        name=name,
        folder=folder,
        directory=directory,
        namespace=bundle.namespace,
        archives=[bundle.bedrock_addon],
        warnings=list(bundle.warnings),
    )
    plan.notes.append(
        "world-scoped behaviour and resource packs are declared in "
        "world_behavior_packs.json and world_resource_packs.json"
    )
    plan.notes.append(
        "the LevelDB database is empty; Bedrock generates the terrain on first load"
    )
    _write_world_json(directory, plan)
    return plan


def bedrock_level_dat(name: str, *, seed: int) -> nbt.Compound:
    """Build a Bedrock ``level.dat`` payload."""
    now = int(time.time())
    version = nbt.List(tuple(nbt.Int(part) for part in (*BEDROCK_VERSION, 0, 0)), nbt.TAG_INT)
    abilities = nbt.Compound(
        {
            "attackmobs": nbt.Byte(1),
            "attackplayers": nbt.Byte(1),
            "build": nbt.Byte(1),
            "doorsandswitches": nbt.Byte(1),
            "fly": nbt.Byte(1),
            "instabuild": nbt.Byte(1),
            "invulnerable": nbt.Byte(1),
            "lightning": nbt.Byte(1),
            "mine": nbt.Byte(1),
            "op": nbt.Byte(1),
            "opencontainers": nbt.Byte(1),
            "permissionsLevel": nbt.Int(2),
            "playerPermissionsLevel": nbt.Int(2),
            "teleport": nbt.Byte(1),
            "walkSpeed": nbt.Float(0.1),
            "flySpeed": nbt.Float(0.05),
        }
    )
    return nbt.Compound(
        {
            "LevelName": nbt.String(name),
            "RandomSeed": nbt.Long(seed),
            "LastPlayed": nbt.Long(now),
            "Time": nbt.Long(6000),
            "currentTick": nbt.Long(0),
            "StorageVersion": nbt.Int(BEDROCK_STORAGE_VERSION),
            "NetworkVersion": nbt.Int(712),
            "Generator": nbt.Int(1),
            "GameType": nbt.Int(1),
            "Difficulty": nbt.Int(1),
            "SpawnX": nbt.Int(0),
            "SpawnY": nbt.Int(64),
            "SpawnZ": nbt.Int(0),
            "SpawnV1V2": nbt.Int(0),
            "WorldVersion": nbt.Int(1),
            "InventoryVersion": nbt.String("1.21.0"),
            "baseGameVersion": nbt.String("1.21.0"),
            "MinimumCompatibleClientVersion": version,
            "lastOpenedWithVersion": version,
            "commandsEnabled": nbt.Byte(1),
            "cheatsEnabled": nbt.Byte(1),
            "hasBeenLoadedInCreative": nbt.Byte(1),
            "showcoordinates": nbt.Byte(1),
            "showtags": nbt.Byte(1),
            "showdeathmessages": nbt.Byte(1),
            "dodaylightcycle": nbt.Byte(1),
            "doweathercycle": nbt.Byte(1),
            "domobspawning": nbt.Byte(1),
            "domobloot": nbt.Byte(1),
            "doentitydrops": nbt.Byte(1),
            "dofiretick": nbt.Byte(1),
            "doinsomnia": nbt.Byte(1),
            "drowningdamage": nbt.Byte(1),
            "falldamage": nbt.Byte(1),
            "firedamage": nbt.Byte(1),
            "freezedamage": nbt.Byte(1),
            "naturalregeneration": nbt.Byte(1),
            "pvp": nbt.Byte(1),
            "spawnMobs": nbt.Byte(1),
            "mobgriefing": nbt.Byte(1),
            "tntexplodes": nbt.Byte(1),
            "respawnblocksexplode": nbt.Byte(1),
            "sendcommandfeedback": nbt.Byte(1),
            "commandblockoutput": nbt.Byte(1),
            "commandblocksenabled": nbt.Byte(1),
            "keepinventory": nbt.Byte(0),
            "immutableWorld": nbt.Byte(0),
            "bonusChestEnabled": nbt.Byte(0),
            "startWithMapEnabled": nbt.Byte(0),
            "texturePacksRequired": nbt.Byte(0),
            "educationFeaturesEnabled": nbt.Byte(0),
            "eduOffer": nbt.Int(0),
            "hasLockedBehaviorPack": nbt.Byte(0),
            "hasLockedResourcePack": nbt.Byte(0),
            "isFromLockedTemplate": nbt.Byte(0),
            "isFromWorldTemplate": nbt.Byte(0),
            "isSingleUseWorld": nbt.Byte(0),
            "isWorldTemplateOptionLocked": nbt.Byte(0),
            "requiresCopiedPackRemovalCheck": nbt.Byte(0),
            "useMsaGamertagsOnly": nbt.Byte(0),
            "LANBroadcast": nbt.Byte(1),
            "LANBroadcastIntent": nbt.Byte(1),
            "MultiplayerGame": nbt.Byte(1),
            "MultiplayerGameIntent": nbt.Byte(1),
            "XBLBroadcastIntent": nbt.Int(3),
            "PlatformBroadcastIntent": nbt.Int(3),
            "Platform": nbt.Int(2),
            "NetherScale": nbt.Int(8),
            "rainLevel": nbt.Float(0.0),
            "lightningLevel": nbt.Float(0.0),
            "rainTime": nbt.Int(0),
            "lightningTime": nbt.Int(0),
            "serverChunkTickRange": nbt.Int(4),
            "spawnradius": nbt.Int(10),
            "functioncommandlimit": nbt.Int(20000),
            "maxcommandchainlength": nbt.Int(65535),
            "randomtickspeed": nbt.Int(1),
            "CenterMapsToOrigin": nbt.Byte(0),
            "ConfirmedPlatformLockedContent": nbt.Byte(0),
            "ForceGameType": nbt.Byte(0),
            "LimitedWorldOriginX": nbt.Int(0),
            "LimitedWorldOriginY": nbt.Int(32767),
            "LimitedWorldOriginZ": nbt.Int(0),
            "limitedWorldDepth": nbt.Int(16),
            "limitedWorldWidth": nbt.Int(16),
            "worldStartCount": nbt.Long(1),
            "prid": nbt.String(""),
            "experiments": nbt.Compound({}),
            "world_policies": nbt.Compound({}),
            "abilities": abilities,
        }
    )


def _extract_bedrock_packs(
    addon: Path, world: Path, *, namespace: str = "luacraft"
) -> dict[str, Path]:
    """Extract the BP and RP of an add-on into the world folder."""
    extracted: dict[str, Path] = {}
    with zipfile.ZipFile(addon) as archive:
        names = archive.namelist()
        roots: dict[str, str] = {}
        for name in names:
            if not name.endswith("manifest.json") or "/" not in name:
                continue
            root = name.split("/", 1)[0]
            manifest = json.loads(archive.read(name))
            kind = _manifest_kind(manifest)
            if kind:
                roots[root] = kind
        if not roots:
            # A plain pack with manifest.json at the archive root.
            if "manifest.json" in names:
                manifest = json.loads(archive.read("manifest.json"))
                kind = _manifest_kind(manifest)
                if kind:
                    roots[""] = kind
        for root, kind in roots.items():
            suffix = "_bp" if kind == "behavior_pack" else "_rp"
            destination = world / ("behavior_packs" if kind == "behavior_pack" else "resource_packs")
            target = destination / f"{namespace}{suffix}"
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True)
            prefix = f"{root}/" if root else ""
            for name in names:
                if not name.startswith(prefix) or name.endswith("/"):
                    continue
                relative = name[len(prefix) :]
                if not relative:
                    continue
                out = target / relative
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(archive.read(name))
            extracted[kind] = target
    return extracted


def _manifest_kind(manifest: object) -> str | None:
    if not isinstance(manifest, dict):
        return None
    modules = manifest.get("modules")
    if isinstance(modules, list):
        for module in modules:
            if isinstance(module, dict):
                kind = _BEDROCK_MANIFEST_TYPES.get(module.get("type"))
                if kind:
                    return kind
    header = manifest.get("header")
    if isinstance(header, dict):
        name = str(header.get("name", "")).lower()
        if "behaviour" in name or "behavior" in name:
            return "behavior_pack"
        if "resource" in name:
            return "resource_pack"
    return None


def _add_bedrock_kit(pack: Path, content: Content | None) -> None:
    if content is None or not content.items:
        return
    function = pack / "functions" / "kit.mcfunction"
    function.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"give @s {item.identifier} 1" for item in content.items.values()]
    function.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Packaging and installation
# --------------------------------------------------------------------------


def pack_mcworld(world: WorldPlan | Path, output: Path) -> Path:
    """Zip a world folder as a ``.mcworld`` that Bedrock can import."""
    directory = world.directory if isinstance(world, WorldPlan) else Path(world)
    if not directory.is_dir():
        raise FileNotFoundError(f"{directory} is not a world folder")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(directory).as_posix())
    return output


def install_java(
    plan: WorldPlan,
    *,
    minecraft_dir: Path | None = None,
    force: bool = False,
) -> Path:
    """Copy a Java world into ``.minecraft/saves`` and enable its packs."""
    root = Path(minecraft_dir) if minecraft_dir else java_minecraft_dir()
    saves = root / "saves"
    saves.mkdir(parents=True, exist_ok=True)
    target = saves / plan.folder
    if target.exists():
        if not force:
            raise FileExistsError(
                f"{target} already exists; pass --force to replace it"
            )
        shutil.rmtree(target)
    shutil.copytree(plan.directory, target)

    packs: list[str] = []
    for archive in sorted((target / "resourcepacks").glob("*.zip")):
        destination = root / "resourcepacks" / archive.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, destination)
        packs.append(f"file/{archive.name}")
    _enable_resource_packs(root / "options.txt", packs)

    plan.installed_at = target
    return target


def install_bedrock(
    plan: WorldPlan,
    *,
    game_dir: Path | None = None,
    force: bool = False,
) -> Path:
    """Copy a Bedrock world into the active ``com.mojang/minecraftWorlds``."""
    root = Path(game_dir) if game_dir else bedrock_game_dir()
    worlds = root / "minecraftWorlds"
    worlds.mkdir(parents=True, exist_ok=True)
    target = worlds / plan.folder
    if target.exists():
        if not force:
            raise FileExistsError(
                f"{target} already exists; pass --force to replace it"
            )
        shutil.rmtree(target)
    shutil.copytree(plan.directory, target)
    plan.installed_at = target
    return target


def _enable_resource_packs(options: Path, packs: list[str]) -> None:
    if not packs:
        return
    text = options.read_text(encoding="utf-8", errors="replace") if options.is_file() else ""
    entries = _read_options(text)
    for key in ("resourcePacks", "incompatibleResourcePacks"):
        current = _option_json(entries.get(key), [])
        for pack in packs:
            if pack not in current:
                current.append(pack)
        entries[key] = json.dumps(current)
    _write_options(options, entries)


def _read_options(text: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        entries[key] = value
    return entries


def _option_json(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return list(default)
    try:
        parsed = json.loads(value)
    except ValueError:
        return list(default)
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return list(default)


def _write_options(path: Path, entries: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{key}:{value}" for key, value in entries.items())
    path.write_text(body + "\n", encoding="utf-8")


def launch_java(plan: WorldPlan) -> str:
    """Open the Java launcher; the official launcher owns the session."""
    if not java_game_installed():
        message = (
            "the Java Edition game files are not installed (only the launcher "
            "stub is present); install the game, then open the world list"
        )
        plan.launched = False
        plan.launch_message = message
        return message
    _open_aumid(JAVA_AUMID)
    message = (
        "opened the Minecraft Java launcher; select the world "
        f'"{plan.name}" in the world list'
    )
    plan.launched = True
    plan.launch_message = message
    return message


def launch_bedrock(plan: WorldPlan) -> str:
    """Open Minecraft for Windows; the world is in the world list."""
    _open_aumid(BEDROCK_AUMID)
    message = (
        "opened Minecraft for Windows; select the world "
        f'"{plan.name}" in the world list'
    )
    plan.launched = True
    plan.launch_message = message
    return message


def _open_aumid(aumid: str) -> None:
    if sys.platform == "win32":
        subprocess.Popen(
            ["explorer.exe", f"shell:AppsFolder\\{aumid}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    raise OSError("launching the store edition is only supported on Windows")


# --------------------------------------------------------------------------
# Local installation discovery
# --------------------------------------------------------------------------


def java_minecraft_dir() -> Path:
    override = os.environ.get("LUACRAFT_MINECRAFT_DIR")
    if override:
        return Path(override).expanduser()
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / ".minecraft"
    return Path.home() / ".minecraft"


def java_game_installed(minecraft_dir: Path | None = None) -> bool:
    root = Path(minecraft_dir) if minecraft_dir else java_minecraft_dir()
    versions = root / "versions"
    if not versions.is_dir():
        return False
    return any(
        (version / f"{version.name}.jar").is_file()
        for version in versions.iterdir()
        if version.is_dir()
    )


def bedrock_game_dirs() -> list[Path]:
    """Return every ``com.mojang`` folder an installed Bedrock client uses."""
    override = os.environ.get("LUACRAFT_BEDROCK_DIR")
    if override:
        return [Path(override).expanduser()]
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return []
    users = Path(appdata) / "Minecraft Bedrock" / "Users"
    if not users.is_dir():
        return []
    candidates: list[Path] = []
    active: list[Path] = []
    for user in users.iterdir():
        game = user / "games" / "com.mojang"
        if not (game / "minecraftWorlds").is_dir():
            continue
        (active if (game / "minecraftpe").is_dir() else candidates).append(game)
    return active + candidates


def bedrock_game_dir() -> Path:
    candidates = bedrock_game_dirs()
    if not candidates:
        raise FileNotFoundError(
            "no Minecraft Bedrock world folder was found; pass --game-dir or "
            "set LUACRAFT_BEDROCK_DIR"
        )
    return candidates[0]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _prepare_directory(directory: Path, *, force: bool) -> None:
    if directory.exists():
        if not force:
            raise FileExistsError(
                f"{directory} already exists; pass --force to replace it"
            )
        shutil.rmtree(directory)
    directory.mkdir(parents=True)


def _safe_folder(text: str, *, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
    return cleaned or fallback


def _safe_namespace(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]", "_", text.lower())
    return cleaned.strip("_") or "luacraft"


def _default_seed() -> int:
    return 424242


def _write_world_json(directory: Path, plan: WorldPlan) -> None:
    (directory / "luacraft-world.json").write_text(
        json.dumps(plan.summary(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _read_json_bytes(value: bytes | None) -> object:
    if value is None:
        return None
    try:
        return json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
