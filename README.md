# LuaCraft

Write Minecraft mods once in Lua. Compile them for **Java Edition**,
**Bedrock Edition** and **Luanti** with a single command, then re-host the same
content in **Hytale** asset packs and a native **Godot 4** project with a
selectable Vulkan or OpenGL renderer.

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

That single file becomes a Java datapack, a Bedrock add-on **and** a Luanti
mod. It can also be edited and compiled from a local GUI, from your editor
through the MCP server, and validated by a built-in sandbox before it ships.

---

## Why

There is no framework that spans all three editions. Every "cross-platform" tool
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

**v0.4.0 — alpha.** The parser, the analyser, the three backends, the CLI, the
GUI, the MCP server, the sandbox, the texture pipeline, the Bedrock bridge, the
Hytale asset-pack exporter and the Godot project exporter work. The examples in
`examples/` compile to all three Minecraft/Luanti editions today, with textures,
and every generated pack is validated before it is written.

Entities are real on the targets that support them: Luanti registers them with
OBJ geometry, Hytale gets NPC roles and `.blockymodel` assets, and Godot spawns
them as native 3D nodes. Java and Bedrock keep a species-card item as the proxy
because neither can add entities from a pack.

Not yet implemented: NBT round-tripping for Bedrock, a Lua runtime for the
sandboxed `table` syntax, and a simulator that executes generated commands
without launching the game. The sandbox itself checks structure and references,
not gameplay. Luanti can go one step further: `tools/validate_luanti_runtime.py`
installs the archive and runs a real headless server (Windows, Luanti 5.17+).
Hytale Java plugins are out of scope; Hytale packs and Godot projects are
validated statically, not launched.

---

## Validated against real packs

The bridge was run end to end on every Bedrock pack available locally. Each row
compiled for Java, Bedrock and Luanti, exported a Hytale asset pack and a Godot
project, and passed the offline sandbox on the in-memory build and on every
archive.

| Pack | Entities | OBJ + blockymodels | Items | Recipes | Hytale files | Godot files |
|---|---|---|---|---|---|---|
| Cobblemon (1074) | 1074 | 1074 | 0 | 0 | 5372 | 2155 |
| Fakemon Optimizado | 169 | 169 | 0 | 0 | 847 | 345 |
| Cobblemon sample | 3 | 3 | 0 | 0 | 17 | 13 |
| FaunaLibre | 64 | 64 | 1 | 1 | 326 | 136 |

`all_sandboxes_ok` and `all_archives_ok` were `True` for all four packs.

**Not available locally:** Cobbleverse and Anime Characters. The runner reports
them as `missing` and does not invent or substitute data.

### Launched inside Luanti 5.17.0

The Luanti archives were also installed and executed in a real Luanti 5.17.0
server using the `mineclonia` game, not just inspected statically.
`tools/validate_luanti_runtime.py` installs a `*-luanti.zip`, creates a
throwaway world, and runs a worldmod that checks the live Lua API, then spawns
one entity and confirms that it steps:

```bash
py tools/validate_luanti_runtime.py out/pmon-luanti.zip \
  --mod pmon --entity pmon:abomasnow --luanti "C:/.../luanti.exe"
```

| Mod | Entities registered | Item | Mesh + texture media | Entity spawned and stepped |
|---|---|---|---|---|
| Cobblemon | 1074 | — | yes | yes |
| Fakemon | 169 | — | yes | yes |
| Cobblemon sample | 3 | — | yes | yes |
| FaunaLibre | 64 | `faunalibre:fruta_lunar` | yes | yes |

All four reported `RESULT PASS` with no Lua traceback or `ModError`. The client
was launched on FaunaLibre as well; that exposed two real bugs, now fixed:

- `add_to_creative_inventory` is not a Luanti function. Every registered item
  already appears in the creative inventory, so the call was removed.
- Luanti announces media by **bare file name**, so a mesh declared as
  `models/benogrok_model.obj` can never be found by the client. The file still
  lives in `models/`, but the entity now declares `mesh = "benogrok_model.obj"`.
  Before the fix the client logged
  `GenericCAO::addToScene(): Could not load mesh`; after the fix it renders the
  generated model with its placeholder texture.

**Environment note:** the first client launch is blocked by a Windows Defender
Firewall prompt. Dismissing it (`Cancelar` is enough for loopback) lets the
local server and client connect. This is a host setting, not a LuaCraft issue.

