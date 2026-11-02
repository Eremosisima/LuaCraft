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

**v0.2.0 — alpha.** The parser, the analyser, both backends, the CLI and the
declarative content layer work. The examples in `examples/` compile to both
editions today.

Not yet implemented: NBT round-tripping, entities and loot tables, a Lua
runtime for the sandboxed `table` syntax, and a simulator to verify the
generated commands without launching the game.

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

## Declaring content

Content is declared with plain Lua tables. The same declaration produces an
item definition in Bedrock and a recipe in Java.

```lua
items.ruby = {
  id = "luacraft:ruby",
  max_stack = 1,
  texture = "ruby",
  display_name = "Ruby",
  category = "items",
}

items.ruby_sword = {
  id = "luacraft:ruby_sword",
  durability = 250,
  texture = "ruby_sword",
  category = "equipment",
  hand_equipped = true,
  glint = true,
}

blocks.ruby_block = {
  hardness = 3.0,
  resistance = 6.0,
  light_emission = 4,
  map_color = "#B00020",
  texture = "ruby_block",
}

recipes.ruby_sword = {
  type = "shaped",
  pattern = { "R", "R", "S" },
  key = { R = "luacraft:ruby", S = "minecraft:stick" },
  result = "luacraft:ruby_sword",
  group = "luacraft_tools",
}

recipes.ruby_block = {
  type = "shapeless",
  ingredients = { "luacraft:ruby_dust", "luacraft:ruby_dust" },
  result = "luacraft:ruby_block",
}
```

### Item fields

| Field | Bedrock | Java | Notes |
|---|---|---|---|
| `id` | ✅ | ⚠️ | Java has no item definitions in a datapack |
| `max_stack` | ✅ | ⚠️ | Becomes `minecraft:max_stack_size` |
| `durability` | ✅ | ⚠️ | Becomes `minecraft:durability` |
| `texture` | ✅ | ⚠️ | Java needs a resource pack |
| `display_name` | ✅ | ⚠️ | |
| `category` | ✅ | ❌ | `construction`, `equipment`, `items`, `nature`, `none` |
| `hand_equipped` | ✅ | ⚠️ | |
| `glint` | ✅ | ⚠️ | |
| `food` | ✅ | ⚠️ | Table passed to `minecraft:food` |
| `fuel` | ✅ | ⚠️ | Table passed to `minecraft:fuel` |
| `components` | ✅ | ⚠️ | Extra components, verbatim |
| `base` | ❌ | ✅ | The vanilla item Java falls back to |

### Recipe fields

| Field | Notes |
|---|---|
| `type` | `shaped` or `shapeless` |
| `pattern` | Rows of the crafting grid, for `shaped` |
| `key` | Pattern character to item, for `shaped` |
| `ingredients` | Items in any order, for `shapeless` |
| `result` | The item produced |
| `count` | How many the recipe produces |
| `group` | Recipe book group |
| `tags` | Crafting stations, for example `crafting_table` |

Identifiers without a namespace get the pack namespace, so `result = "ruby"`
and `result = "luacraft:ruby"` mean the same thing.

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

mod.lua:5: warning: Java Edition has no item definitions in a datapack, so the
custom item luacraft:ruby needs a resource pack to have its own texture and
model
    = hint: use minecraft:stick as the base item and ship a resource pack with
      a model and texture for it

  3 warnings, 1 error
```

Errors mean the build cannot produce a working target. Warnings mean it can,
but the behaviour is approximated or a feature is dropped.

The custom item case above is the clearest example of why one source cannot
cover both editions perfectly: **Bedrock has item definitions in the
behaviour pack, Java Edition has none in a datapack.** The recipe converts
cleanly. The item needs a resource pack on the Java side, and LuaCraft says
so instead of pretending otherwise.

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
      ┌────▼──────────┐
      │    Content    │  items, blocks and recipes become an
      │    reader     │  intermediate representation
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

Content renders differently on each side, and the difference is reported
rather than hidden. Recipes convert cleanly. Custom items are written as item
definitions in the Bedrock behaviour pack; on the Java side LuaCraft reports
that a resource pack is needed for the texture and model, because Java
datapacks have no item definitions at all.

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
