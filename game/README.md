# The Art of Becoming

A LÖVE2D 2D platformer. The game progresses through evolving art styles — each stage uses a different visual language as the world "becomes" more realized.

**Run:** `love Lua/platformer`

---

## Stage I — Pencil World

Stick figure on a cream graph-paper background. Everything drawn with line primitives only — no fills, no sprites.

### What Works
- Movement (left/right arrows or A/D)
- Jump (up / W / space)
- AABB tilemap collision (resolved per-axis: X then Y)
- Smooth camera follow with lerp and world-edge clamping
- Cream background with faint graph-paper grid
- Stick-figure player drawn with `love.graphics.line` and `circle("line")`
- HUD: stage name + player state

### Known Bugs
- **Corner clipping** — player can clip into tile corners at speed. `check_rect` resolves per-axis but accumulated pushes across multiple tiles in one frame can cancel at diagonals. Needs swept AABB or two-pass separation.
- **No coyote time** — `was_on_ground` lasts one frame only. Need ~0.1s grace window for jumps off platform edges to feel correct.
- **No jump buffering** — jump pressed just before landing is ignored. Buffer input for ~0.1s.

### Needs Refactor
- **OOP inconsistency** — `player.lua` uses closure-based methods; `camera.lua` uses `setmetatable/__index`. Unify to `setmetatable` across all modules.
- **Hardcoded level layout** — platforms in `world.lua` are literal tile ranges. Extract to a level data table `{row, col_start, col_end}` so levels are editable without touching code.
- **No death / out-of-bounds** — falling below `MAP_H` has no respawn logic.

---

## File Structure

```
Lua/platformer/
  conf.lua       — window config (960×540)
  main.lua       — love.load/update/draw/keypressed, wires all modules
  player.lua     — physics, input, state machine, stick-figure draw
  world.lua      — tilemap, AABB collision (check_rect), draw
  camera.lua     — smooth follow camera, viewport clamp
  renderer.lua   — background, grid lines, HUD
```

---

## Design Doc

See `NOVA shard: chatgpt_creative_evolving_art_style_platformer` — full GDD including all planned stages, enemy types, and progression system.
