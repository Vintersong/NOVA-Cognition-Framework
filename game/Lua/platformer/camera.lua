local Camera = {}
Camera.__index = Camera

function Camera.new(vp_w, vp_h, world_w, world_h)
    local self = {
        x = 0,
        y = 0,
        vp_w = vp_w,
        vp_h = vp_h,
        world_w = world_w,
        world_h = world_h,
        LERP = 6
    }
    setmetatable(self, Camera)
    return self
end

function Camera:update(target_x, target_y, dt)
    local desired_x = target_x - self.vp_w / 2
    local desired_y = target_y - self.vp_h / 2

    self.x = self.x + (desired_x - self.x) * self.LERP * dt
    self.y = self.y + (desired_y - self.y) * self.LERP * dt

    local clamp_x_max = math.max(0, self.world_w - self.vp_w)
    local clamp_y_max = math.max(0, self.world_h - self.vp_h)

    self.x = math.max(0, math.min(self.x, clamp_x_max))
    self.y = math.max(0, math.min(self.y, clamp_y_max))
end

function Camera:apply()
    love.graphics.push()
    love.graphics.translate(-math.floor(self.x), -math.floor(self.y))
end

function Camera:detach()
    love.graphics.pop()
end

return Camera