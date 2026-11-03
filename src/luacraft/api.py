"""The portable runtime API of LuaCraft.

Every primitive the author can call is declared once, here, together with the
capability level it reaches in each edition.  Backends consult this table; the
analyser consults it to raise warnings and errors.

Capability levels
-----------------

``full``
    Supported natively and with the same observable semantics.

``partial``
    Supported, but the implementation has to be approximated.  Compiling for
    that edition emits a warning explaining the difference.

``none``
    Not available.  Compiling for that edition is an error unless the call
    sits inside a target specific hook.

The point of this table is that portability is decided *by construction*: a
primitive only becomes part of the portable API once it reaches ``full`` in
both editions.  Everything else is reported, loudly, at build time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class Edition(Enum):
    """The target editions LuaCraft can compile for."""

    JAVA = "java"
    BEDROCK = "bedrock"
    LUANTI = "luanti"

    @property
    def label(self) -> str:
        if self is Edition.JAVA:
            return "Java Edition"
        if self is Edition.BEDROCK:
            return "Bedrock Edition"
        return "Luanti"

    @property
    def short(self) -> str:
        """A short name, used to build hook names such as ``onJavaOnly``."""
        if self is Edition.JAVA:
            return "Java"
        if self is Edition.BEDROCK:
            return "Bedrock"
        return "Luanti"

    @property
    def hook_suffix(self) -> str:
        """The suffix used by the edition specific hook."""
        if self is Edition.JAVA:
            return "JavaOnly"
        if self is Edition.BEDROCK:
            return "BedrockOnly"
        return "LuantiOnly"


class Capability(Enum):
    """How well a primitive is supported by an edition."""

    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"


@dataclass(frozen=True)
class Param:
    """A single parameter of a primitive."""

    name: str
    lua_type: str
    description: str = ""


@dataclass(frozen=True)
class Primitive:
    """One callable in the portable runtime API."""

    name: str
    summary: str
    params: tuple[Param, ...]
    #: Capability level per edition.
    capabilities: dict[Edition, Capability]
    #: Extra explanation used when a warning or error is raised.
    notes: dict[Edition, str] = field(default_factory=dict)
    #: Emitted by the Java backend as a plain mcfunction command.
    java_command: str = ""
    #: Emitted by the Bedrock backend as a Script API call.
    bedrock_call: str = ""
    #: Emitted by the Luanti backend as a core API call.
    luanti_call: str = ""
    #: Whether the primitive returns a value.
    returns_value: bool = False

    def capability(self, edition: Edition) -> Capability:
        return self.capabilities.get(edition, Capability.NONE)

    def is_portable(self) -> bool:
        """True when the primitive behaves identically in every edition."""
        return all(
            self.capability(edition) is Capability.FULL for edition in Edition
        )

    def signature(self) -> str:
        args = ", ".join(f"{p.name}: {p.lua_type}" for p in self.params)
        return f"{self.name}({args})"


# --------------------------------------------------------------------------
# The primitive table
# --------------------------------------------------------------------------

PRIMITIVES: tuple[Primitive, ...] = (
    Primitive(
        name="say",
        summary="Send a message to every player in the world.",
        params=(Param("message", "string"),),
        capabilities={
            Edition.JAVA: Capability.FULL,
            Edition.BEDROCK: Capability.FULL,
            Edition.LUANTI: Capability.FULL,
        },
        java_command="say",
        bedrock_call="broadcast",
        luanti_call="chat_send_all",
    ),
    Primitive(
        name="message",
        summary="Send a message to a single player.",
        params=(Param("player", "player"), Param("message", "string")),
        capabilities={
            Edition.JAVA: Capability.FULL,
            Edition.BEDROCK: Capability.FULL,
            Edition.LUANTI: Capability.FULL,
        },
        java_command="tellraw",
        bedrock_call="sendMessage",
        luanti_call="chat_send_player",
    ),
    Primitive(
        name="give",
        summary="Give items to a player.",
        params=(
            Param("player", "player"),
            Param("item", "string"),
            Param("count", "number?"),
            Param("components", "table?"),
        ),
        capabilities={
            Edition.JAVA: Capability.FULL,
            Edition.BEDROCK: Capability.FULL,
            Edition.LUANTI: Capability.FULL,
        },
        java_command="give",
        bedrock_call="addItem",
        luanti_call="get_inventory().add_item",
    ),
    Primitive(
        name="clear",
        summary="Remove items from a player's inventory.",
        params=(
            Param("player", "player"),
            Param("item", "string?"),
            Param("count", "number?"),
        ),
        capabilities={
            Edition.JAVA: Capability.FULL,
            Edition.BEDROCK: Capability.FULL,
            Edition.LUANTI: Capability.FULL,
        },
        java_command="clear",
        bedrock_call="removeItem",
        luanti_call="get_inventory().clear",
    ),
    Primitive(
        name="score",
        summary="Read or modify a scoreboard objective.",
        params=(
            Param("player", "player"),
            Param("objective", "string"),
            Param("operation", "string?"),
            Param("value", "number?"),
        ),
        capabilities={
            Edition.JAVA: Capability.FULL,
            Edition.BEDROCK: Capability.FULL,
            Edition.LUANTI: Capability.PARTIAL,
        },
        notes={
            Edition.LUANTI: (
                "Luanti has no global scoreboard. The value is kept in the "
                "player's metadata, which is per player and does not survive "
                "a world reset."
            ),
        },
        java_command="scoreboard players set",
        bedrock_call="scoreboard",
        luanti_call="get_meta().set_int",
    ),
    Primitive(
        name="forEachPlayer",
        summary="Run a callback once per player currently online.",
        params=(Param("callback", "function"),),
        capabilities={
            Edition.JAVA: Capability.FULL,
            Edition.BEDROCK: Capability.FULL,
            Edition.LUANTI: Capability.FULL,
        },
        java_command="execute as @a",
        bedrock_call="getAllPlayers",
        luanti_call="get_connected_players",
    ),
    Primitive(
        name="forEachEntity",
        summary="Run a callback once per entity of a given type.",
        params=(Param("entity_type", "string"), Param("callback", "function")),
        capabilities={
            Edition.JAVA: Capability.FULL,
            Edition.BEDROCK: Capability.PARTIAL,
            Edition.LUANTI: Capability.PARTIAL,
        },
        notes={
            Edition.BEDROCK: (
                "Bedrock has no entity selector, so the callback receives "
                "entities fetched through a dimension query instead. Filtering "
                "happens in script rather than in the selector."
            ),
            Edition.LUANTI: (
                "Luanti has no entity type filter. Entities are found inside "
                "the area around the origin and filtered in script."
            ),
        },
        java_command="execute as @e[type=...]",
        bedrock_call="getEntities",
        luanti_call="get_objects_inside_radius",
    ),
    Primitive(
        name="setblock",
        summary="Place a block in the world.",
        params=(
            Param("x", "number"),
            Param("y", "number"),
            Param("z", "number"),
            Param("block", "string"),
        ),
        capabilities={
            Edition.JAVA: Capability.FULL,
            Edition.BEDROCK: Capability.PARTIAL,
            Edition.LUANTI: Capability.FULL,
        },
        notes={
            Edition.BEDROCK: (
                "Bedrock cannot place a block with arbitrary block states "
                "through the script API. The block is placed through a command "
                "and only plain block identifiers are supported."
            ),
        },
        java_command="setblock",
        bedrock_call="runCommand",
        luanti_call="set_node",
    ),
    Primitive(
        name="getBlock",
        summary="Read the block at a position.",
        params=(Param("x", "number"), Param("y", "number"), Param("z", "number")),
        capabilities={
            Edition.JAVA: Capability.FULL,
            Edition.BEDROCK: Capability.PARTIAL,
            Edition.LUANTI: Capability.FULL,
        },
        notes={
            Edition.BEDROCK: (
                "Bedrock can only read blocks through a command, which is "
                "slower and returns a text result rather than a block object."
            ),
        },
        java_command="data get block",
        bedrock_call="runCommand",
        luanti_call="get_node",
    ),
    Primitive(
        name="effect",
        summary="Apply a status effect to a player.",
        params=(
            Param("player", "player"),
            Param("effect", "string"),
            Param("seconds", "number?"),
            Param("amplifier", "number?"),
        ),
        capabilities={
            Edition.JAVA: Capability.FULL,
            Edition.BEDROCK: Capability.FULL,
            Edition.LUANTI: Capability.NONE,
        },
        notes={
            Edition.LUANTI: (
                "Luanti has no status effects. Use player attributes or a "
                "metadata flag and read it from the movement callback."
            ),
        },
        java_command="effect give",
        bedrock_call="addEffect",
    ),
    Primitive(
        name="playSound",
        summary="Play a sound for a player or in the world.",
        params=(
            Param("sound", "string"),
            Param("player", "player?"),
            Param("volume", "number?"),
            Param("pitch", "number?"),
        ),
        capabilities={
            Edition.JAVA: Capability.FULL,
            Edition.BEDROCK: Capability.FULL,
            Edition.LUANTI: Capability.PARTIAL,
        },
        notes={
            Edition.LUANTI: (
                "Luanti has no server side sound API. The sound is played "
                "client side through a chat message the client expands."
            ),
        },
        java_command="playsound",
        bedrock_call="playSound",
        luanti_call="chat_send_player",
    ),
    Primitive(
        name="teleport",
        summary="Move an entity to a position.",
        params=(
            Param("target", "entity"),
            Param("x", "number"),
            Param("y", "number"),
            Param("z", "number"),
        ),
        capabilities={
            Edition.JAVA: Capability.FULL,
            Edition.BEDROCK: Capability.FULL,
            Edition.LUANTI: Capability.FULL,
        },
        java_command="tp",
        bedrock_call="teleport",
        luanti_call="set_pos",
    ),
    Primitive(
        name="onTick",
        summary="Register a callback that runs once every game tick.",
        params=(Param("callback", "function"),),
        capabilities={
            Edition.JAVA: Capability.FULL,
            Edition.BEDROCK: Capability.FULL,
            Edition.LUANTI: Capability.FULL,
        },
        notes={
            Edition.LUANTI: (
                "Luanti runs the global step about ten times per second "
                "instead of twenty times per second, so the callback runs "
                "half as often."
            ),
        },
        java_command="#minecraft:tick",
        bedrock_call="system.runInterval",
        luanti_call="register_globalstep",
    ),
    Primitive(
        name="onJoin",
        summary="Register a callback that runs when a player joins.",
        params=(Param("callback", "function"),),
        capabilities={
            Edition.JAVA: Capability.FULL,
            Edition.BEDROCK: Capability.FULL,
            Edition.LUANTI: Capability.FULL,
        },
        java_command="#minecraft:load",
        bedrock_call="playerSpawn",
        luanti_call="register_on_joinplayer",
    ),
    Primitive(
        name="addObjective",
        summary="Declare a scoreboard objective.",
        params=(
            Param("name", "string"),
            Param("criteria", "string?"),
            Param("display", "string?"),
        ),
        capabilities={
            Edition.JAVA: Capability.FULL,
            Edition.BEDROCK: Capability.FULL,
            Edition.LUANTI: Capability.PARTIAL,
        },
        notes={
            Edition.LUANTI: (
                "Luanti has no scoreboard objectives. The value is stored in "
                "the player's metadata, so no declaration is needed and the "
                "call is dropped from the generated mod."
            ),
        },
        java_command="scoreboard objectives add",
        bedrock_call="scoreboard",
    ),
    Primitive(
        name="setDynamicProperty",
        summary="Store a value on an entity using dynamic properties.",
        params=(
            Param("target", "entity"),
            Param("key", "string"),
            Param("value", "any"),
        ),
        capabilities={
            Edition.JAVA: Capability.NONE,
            Edition.BEDROCK: Capability.FULL,
            Edition.LUANTI: Capability.FULL,
        },
        notes={
            Edition.JAVA: (
                "Java Edition has no equivalent to dynamic properties. Use "
                "scoreboards or NBT storage instead."
            ),
        },
        bedrock_call="setDynamicProperty",
        luanti_call="get_meta().set_string",
    ),
    Primitive(
        name="getDynamicProperty",
        summary="Read a value stored with setDynamicProperty.",
        params=(Param("target", "entity"), Param("key", "string")),
        capabilities={
            Edition.JAVA: Capability.NONE,
            Edition.BEDROCK: Capability.FULL,
            Edition.LUANTI: Capability.FULL,
        },
        notes={
            Edition.JAVA: (
                "Java Edition has no equivalent to dynamic properties."
            ),
        },
        bedrock_call="getDynamicProperty",
        luanti_call="get_meta().get_string",
    ),
    Primitive(
        name="nbt",
        summary="Read or write arbitrary NBT data on an entity.",
        params=(
            Param("target", "entity"),
            Param("path", "string"),
            Param("value", "any"),
        ),
        capabilities={
            Edition.JAVA: Capability.FULL,
            Edition.BEDROCK: Capability.NONE,
            Edition.LUANTI: Capability.NONE,
        },
        notes={
            Edition.BEDROCK: (
                "Bedrock Edition has no general purpose NBT access from the "
                "script API. Use setDynamicProperty for simple values."
            ),
            Edition.LUANTI: (
                "Luanti has no NBT. Use setDynamicProperty, which writes to "
                "the entity metadata."
            ),
        },
        java_command="data modify entity",
    ),
    Primitive(
        name="runCommand",
        summary="Run a raw command in the world.",
        params=(Param("command", "string"),),
        capabilities={
            Edition.JAVA: Capability.FULL,
            Edition.BEDROCK: Capability.FULL,
            Edition.LUANTI: Capability.PARTIAL,
        },
        notes={
            Edition.LUANTI: (
                "Luanti commands are not the same as Minecraft commands. The "
                "command is registered as a chat command instead."
            ),
        },
        java_command="<raw>",
        bedrock_call="runCommand",
        luanti_call="register_chatcommand",
    ),
)

#: Primitives indexed by name.
PRIMITIVE_BY_NAME: dict[str, Primitive] = {p.name: p for p in PRIMITIVES}

#: Primitives that exist in both editions with identical semantics.
PORTABLE_PRIMITIVES: tuple[Primitive, ...] = tuple(
    p for p in PRIMITIVES if p.is_portable()
)

#: Primitives available in exactly one edition.
EDITION_ONLY_PRIMITIVES: tuple[Primitive, ...] = tuple(
    p for p in PRIMITIVES if not p.is_portable()
)


def get_primitive(name: str) -> Primitive | None:
    """Look up a primitive by name."""
    return PRIMITIVE_BY_NAME.get(name)


def describe_capabilities() -> str:
    """Render the primitive table as a Markdown table (used by the docs)."""
    header = "| Primitive | Java | Bedrock | Notes |"
    divider = "|---|---|---|---|"
    rows = [header, divider]
    for prim in PRIMITIVES:
        java = prim.capability(Edition.JAVA).value
        bedrock = prim.capability(Edition.BEDROCK).value
        note = prim.notes.get(Edition.BEDROCK) or prim.notes.get(Edition.JAVA) or ""
        note = note.replace("\n", " ")
        rows.append(f"| `{prim.signature()}` | {java} | {bedrock} | {note} |")
    return "\n".join(rows)
