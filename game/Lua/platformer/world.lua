local World = {}

-- Constants (local, not exported)
local TILE_SIZE = 32
local MAP_W = 120   -- tiles wide  (~4 screens at 960px)
local MAP_H = 17    -- tiles tall

--- Creates a new world instance.
-- @return table A new world instance.
function World.new()
    local world = {}
    world.tiles = {} -- 2D array [row][col], 1-indexed. 1=solid, 0=air.

    world.pixel_width = MAP_W * TILE_SIZE
    world.pixel_height = MAP_H * TILE_SIZE

    -- Initialize all tiles to air (0)
    for row = 1, MAP_H do
        world.tiles[row] = {}
        for col = 1, MAP_W do
            world.tiles[row][col] = 0
        end
    end

    -- Layout (populate in World.new):
    -- 1. Bottom row (row MAP_H): all solid (ground).
    for col = 1, MAP_W do
        world.tiles[MAP_H][col] = 1
    end

    -- 2. Row MAP_H-1, cols 1..MAP_W: solid (second ground layer for thickness).
    for col = 1, MAP_W do
        world.tiles[MAP_H-1][col] = 1
    end

    -- Helper function to set a range of tiles to solid
    local function set_solid_range(r, c_start, c_end)
        for c = c_start, c_end do
            -- Ensure coordinates are within map bounds
            if r >= 1 and r <= MAP_H and c >= 1 and c <= MAP_W then
                world.tiles[r][c] = 1
            end
        end
    end

    -- 3. Four platforms (solid tiles, 5-8 wide, varied heights):
    set_solid_range(12, 10, 16) -- a. row=12, cols 10..16
    set_solid_range(10, 28, 34) -- b. row=10, cols 28..34
    set_solid_range(8, 50, 57)  -- c. row= 8, cols 50..57
    set_solid_range(11, 75, 82) -- d. row=11, cols 75..82

    --- Sweeps a bounding box against solid tiles.
    -- Accumulates dx, dy to push the rect out of all overlapping solid tiles.
    -- @param x number The x-coordinate of the rectangle (top-left pixel).
    -- @param y number The y-coordinate of the rectangle (top-left pixel).
    -- @param w number The width of the rectangle.
    -- @param h number The height of the rectangle.
    -- @return table A result table {dx=total_dx, dy=total_dy, on_ground=false}.
    -- axis: "x" = only resolve X collisions; "y" = only resolve Y; nil = legacy min-penetration
    function world:check_rect(x, y, w, h, axis)
        local total_dx = 0
        local total_dy = 0
        local on_ground = false

        local min_col = math.max(1, math.floor(x / TILE_SIZE) + 1)
        local max_col = math.min(MAP_W, math.ceil((x + w) / TILE_SIZE))
        local min_row = math.max(1, math.floor(y / TILE_SIZE) + 1)
        local max_row = math.min(MAP_H, math.ceil((y + h) / TILE_SIZE))

        for row = min_row, max_row do
            for col = min_col, max_col do
                if self.tiles[row] and self.tiles[row][col] == 1 then
                    local tile_left   = (col - 1) * TILE_SIZE
                    local tile_right  = col * TILE_SIZE
                    local tile_top    = (row - 1) * TILE_SIZE
                    local tile_bottom = row * TILE_SIZE

                    local rl = x + total_dx
                    local rr = x + w + total_dx
                    local rt = y + total_dy
                    local rb = y + h + total_dy

                    if rr > tile_left and rl < tile_right and rb > tile_top and rt < tile_bottom then
                        local overlap_left   = rr - tile_left
                        local overlap_right  = tile_right - rl
                        local overlap_top    = rb - tile_top
                        local overlap_bottom = tile_bottom - rt

                        if axis == "x" then
                            if overlap_left < overlap_right then
                                total_dx = total_dx - overlap_left
                            else
                                total_dx = total_dx + overlap_right
                            end
                        elseif axis == "y" then
                            if overlap_top < overlap_bottom then
                                total_dy = total_dy - overlap_top
                                on_ground = true
                            else
                                total_dy = total_dy + overlap_bottom
                            end
                        else
                            local pen_x = math.min(overlap_left, overlap_right)
                            local pen_y = math.min(overlap_top, overlap_bottom)
                            if pen_x < pen_y then
                                if overlap_left < overlap_right then
                                    total_dx = total_dx - overlap_left
                                else
                                    total_dx = total_dx + overlap_right
                                end
                            else
                                if overlap_top < overlap_bottom then
                                    total_dy = total_dy - overlap_top
                                    on_ground = true
                                else
                                    total_dy = total_dy + overlap_bottom
                                end
                            end
                        end
                    end
                end
            end
        end

        return {dx = total_dx, dy = total_dy, on_ground = on_ground}
    end

    --- Draws the solid tiles of the world.
    function world:draw()
        love.graphics.setColor(0.15, 0.13, 0.10) -- Dark brownish color
        love.graphics.setLineWidth(1)
        for row = 1, MAP_H do
            for col = 1, MAP_W do
                if self.tiles[row] and self.tiles[row][col] == 1 then
                    local tx = (col - 1) * TILE_SIZE
                    local ty = (row - 1) * TILE_SIZE
                    love.graphics.rectangle("line", tx, ty, TILE_SIZE, TILE_SIZE)
                end
            end
        end
    end

    return world
end

return World