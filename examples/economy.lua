-- Ejemplo de mod LuaCraft.
-- El mismo archivo se compila para Java Edition y para Bedrock Edition.

-- ---------------------------------------------------------------------------
-- Contenido declarativo
-- ---------------------------------------------------------------------------

items.ruby = {
  id = "luacraft:ruby",
  max_stack = 1,
  texture = "ruby",
  display_name = "Ruby",
  category = "items",
}

items.ruby_sword = {
  id = "luacraft:ruby_sword",
  max_stack = 1,
  durability = 250,
  texture = "ruby_sword",
  display_name = "Ruby Sword",
  category = "equipment",
  hand_equipped = true,
  glint = true,
}

items.ruby_dust = {
  id = "luacraft:ruby_dust",
  max_stack = 64,
  texture = "ruby_dust",
}

blocks.ruby_block = {
  id = "luacraft:ruby_block",
  hardness = 3.0,
  resistance = 6.0,
  light_emission = 4,
  friction = 0.6,
  map_color = "#B00020",
  texture = "ruby_block",
}

recipes.ruby_sword = {
  type = "shaped",
  pattern = { "R", "R", "S" },
  key = { R = "luacraft:ruby", S = "minecraft:stick" },
  result = "luacraft:ruby_sword",
  count = 1,
  group = "luacraft_tools",
}

recipes.ruby_block = {
  type = "shapeless",
  ingredients = { "luacraft:ruby_dust", "luacraft:ruby_dust", "luacraft:ruby_dust" },
  result = "luacraft:ruby_block",
  count = 1,
}

-- ---------------------------------------------------------------------------
-- Logica
-- ---------------------------------------------------------------------------

-- Un objetivo de scoreboard para llevar la cuenta.
addObjective("coins", "dummy")

-- Al entrar, saludar a todos.
onJoin(function()
  forEachPlayer(function(player)
    message(player, "Bienvenido a LuaCraft")
  end)
end)

-- Cada tick, contar las monedas de cada jugador.
onTick(function()
  forEachPlayer(function(player)
    score(player, "coins", "add", 1)
    if score(player, "coins") >= 100 then
      give(player, "luacraft:ruby", 1)
      score(player, "coins", "remove", 100)
      message(player, "Has ganado una gema!")
    end
  end)
end)