**What this evidence does and does not prove:**

- The generated Minecraft/Luanti packs reload through the LuaCraft parser and
  the sandbox checks every JSON file, manifest, function, tag, identifier and
  texture reference.
- The Luanti mods were executed by a real server: entities, items, recipes,
  meshes, spawn ABMs and the chat command are registered, and one entity was
  spawned and moved under the generated AI.
- Hytale packs are schema-validated against the public 2026 asset documentation.
  No Hytale SDK or Java 25 runtime is installed here, so no Java plugin was
  compiled and the game did not load the pack.
- Godot projects are validated statically (project file, rendering method,
  scene, runtime script, per-entity model and texture). No Godot executable is
  installed here, so the project was not launched.

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

# Compile for Luanti
luacraft build mod.lua --target luanti -o mod.zip

# Compile every edition at once, with textures and sandbox validation
luacraft build mod.lua --target all --textures --sandbox --bundle out/

# Bridge a Bedrock add-on and export every target at once
luacraft bridge pack.mcaddon -o out/pack

# Compile a Lua source straight into a Godot 4 project or a Hytale asset pack
luacraft godot mod.lua -o out/godot --graphics vulkan
luacraft hytale mod.lua -o out/hytale.zip

# Report what each edition supports, without writing anything
luacraft check mod.lua --debug

# Show the portable API and its capabilities
luacraft api

# Open the local editor in a browser
luacraft gui

# Serve LuaCraft to an MCP client (OpenCode, Claude, any agent)
luacraft mcp
```

### Bridge a Bedrock add-on

```bash
# Convert a .mcaddon and export every target at once
luacraft bridge pack.mcaddon -o out/pack

# Only some targets, with an OpenGL Godot project
luacraft bridge pack.mcaddon -o out/pack --targets luanti,hytale,godot --graphics opengl

