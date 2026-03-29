# Design Document: Brick Breaker
## Platform: LÖVE2D (Lua)
## Scope: Single file, no external assets

---

## 1. Core Concept
Classic brick breaker. Ball bounces around the screen. Player controls a paddle to keep the ball in play. Bricks are destroyed on contact. Clear all bricks to advance to the next level.

---

## 2. Screen & Layout
- Resolution: 800x600
- Paddle zone: bottom 10% of screen
- Brick zone: top 40% of screen (8 rows x 12 columns)
- Play area: full width, padded 20px each side
- Background: black

---

## 3. Game Objects

### Ball
- Radius: 8px
- Starting position: center screen, just above paddle
- Starting velocity: 300px/s at 45 degree angle (upward)
- Speed increases by 10% per level cleared
- Color: white

### Paddle
- Width: 100px (shrinks by 10px per level, minimum 60px)
- Height: 12px
- Controlled by mouse X position (clamped to screen bounds)
- Color: white
- Y position: fixed at 540px

### Bricks
- Grid: 12 columns x 8 rows
- Brick size: 58px wide x 20px tall
- Gap between bricks: 4px
- Grid starts at Y: 60px
- Colors by row (top to bottom):
  - Row 1-2: Red
  - Row 3-4: Orange  
  - Row 5-6: Yellow
  - Row 7-8: Green
- All bricks: single hit to destroy
- Total bricks per level: 96

---

## 4. Collision Detection
- Use AABB (axis-aligned bounding box) for all collisions
- Ball vs walls: reflect X velocity on left/right walls, reflect Y on top wall
- Ball vs paddle: reflect Y velocity, adjust X velocity based on where ball hits paddle (center = straight up, edges = angled)
- Ball vs brick: determine which face was hit (top/bottom vs left/right) and reflect appropriate axis. Destroy brick.
- Ball falls below screen bottom: lose a life

### Paddle Angle Influence
- Divide paddle into 5 zones
- Center zone: ball goes straight up (angle: -90 degrees)
- Inner zones: slight angle (±30 degrees from vertical)
- Outer zones: steep angle (±60 degrees from vertical)
- Speed is preserved, only direction changes

---

## 5. Game States

### States
- `menu` — Title screen, press any key to start
- `playing` — Active gameplay
- `dead` — Ball lost, brief pause before relaunching
- `level_complete` — All bricks cleared, brief pause before next level
- `game_over` — No lives remaining

### Transitions
- `menu` → `playing`: any key press
- `playing` → `dead`: ball falls below screen
- `dead` → `playing`: after 1.5 second delay (relaunch ball)
- `playing` → `level_complete`: all bricks destroyed
- `level_complete` → `playing`: after 2 second delay (new level)
- `playing` → `game_over`: lives reach 0
- `game_over` → `menu`: any key press

---

## 6. Lives & Scoring

### Lives
- Start with 3 lives
- Lose 1 life when ball falls below screen
- No extra lives

### Score
- Red brick: 70 points
- Orange brick: 50 points  
- Yellow brick: 30 points
- Green brick: 10 points
- Display score top-left
- Display lives top-right
- Display level top-center

---

## 7. UI & Text

### Menu Screen
- Title: "BRICK BREAKER" centered, large font
- Subtitle: "Press any key to start" centered, small font

### HUD (during play)
- Score: "SCORE: XXXXX" top-left
- Level: "LEVEL: X" top-center  
- Lives: "LIVES: X" top-right

### Game Over Screen
- "GAME OVER" centered, large font
- Final score centered, medium font
- "Press any key to continue" centered, small font

### Level Complete Screen
- "LEVEL COMPLETE!" centered, large font
- "Get ready..." centered, small font

---

## 8. Technical Notes
- All game logic in `love.update(dt)`
- All rendering in `love.draw()`
- Input in `love.mousemoved()` for paddle
- Key handling in `love.keypressed()` for state transitions
- Use `love.graphics.setColor()` for all colors
- Use `love.graphics.rectangle()` for paddle and bricks
- Use `love.graphics.circle()` for ball
- Reset ball to center above paddle on life loss or level start
- Ball launches automatically after delay in `dead` state

---

## 9. Out of Scope
- Sound
- Particle effects
- Power-ups
- High score saving
- Multiple ball types
- Animated backgrounds

---

## 10. Acceptance Criteria
- [ ] Game launches without errors in LÖVE2D
- [ ] Ball bounces correctly off all surfaces
- [ ] Paddle responds to mouse movement
- [ ] Bricks disappear when hit
- [ ] Score updates correctly per brick color
- [ ] Lives decrease when ball is lost
- [ ] Level advances when all bricks cleared
- [ ] Game over triggers at 0 lives
- [ ] All state transitions work correctly
- [ ] Speed increases each level
