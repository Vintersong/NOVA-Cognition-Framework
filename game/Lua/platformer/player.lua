local Player = {}

-- Local constants
local GRAVITY = 900
local JUMP_FORCE = -420
local MOVE_SPEED = 180
local MAX_FALL = 600

-- Private helper functions for input
local function is_left_pressed()
    return love.keyboard.isDown("left", "a")
end

local function is_right_pressed()
    return love.keyboard.isDown("right", "d")
end

local function is_jump_pressed()
    return love.keyboard.isDown("up", "w", "space")
end

function Player.new(x, y)
    local self = {}

    self.x = x
    self.y = y
    self.vx = 0
    self.vy = 0
    self.w = 20
    self.h = 40
    self.on_ground = false
    self.facing = 1 -- 1=right, -1=left
    self.state = "idle" -- "idle"|"run"|"jump"|"fall"

    function self:update(dt, world)
        -- 1. Capture last frame's on_ground before resetting
        local was_on_ground = self.on_ground
        self.on_ground = false

        -- 2. Horizontal input
        local desired_vx = 0
        if is_left_pressed() then
            desired_vx = -MOVE_SPEED
            self.facing = -1
        end
        if is_right_pressed() then
            desired_vx = MOVE_SPEED
            self.facing = 1
        end
        self.vx = desired_vx

        -- 3. Jump input (use was_on_ground — on_ground was just cleared above)
        if is_jump_pressed() and was_on_ground then
            self.vy = JUMP_FORCE
        end

        -- 4. Apply gravity
        self.vy = math.min(self.vy + GRAVITY * dt, MAX_FALL)

        -- 5. Move X
        self.x = self.x + self.vx * dt

        -- 6. Resolve X collision
        local collision_result_x = world:check_rect(self.x, self.y, self.w, self.h, "x")
        self.x = self.x + collision_result_x.dx
        if math.abs(collision_result_x.dx) > 0.001 then -- Use a small epsilon for float comparison
            self.vx = 0
        end

        -- 7. Move Y
        self.y = self.y + self.vy * dt

        -- 8. Resolve Y collision
        local collision_result_y = world:check_rect(self.x, self.y, self.w, self.h, "y")
        self.y = self.y + collision_result_y.dy
        if collision_result_y.on_ground then
            self.on_ground = true
            self.vy = 0
        end
        if collision_result_y.dy < 0 then -- hit ceiling
            self.vy = 0
        end

        -- 9. State machine
        if self.on_ground and math.abs(self.vx) > 1 then
            self.state = "run"
        elseif self.on_ground then
            self.state = "idle"
        elseif self.vy < 0 then
            self.state = "jump"
        else
            self.state = "fall"
        end
    end

    function self:draw()
        love.graphics.setLineWidth(2)
        love.graphics.setColor(0.15, 0.13, 0.10) -- Dark brown/grey

        local cx = self.x + self.w / 2
        local top = self.y

        -- head (circle, line only, 12 segments)
        love.graphics.circle("line", cx, top + 6, 6, 12)

        -- body
        love.graphics.line(cx, top + 12, cx, top + 26)

        -- legs
        love.graphics.line(cx, top + 26, cx - 8, top + 40)
        love.graphics.line(cx, top + 26, cx + 8, top + 40)

        -- arms (raise slightly when airborne)
        local aoff = (self.state == "jump" or self.state == "fall") and -4 or 4
        love.graphics.line(cx, top + 16, cx - self.facing * 10, top + 22 + aoff)
        love.graphics.line(cx, top + 16, cx + self.facing * 10, top + 20)
    end

    return self
end

return Player