# Machine readable report of what was kept, approximated or lost
luacraft bridge pack.mcaddon -o out/pack --json
```

| Option | Meaning |
|---|---|
| `-o, --output` | Directory for `mod.lua`, textures, models and archives |
| `-n, --namespace` | Override the namespace inferred from the pack |
| `--name` | Display name of the converted pack |
| `--copy-textures` | Copy source textures instead of placeholders (licence permitting) |
| `--graphics` | Godot backend: `vulkan`, `vulkan-mobile` or `opengl` |
| `--targets` | Comma separated subset of `java,bedrock,luanti,hytale,godot` |
| `--json` | Print the bridge report as JSON |

### Options

| Option | Meaning |
|---|---|
| `-t, --target` | `java`, `bedrock`, `luanti` or `all` |
| `-o, --output` | Where to write the archive (a directory with `all`) |
| `-n, --namespace` | Namespace of the generated pack, default `luacraft` |
| `--name` | Display name of the pack |
| `--pack-format` | Java `pack_format`, default `48` |
| `--engine-version` | Bedrock `min_engine_version`, default `1.21.0` |
| `--textures` | Generate a placeholder texture for every declared item and block |
| `--texture-dir` | Folder of author supplied PNG files to use instead |
| `--resourcepack` | Separate output for the Java resource pack |
| `--bundle` | Directory for the archives when `--target all` |
| `--sandbox` | Validate every generated pack before writing it |
| `--debug` | Print the phase trace and timings to stderr |
| `--json` | Machine readable report, for CI |
| `--strict` | Exit with an error code when there were warnings |

`luacraft sandbox <archive|directory>` validates a pack that was already
built. `luacraft gui` and `luacraft mcp` accept `--debug` too.

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

| Field | Java | Bedrock | Luanti | Notes |
|---|---|---|---|---|
| `id` | ⚠️ | ✅ | ✅ | Java has no item definitions in a datapack |
| `max_stack` | ⚠️ | ✅ | ⚠️ | Becomes `minecraft:max_stack_size` where possible |
| `durability` | ⚠️ | ✅ | ⚠️ | Becomes `minecraft:durability` |
| `texture` | ⚠️ | ✅ | ✅ | Java needs a resource pack |
| `display_name` | ⚠️ | ✅ | ⚠️ | |
| `category` | ❌ | ✅ | ❌ | `construction`, `equipment`, `items`, `nature`, `none` |
| `hand_equipped` | ⚠️ | ✅ | ❌ | |
| `glint` | ⚠️ | ✅ | ❌ | |
| `food` | ⚠️ | ✅ | ❌ | Table passed to `minecraft:food` |
| `fuel` | ⚠️ | ✅ | ❌ | Table passed to `minecraft:fuel` |
| `components` | ⚠️ | ✅ | ❌ | Extra components, verbatim |
| `base` | ✅ | ❌ | ❌ | The vanilla item Java falls back to |

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

### Entity fields

Entities are declared with an `entities` table. The Bedrock bridge fills this
table from a source pack, but you can also write it by hand:

```lua
entities.benogrok = {
  id = "faunalibre:benogrok",
  display_name = "Benogrok",
  texture = "benogrok",
  model = "benogrok_model",
  health = 17,
  movement_speed = 0.22,
  collision_box = { width = 1.8, height = 0.69 },
  hostile = false,
  tameable = { probability = 0.33, tame_items = { "faunalibre:fruta_lunar" } },
  drops = {
    { item = "faunalibre:fruta_lunar", min = 1, max = 2, chance = 0.5 },
  },
  families = { "fauna", "benogrok" },
}
```

| Target | What an entity becomes |
|---|---|
| Luanti | `core.register_entity` with OBJ mesh, collision box, HP, taming, drops and a small wander/chase AI, plus a chat spawn command and an ABM approximation |
| Hytale | an NPC role, a server model, a drop table and a `Model.blockymodel` |
| Godot | a native 3D node with an OBJ mesh, texture, hitbox and name label |
| Bedrock | the species-card item proxy (a behaviour pack can add entities, but this backend does not author one) |
| Java | the species-card item proxy (a datapack cannot add entities) |

Models are exported as OBJ for Luanti/Godot and as `.blockymodel` for Hytale.
Bedrock geometry is 16 units per block, so OBJ divides by 16 and the Hytale
exporter multiplies by 2 (32 units per block).

---

## Bridging a Bedrock add-on

`luacraft bridge` reads a `.mcaddon`, `.mcpack`, `.zip` or unpacked pack and
runs the whole pipeline locally:

1. items, blocks, recipes and entities are parsed from the behaviour and
   resource packs,
2. geometry, textures, health, movement, collision, taming, loot drops and
   type families are carried into `mod.lua`,
3. Java, Bedrock and Luanti packs are compiled and sandbox-validated,
4. a Hytale asset pack and a Godot 4 project are written,
5. `bridge.json` records exactly what each target kept, approximated or lost.

```bash
luacraft bridge FaunaLibre.mcaddon -o out/faunalibre --copy-textures
```

Textures are **not** copied by default: LuaCraft writes deterministic
placeholders so third-party art is never redistributed silently. Pass
`--copy-textures` only when the source pack's licence allows it.

### Graphics backend for Godot

Godot chooses its rendering method when it loads a project, so the backend is
written into `project.godot`:

| `--graphics` | Godot rendering method | API |
|---|---|---|
| `vulkan` (default) | `forward_plus` | Vulkan, desktop |
| `vulkan-mobile` | `mobile` | Vulkan, mobile renderer |
| `opengl` | `gl_compatibility` | OpenGL 3.3 / GLES 3 |

`scripts/graphics.gd` prints the method and adapter Godot actually started
with, so a fallback is visible in the console instead of being a silent
performance problem. To switch backend, regenerate the project; Godot does not
change it at runtime.

The Godot target is a **re-host** of the converted content, not an embedded
Luanti client. It ships a small runtime that spawns the entities, adds a ground
plane and lets you cycle through them; it does not execute Luanti Lua mods,
Molang animations or shaders.

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
cover all three editions perfectly: **Bedrock has item definitions in the
behaviour pack, Java Edition has none in a datapack.** The recipe converts
cleanly. The item needs a resource pack on the Java side, and LuaCraft says
so instead of pretending otherwise.

---

## The portable API

Every primitive declares a capability level per edition: `full`, `partial` or
`none`.

| Primitive | Java | Bedrock | Luanti | Notes |
|---|---|---|---|---|
| `say(message)` | full | full | full | |
| `message(player, message)` | full | full | full | |
| `give(player, item, count?, components?)` | full | full | full | |
| `clear(player, item?, count?)` | full | full | full | |
| `score(player, objective, operation?, value?)` | full | full | partial | Luanti stores scores in mod storage |
| `forEachPlayer(callback)` | full | full | full | |
| `forEachEntity(entity_type, callback)` | full | partial | partial | Bedrock filters in script, not in a selector |
| `setblock(x, y, z, block)` | full | partial | full | Bedrock cannot set arbitrary block states |
| `getBlock(x, y, z)` | full | partial | full | Bedrock reads through a command |
| `effect(player, effect, seconds?, amplifier?)` | full | full | none | Luanti has no potion effects |
| `playSound(sound, player?, volume?, pitch?)` | full | full | partial | Luanti uses its own sound registry |
| `teleport(target, x, y, z)` | full | full | full | |
| `onTick(callback)` | full | full | full | |
| `onJoin(callback)` | full | full | full | |
| `addObjective(name, criteria?, display?)` | full | full | partial | Luanti emulates objectives |
| `setDynamicProperty(target, key, value)` | none | full | full | Use scoreboards or NBT in Java |
| `getDynamicProperty(target, key)` | none | full | full | |
| `nbt(target, path, value)` | full | none | none | Use dynamic properties elsewhere |
| `runCommand(command)` | full | full | partial | Luanti interprets the built-in commands |

Run `luacraft api` for the live table.

### Hooks

| Hook | Behaviour |
|---|---|
| `onTick(callback)` | Every game tick |
| `onJoin(callback)` | When a player joins |
| `onLoad(callback)` | When the world loads |
| `onJavaOnly(callback)` | Body is kept in the Java build only |
| `onBedrockOnly(callback)` | Body is kept in the Bedrock build only |
| `onLuantiOnly(callback)` | Body is kept in the Luanti build only |

The edition specific hooks are how you write code that cannot be portable.
The other editions report a warning naming the hook.

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
      │    Content    │  items, blocks, recipes and entities become an
      │    reader     │  intermediate representation
      └────┬──────────┘
           │
   ┌───────┼────────────────┬──────────────┐
   ▼       ▼                ▼              ▼
┌────────┐ ┌──────────────┐ ┌────────────┐ ┌────────────────┐
│  Java  │ │   Bedrock    │ │   Luanti   │ │ Hytale + Godot │
│backend │ │   backend    │ │  backend   │ │   exporters    │
└───┬────┘ └──────┬───────┘ └─────┬──────┘ └───────┬────────┘
    │             │               │                │
datapack.zip   .mcaddon        mod.zip         asset pack +
+ resource pack                                 Godot project
```

