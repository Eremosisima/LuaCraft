-- Ejemplo de mod LuaCraft.
-- El mismo archivo se compila para Java Edition y para Bedrock Edition.

-- Declaracion de contenido: un item y una receta.
items.ruby = {
  id = "luacraft:ruby",
  max_stack = 1,
  texture = "ruby",
}

recipes.ruby_sword = {
  type = "shaped",
  pattern = { "R", "R", "S" },
  key = { R = "luacraft:ruby", S = "minecraft:stick" },
  result = "luacraft:ruby_sword",
}

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
