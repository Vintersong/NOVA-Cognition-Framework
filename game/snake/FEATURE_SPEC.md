# Snake Game Feature Specification

## 1. Problem Statement

Many users seek simple, engaging, and nostalgic gaming experiences that are easily accessible and do not require complex setups or installations. A classic arcade game like Snake provides immediate entertainment and a satisfying challenge, fulfilling the desire for quick, fun, and retro-themed digital pastimes.

## 2. Target Users

- **Casual Gamers:** Individuals looking for a quick, entertaining diversion without a significant time commitment.
- **Nostalgia Seekers:** Users who appreciate classic arcade games and retro aesthetics.
- **Developers/Learners:** Those interested in simple game mechanics and basic web development.

## 3. Scope

### In Scope
- Single-file HTML/JS implementation
- Basic snake movement (Up, Down, Left, Right)
- Food generation at random, clear positions
- Snake growth upon eating food
- Score tracking and display
- Progressive game speed increase based on score
- Game over condition: snake collides with itself or game boundaries
- Retro-arcade visual style
- Display of final score on game over

### Out of Scope
- Multiplayer functionality
- Sound effects or background music
- Persistent high scores (local storage, backend database)
- Multiple game modes (obstacles, different board sizes)
- Complex UI beyond game board and score display
- External libraries, frameworks, or dependencies
- Pause/Resume functionality

## 4. Functional Requirements

- **FR1: Snake Movement** — Player controls snake direction (Up/Down/Left/Right) via arrow keys. Snake moves continuously.
- **FR2: Food Generation** — A single food item appears randomly on the grid, never on top of the snake.
- **FR3: Eating & Growth** — When head collides with food: food disappears, snake grows by one segment, score increases.
- **FR4: Score System** — Current score is displayed during gameplay, updated whenever food is eaten (+10 pts).
- **FR5: Progressive Speed** — Game speed incrementally increases every 5 foods eaten (150ms start, 5ms decrease, 60ms min).
- **FR6: Game Over Conditions** — Game ends if snake head collides with its own body or grid boundaries.
- **FR7: Game Over Display** — "Game Over" message displayed with final score and restart option.

## 5. Non-Functional Requirements

- **NFR1: Performance** — Game runs smoothly without noticeable lag in modern browsers.
- **NFR2: Usability** — Controls are intuitive and responsive; no input lag.
- **NFR3: Maintainability** — Code is well-structured, readable, contained in a single HTML/JS file.
- **NFR4: Compatibility** — Functions correctly in Chrome, Firefox, Edge, Safari.
- **NFR5: Aesthetics** — Visual design adheres to retro-arcade style with a limited, intentional color palette.

## 6. Definition of Done

- [ ] All functional requirements FR1–FR7 implemented and tested
- [ ] All non-functional requirements NFR1–NFR5 met
- [ ] Game is fully playable from start to game over
- [ ] Code has undergone peer review
- [ ] No critical or major bugs present
- [ ] Visual style is consistent with the retro-arcade theme

## 7. Open Questions / Risks

- **Open Question:** Exact speed escalation curve — linear decrement or exponential? (Assumption: linear, -5ms per 5 foods)
- **Risk:** Collision detection accuracy — snake self-collision is subtle at high speeds. Mitigate: thorough step-by-step testing.
- **Risk:** Frame rate consistency across browsers — pure JS `setInterval` may drift. Mitigate: test early on Chrome/Firefox/Safari.
- **Risk:** Retro style execution — achieving a distinctive aesthetic without external assets. Mitigate: commit to a defined color system (OKLCH tokens) before implementation.
