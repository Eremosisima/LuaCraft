-- Ejemplo que usa capacidades que no existen en una de las ediciones.
-- Sirve para comprobar los avisos que emite LuaCraft.

onTick(function()
  -- NBT generico: solo existe en Java Edition.
  nbt(player, "CustomData", "value")

  -- Dynamic properties: solo existen en Bedrock Edition.
  setDynamicProperty(player, "coins", 10)

  -- Entidades: Bedrock solo lo soporta de forma parcial.
  forEachEntity("minecraft:zombie", function(entity)
    message(entity, "Hola zombi")
  end)

  -- Bloques: Bedrock solo lo soporta de forma parcial.
  setblock(0, 64, 0, "minecraft:stone")
end)

-- Un hook exclusivo de Java Edition.
onJavaOnly(function()
  say("Esto solo sale en Java Edition")
end)

-- Un hook exclusivo de Bedrock Edition.
onBedrockOnly(function()
  say("Esto solo sale en Bedrock Edition")
end)
