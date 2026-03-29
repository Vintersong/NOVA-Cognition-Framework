```lua
-- event_bus.lua

--- A simple event bus module for LÖVE2D games.
-- Allows different parts of the game to communicate without direct dependencies.
--
-- Example Usage:
--
-- -- In game_state.lua
-- local EventBus = require("event_bus")
--
-- local function onPlayerDied(player_data)
--     print("Player died! Score:", player_data.score)
--     -- Trigger game over logic
-- end
--
-- local function onEnemyDestroyed(enemy_id)
--     print("Enemy", enemy_id, "destroyed!")
--     -- Update score, play sound, etc.
-- end
--
-- EventBus.subscribe("player_death", onPlayerDied)
-- EventBus.subscribe("enemy_destroyed", onEnemyDestroyed)
--
-- -- In player.lua
-- local EventBus = require("event_bus")
--
-- function Player:takeDamage(amount)
--     self.health = self.health - amount
--     if self.health <= 0 then
--         EventBus.publish("player_death", {score = self.score, player_id = self.id})
--     end
-- end
--
-- -- In enemy.lua
-- local EventBus = require("event_bus")
--
-- function Enemy:destroy()
--     EventBus.publish("enemy_destroyed", self.id)
--     -- Cleanup enemy object
-- end
--
-- -- To stop listening
-- EventBus.unsubscribe("player_death", onPlayerDied)

local EventBus = {}

-- Private table to store subscribers.
-- Structure:
-- subscribers = {
--   [event_name_string] = {
--     [1] = callback_function_1,
--     [2] = callback_function_2,
--     ...
--   },
--   [another_event_name_string] = { ... }
-- }
local subscribers = {}

--- Subscribes a callback function to an event.
-- When the specified `event_name` is published, the `callback` function will be called.
--
-- @param event_name string The name of the event to subscribe to (e.g., "player_died", "enemy_spawned").
-- @param callback function The function to call when the event is published. It will receive
--                          any `data` passed during the `publish` call as its first argument.
-- @usage EventBus.subscribe("player_hit", function(data) print("Player hit!", data.damage) end)
function EventBus.subscribe(event_name, callback)
    if type(event_name) ~= "string" then
        error("EventBus.subscribe: event_name must be a string.", 2)
    end
    if type(callback) ~= "function" then
        error("EventBus.subscribe: callback must be a function.", 2)
    end

    -- Initialize the event's subscriber list if it doesn't exist
    if not subscribers[event_name] then
        subscribers[event_name] = {}
    end

    -- Add the callback to the list.
    -- Multiple subscriptions of the same function to the same event are allowed.
    table.insert(subscribers[event_name], callback)
end

--- Publishes an event, calling all currently subscribed callbacks for that event.
--
-- @param event_name string The name of the event to publish.
-- @param data any (optional) Data to pass to the subscribed callbacks. This can be any Lua type.
-- @usage EventBus.publish("player_hit", {damage = 10, source = "laser"})
-- @usage EventBus.publish("game_start") -- No data needed
function EventBus.publish(event_name, data)
    if type(event_name) ~= "string" then
        error("EventBus.publish: event_name must be a string.", 2)
    end

    local event_subscribers = subscribers[event_name]

    if event_subscribers then
        -- Iterate over a shallow copy of the subscribers list.
        -- This prevents issues if a callback unsubscribes itself or others
        -- during the publish cycle, which would otherwise invalidate iterator indices.
        local callbacks_to_call = {}
        for _, cb in ipairs(event_subscribers) do
            table.insert(callbacks_to_call, cb)
        end

        for _, callback in ipairs(callbacks_to_call) do
            -- A final type check just in case, though the `subscribe` function
            -- ensures only functions are added.
            if type(callback) == "function" then
                callback(data)
            end
        end
    end
end

--- Unsubscribes a specific callback function from an event.
-- Only the exact function reference that was subscribed will be removed.
--
-- @param event_name string The name of the event to unsubscribe from.
-- @param callback function The specific function reference to remove.
-- @usage local my_handler = function(data) print("Handling event:", data) end
--        EventBus.subscribe("my_event", my_handler)
--        -- ... later ...
--        EventBus.unsubscribe("my_event", my_handler)
function EventBus.unsubscribe(event_name, callback)
    if type(event_name) ~= "string" then
        error("EventBus.unsubscribe: event_name must be a string.", 2)
    end
    if type(callback) ~= "function" then
        error("EventBus.unsubscribe: callback must be a function.", 2)
    end

    local event_subscribers = subscribers[event_name]

    if event_subscribers then
        -- Iterate backward to safely remove elements without disrupting iteration.
        for i = #event_subscribers, 1, -1 do
            if event_subscribers[i] == callback then
                table.remove(event_subscribers, i)
                -- Assuming a callback is typically unsubscribed once for a given event,
                -- we break after the first match. If multiple identical callbacks
                -- are expected to be unsubscribed (e.g., if subscribed multiple times),
                -- remove the `break` to remove all instances.
                break
            end
        end

        -- Clean up the event list if it becomes empty to save memory.
        if #event_subscribers == 0 then
            subscribers[event_name] = nil
        end
    end
end

return EventBus
```