The Java backend emits ordinary mcfunction commands, so the datapack loads in
vanilla Java Edition with no mod installed. The Bedrock backend emits
TypeScript for the `@minecraft/server` module, which the game compiles itself.
The Luanti backend emits a regular `init.lua` + `mod.conf` mod, which is the
author's own Lua and runs on the LGPL engine without bundling any engine code.

Content renders differently on each side, and the difference is reported
rather than hidden. Recipes convert cleanly. Custom items are written as item
definitions in the Bedrock behaviour pack and as `register_craftitem` calls in
Luanti; on the Java side LuaCraft reports that a resource pack is needed for
the texture and model, because Java datapacks have no item definitions at all.

---

## Textures, without downloading anything

`--textures` looks at every declared item and block and packages an image for
it:

| Edition | Where the images go |
|---|---|
| Java | `assets/<ns>/textures/item|block/`, models and a `custom_model_data` override, in a separate resource pack |
| Bedrock | `rp/textures/items|blocks/`, plus `item_texture.json`, `terrain_texture.json` and the language file |
| Luanti | `textures/<name>.png`, referenced by `inventory_image` and `tiles` |

If you already have art, pass the folder with `--texture-dir art/`. LuaCraft
reads `*.png` files keyed by file stem, uses them when the texture name
matches, and generates a deterministic placeholder for anything missing. The
placeholder is a valid 16×16 RGBA PNG written by a small encoder built on
`zlib`; it is ugly but unique per name, and it keeps the build self contained.

**Java limitation, stated by the compiler:** a datapack cannot register a new
item or block. The generated resource pack retextures a base vanilla item and
keys the model with `custom_model_data = 1000 + index`. The item keeps the
vanilla name and behaviour unless a real mod replaces it. Bedrock and Luanti
do register real content.

---

## Sandbox

`luacraft sandbox` validates a generated archive or an unpacked directory
without launching any game:

