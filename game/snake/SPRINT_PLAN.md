# Sprint Plan: Core Snake Mechanics

## Sprint Goal

Implement the fundamental Snake game mechanics — movement, food, growth, and game over — to establish a playable core loop for initial testing and feedback.

## User Stories

### Story 1 — Snake Movement (3 pts)
**As a player, I want to control the snake's movement within a grid, so I can navigate the game area.**

Acceptance Criteria:
- Snake moves continuously in a set direction (initial: right)
- Player changes direction with arrow keys (Up/Down/Left/Right) and WASD
- Snake cannot immediately reverse direction (e.g., Left after Right)
- Wall collision triggers game over (no wrapping)

**Estimate:** 3 story points

---

### Story 2 — Food & Growth (5 pts)
**As a player, I want food to appear on the board and for the snake to grow when it eats it, so I can increase my score and progress.**

Acceptance Criteria:
- A single food item appears randomly at a position not occupied by the snake
- When the snake's head collides with food, food disappears and score increases by 10
- Snake grows by one segment at the tail
- New food appears immediately after consumption

**Estimate:** 5 story points

---

### Story 3 — Game Over (2 pts)
**As a player, I want the game to end if the snake collides with itself or the boundary, so I know when my run is over.**

Acceptance Criteria:
- Game stops when snake head occupies the same cell as any body segment
- Game stops when snake head moves beyond grid boundaries
- "GAME OVER" message is displayed prominently
- Final score is visible
- Restart option is available (button + R key)

**Estimate:** 2 story points

---

**Total: 10 story points**

## Dependencies

None — all three stories can be developed sequentially within the sprint. Story 1 is a prerequisite for Stories 2 and 3 functionally, but scope is small enough to execute in order without blocking.

## Risk Register

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Collision detection bugs (self-collision edge cases at high speed) | Medium | High | Implement and test wall collision first, then self-collision. Peer review the game loop logic before closing the sprint. |
| Jittery or inconsistent movement across browsers | Medium | Medium | Use `setInterval` for game state updates. Test on Chrome, Firefox, and Safari early. Clear and redraw the full canvas each tick. |
| Unclear game state after Game Over (double-trigger, stale intervals) | Low | Medium | Use a `gameOver` boolean flag; always call `clearInterval` before setting a new one on restart. Define explicit states: PLAYING → GAME_OVER → RESTART. |

## Definition of Done

- [ ] All 3 user stories fully implemented and meet their acceptance criteria
- [ ] Game is playable from start through game over and restart
- [ ] Code is clean and readable
- [ ] Developer-level testing completed (all collision cases manually verified)
- [ ] All work committed to version control
- [ ] No critical bugs preventing core gameplay
- [ ] Peer code review completed
