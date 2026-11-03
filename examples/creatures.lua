-- Entities are first class on the targets that support them.
--
-- Luanti registers a real entity with the OBJ model, hitbox, health, taming
-- and drops. Hytale gets an NPC role plus a .blockymodel. Godot re-hosts it as
-- a native 3D node. Java and Bedrock keep the species-card item as a proxy.
--
-- Build it with:
--   luacraft godot examples/creatures.lua -o out/creatures --textures
--   luacraft hytale examples/creatures.lua -o out/creatures.zip --textures

items.fruta_lunar = {
  id = "luacraft:fruta_lunar",
  texture = "fruta_lunar",
  display_name = "Fruta Lunar",
  max_stack = 64,
  category = "nature",
  food = { nutrition = 4 },
}

entities.benogrok = {
  id = "luacraft:benogrok",
  display_name = "Benogrok",
  texture = "benogrok",
  model = "benogrok_model",
  health = 17,
  movement_speed = 0.22,
  collision_box = { width = 1.8, height = 0.69 },
  hostile = false,
  tameable = {
    probability = 0.33,
    tame_items = { "luacraft:fruta_lunar" },
  },
  drops = {
    { item = "luacraft:fruta_lunar", min = 1, max = 2, chance = 0.5 },
  },
  families = { "fauna", "benogrok" },
}

entities.broflaifa = {
  id = "luacraft:broflaifa",
  display_name = "Broflaifa",
  texture = "broflaifa",
  model = "broflaifa_model",
  health = 24,
  movement_speed = 0.26,
  collision_box = { width = 1.1, height = 1.4 },
  hostile = true,
  drops = {
    { item = "luacraft:fruta_lunar", min = 1, max = 1, chance = 1.0 },
  },
  families = { "fauna", "broflaifa", "monster" },
}

recipes.fruta_lunar_recipe = {
  type = "shapeless",
  ingredients = { "minecraft:apple", "minecraft:sugar" },
  result = "luacraft:fruta_lunar",
  count = 2,
}
