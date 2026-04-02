local Player   = require("player")
local World    = require("world")
local Camera   = require("camera")
local Renderer = require("renderer")

local world, player, cam

function love.load()
  world  = World.new()
  player = Player.new(64, 440)          -- spawn flush on top of ground row (480-40)
  cam    = Camera.new(960, 540, world.pixel_width, world.pixel_height)
end

function love.update(dt)
  player:update(dt, world)
  local cx = player.x + player.w/2
  local cy = player.y + player.h/2
  cam:update(cx, cy, dt)
end

function love.draw()
  -- 1. background (before camera transform, screen-space)
  Renderer.draw_background(960, 540)

  -- 2. world-space rendering
  cam:apply()
    Renderer.set_line_color()
    world:draw()
    player:draw()
  cam:detach()

  -- 3. HUD (screen-space again)
  Renderer.draw_hud(player, 960)
end

function love.keypressed(key)
  if key == "escape" then love.event.quit() end
end