```bash
luacraft build mod.lua --target all --textures --sandbox --bundle out/
luacraft sandbox out/luacraft.mcaddon
luacraft sandbox out/luacraft-java.zip --json
luacraft sandbox out/faunalibre-hytale.zip --target hytale
luacraft sandbox out/godot --target godot
```

It checks that every JSON file parses, every Bedrock manifest has valid UUIDs
and versions, every Java command is a real command, every called function and
tag target exists, every model or atlas texture is inside the pack, every PNG
has a PNG signature, the Luanti `init.lua` parses again, its recipes only use
registered items, and its textures exist. A Hytale pack is checked for its
manifest, layout, role-to-model references, `.blockymodel` nodes and
quaternions, item icons and PNGs. A Godot project is checked for
`project.godot`, a supported rendering method, the main scene, its runtime
script and the models and textures each entity references.

The report is explicit about its limits: the sandbox inspects the pack
statically, it does not execute it, and it cannot judge gameplay or balance.
It catches compiler mistakes (a typo in a path, a missing file, a command that
is not a command), not design mistakes.

---

## Local GUI

```bash
luacraft gui            # http://127.0.0.1:8765/
luacraft gui --port 0   # pick a free port
```

A single page editor served by the Python standard library. There is no npm,
no CDN and no external request: the HTML, CSS and JavaScript are embedded in
`gui.py`. It lets you load an example, edit the Lua, compile for any subset of
the three editions, drag PNG textures in, and inspect diagnostics, generated
files, the sandbox report and the debug trace side by side. The download
buttons give you the datapack, the resource pack, the `.mcaddon` and the
Luanti mod.

The server binds to loopback and has no authentication, because it is a
development tool for one machine, not a web service. Request bodies are
capped at 8 MB and responses at 200 000 characters per preview.

---

## MCP server

`luacraft mcp` speaks the Model Context Protocol over stdio: one JSON-RPC 2.0
message per line, standard library only. Point OpenCode (or any MCP client)
at it and the agent can compile, check, inspect content and validate packs
without guessing at command syntax.

| Tool | What it does |
|---|---|
| `luacraft_compile` | Compile one source for any edition; optional archives, sandbox and trace |
| `luacraft_check` | Report supported, partial and missing capabilities per edition |
| `luacraft_api` | Describe every primitive and its capability in each edition |
| `luacraft_content` | List declared items, blocks, recipes and loot |
| `luacraft_sandbox` | Validate an archive, or compile and validate in one step |
| `luacraft_list_examples` | List the bundled examples with their source |

Example configuration for an MCP client that launches a command:

```json
{
  "mcpServers": {
    "luacraft": {
      "command": "luacraft",
      "args": ["mcp"]
    }
  }
}
```

The server never writes outside the `output_dir` the caller names, and
`include_archives` returns the same bytes the CLI would write, base64 encoded.

---

## Debug

`--debug` records what every phase did and how long it took: source size,
statement count, diagnostics, declared content, files emitted per backend,
textures attached and totals. The trace is structured data, not log noise, so
the CLI prints it to stderr, the GUI shows it in a tab and the MCP server
returns it as JSON:

```bash
luacraft build mod.lua --target java --debug --json
```

```json
{
  "debug": {
    "java": {
      "enabled": true,
      "total_ms": 2.914,
      "events": [
        {"phase": "parse", "detail": "6 top level statements", "duration_ms": 1.02},
        {"phase": "analyse", "detail": "0 errors, 0 warnings", "duration_ms": 0.31},
        {"phase": "backend:java", "detail": "4 files", "duration_ms": 0.42}
      ],
      "data": {"edition": "java", "files": 4, "warnings": 0}
    }
  }
}
```

The trace never includes file contents and never touches the network.

---

## Legal

LuaCraft is an unofficial tool and is not affiliated with Mojang Studios or
Microsoft, Hypixel Studios or the Hytale team, or the Luanti and Godot
projects. It ships no game assets and downloads nothing; it only writes files
in the public pack formats, which you compile with your own account and your
own copy of the game.

The Minecraft formats and the Bedrock Script API are documented publicly by
their respective holders. Hytale asset schemas follow the public 2026
modding documentation and must be verified against the installed game version.
When a source pack's licence does not allow redistribution, leave
`--copy-textures` off and LuaCraft generates placeholders instead.

---

## Licence

MIT.
