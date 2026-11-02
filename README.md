# LuaCraft

Write Minecraft mods once in Lua. Compile them for **Java Edition** and
**Bedrock Edition** with a single command.

```lua
onTick(function()
  forEachPlayer(function(player)
    score(player, "coins", "add", 1)
    if score(player, "coins") >= 100 then
      give(player, "luacraft:ruby", 1)
      score(player, "coins", "remove", 100)
      message(player, "¡Has ganado una gema!")
    end
  end)
end)
```

That single file becomes a Java datapack **and** a Bedrock add-on.

---

## Why

There is no framework that spans both editions. Every "cross-platform" tool
that exists unifies Java *loaders* (Fabric, Forge, NeoForge, Quilt) and stops
there:

| Tool | Unifies | Bedrock? |
|---|---|---|
| [Architectury](https://architecturyapi.com/) | Fabric + Forge + NeoForge + Quilt | No |
| [Modkit](https://modkitmc.com/) | Loaders + multi-version Java | No |
| [Unified API](https://unified-api.dev/) | Fabric + NeoForge, data-driven | No |
| [Conium](https://github.com/cao-awa/Conium) | Datapacks + Kotlin scripts | Planned, unfinished |

Lua is the most widely used scripting language in game modding: Roblox,
Factorio, Don't Starve Together, Starbound, Garry's Mod, LÖVE and the core
Lua ecosystem all use it. Minecraft Bedrock's Script API is TypeScript, which
sits much closer to Lua than Java does. LuaCraft turns that into a single
authoring language with two compilation targets.

---

## Status

**v0.1.0 — alpha.** The parser, the analyser, both backends and the CLI work.
The portable API covers the common cases; the examples in `examples/` compile
to both editions today.

Not yet implemented: content generation (items, recipes, blocks and entities
are parsed but not emitted yet), a Lua runtime for the sandboxed `table`
syntax, NBT round-tripping, and a simulator to verify the generated commands
without launching the game.

---

## Install

No dependencies beyond the Python standard library, and the tool never
touches the network at runtime.

```bash
pip install -e .
```

Python 3.11 or newer.

---

## Usage

```bash
# Compile for Bedrock Edition
luacraft build mod.lua --target bedrock -o mod.mcaddon

# Compile for Java Edition
luacraft build mod.lua --target java -o mod.zip

# Report what each edition supports, without writing anything
luacraft check mod.lua --target both

# Show the portable API and its capabilities
luacraft api
```

### Options

| Option | Meaning |
|---|---|
| `-t, --target` | `java` or `bedrock` |
| `-o, --output` | Where to write the archive |
| `-n, --namespace` | Namespace of the generated pack, default `luacraft` |
| `--name` | Display name of the pack |
| `--pack-format` | Java `pack_format`, default `48` |
| `--engine-version` | Bedrock `min_engine_version`, default `1.21.0` |
| `--json` | Machine readable report, for CI |
| `--strict` | Exit with an error code when there were warnings |

---

## Warnings tell you what will be missing

Portability is decided by construction: each primitive declares what it
reaches in each edition. When a capability does not exist, or only exists
partially, LuaCraft says so at build time and points at the exact line.

```
$ luacraft check mod.lua --target both

== Java Edition ==
mod.lua:9: error: setDynamicProperty() is not available in Java Edition:
Java Edition has no equivalent to dynamic properties. Use scoreboards or NBT
storage instead.
    | setDynamicProperty(...)
    = hint: use the onBedrockOnly hook to keep this code edition specific

mod.lua:12: warning: forEachEntity() is only partially supported in Bedrock Edition
    | forEachEntity(...)
    = hint: Bedrock has no entity selector, so the callback receives entities
      fetched through a dimension query instead.

  3 warnings, 1 error
```

Errors mean the build cannot produce a working target. Warnings mean it can,
but the behaviour is approximated or a feature is dropped.

---

## The portable API

Every primitive declares a capability level per edition: `full`, `partial` or
`none`.

| Primitive | Java | Bedrock | Notes |
|---|---|---|---|
| `say(message)` | full | full | |
| `message(player, message)` | full | full | |
| `give(player, item, count?, components?)` | full | full | |
| `clear(player, item?, count?)` | full | full | |
| `score(player, objective, operation?, value?)` | full | full | |
| `forEachPlayer(callback)` | full | full | |
| `forEachEntity(entity_type, callback)` | full | partial | Bedrock filters in script, not in a selector |
| `setblock(x, y, z, block)` | full | partial | Bedrock cannot set arbitrary block states |
| `getBlock(x, y, z)` | full | partial | Bedrock reads through a command |
| `effect(player, effect, seconds?, amplifier?)` | full | full | |
| `playSound(sound, player?, volume?, pitch?)` | full | full | |
| `teleport(target, x, y, z)` | full | full | |
| `onTick(callback)` | full | full | |
| `onJoin(callback)` | full | full | |
| `addObjective(name, criteria?, display?)` | full | full | |
| `setDynamicProperty(target, key, value)` | none | full | Use scoreboards or NBT in Java |
| `getDynamicProperty(target, key)` | none | full | |
| `nbt(target, path, value)` | full | none | Use dynamic properties in Bedrock |
| `runCommand(command)` | full | full | |

Run `luacraft api` for the live table.

### Hooks

| Hook | Behaviour |
|---|---|
| `onTick(callback)` | Every game tick |
| `onJoin(callback)` | When a player joins |
| `onLoad(callback)` | When the world loads |
| `onJavaOnly(callback)` | Body is kept in the Java build only |
| `onBedrockOnly(callback)` | Body is kept in the Bedrock build only |

The edition specific hooks are how you write code that cannot be portable.
The other edition reports a warning naming the hook.

---

## How it works

```
        mod.lua
           │
      ┌────▼─────┐
      │  Parser  │  a Lua parser written from scratch, no dependencies
      └────┬─────┘
           │  AST
      ┌────▼──────────┐
      │   Analyser    │  capability checking, warnings with line numbers
      └────┬──────────┘
           │
   ┌───────┴────────┐
   ▼                ▼
┌─────────┐   ┌──────────────┐
│  Java   │   │   Bedrock    │
│ backend │   │   backend    │
└────┬────┘   └──────┬───────┘
     │               │
datapack.zip    add-on .mcaddon
```

The Java backend emits ordinary mcfunction commands, so the datapack loads in
vanilla Java Edition with no mod installed. The Bedrock backend emits
TypeScript for the `@minecraft/server` module, which the game compiles itself.

---

## Legal

LuaCraft is an unofficial tool and is not affiliated with Mojang Studios or
Microsoft. It ships no game assets and downloads nothing; it only writes
files in the public pack formats, which you compile with your own account
and your own copy of the game.

The Minecraft formats and the Bedrock Script API are documented publicly by
their respective holders.

---

## Licence

MIT.
