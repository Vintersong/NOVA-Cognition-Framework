local Renderer = {}

function Renderer.draw_background(vp_w, vp_h)
  love.graphics.setColor(248/255, 244/255, 230/255)
  love.graphics.rectangle("fill", 0, 0, vp_w, vp_h)

  love.graphics.setColor(0.82, 0.79, 0.72, 0.4)
  love.graphics.setLineWidth(0.5)

  for x = 0, vp_w, 32 do
    love.graphics.line(x, 0, x, vp_h)
  end
  for y = 0, vp_h, 32 do
    love.graphics.line(0, y, vp_w, y)
  end
end

function Renderer.set_line_color()
  love.graphics.setColor(0.15, 0.13, 0.10)
  love.graphics.setLineWidth(1)
end

function Renderer.draw_hud(player, vp_w)
  love.graphics.setColor(0.3, 0.28, 0.25)
  love.graphics.print("Stage I  —  Pencil World", 12, 10)
  local state_str = "[ " .. player.state .. " ]"
  love.graphics.print(state_str, vp_w - 90, 10)
end

return Renderer