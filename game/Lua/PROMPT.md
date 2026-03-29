# Claude Code Orchestration Test — Brick Breaker in LÖVE2D

## Your Role
You are the lead orchestrator for this build. Your job is to:
1. Read the design document carefully
2. Plan the implementation as a series of discrete tasks
3. Implement each task, logging every decision
4. Produce a complete, runnable `main.lua` for LÖVE2D
5. Write a session log documenting what was built, what decisions were made, and any issues encountered

## Instructions
- Implement the game as a single `main.lua` file
- Use only LÖVE2D built-in functions — no external libraries
- Use Gemini for any sub-tasks you delegate (code generation, collision logic, state machine design)
- Log every agent call, every decision point, and every iteration
- After completing the implementation, write a `session_log.md` documenting the full process

## Agent & Skill Selection — CRITICAL
Before starting implementation, explicitly document in session_log.md:
1. **Which agent.md was selected** — name it, explain why it matched this task over other available agents
2. **Which skill files were loaded** — list each skill loaded, explain what triggered the load
3. **Skills considered but not loaded** — list any skills evaluated but deemed not relevant and why
4. **Routing confidence** — how confident was the routing decision, were there ambiguous cases

This agent and skill selection audit is the primary test of this session. The game itself is secondary.

## Deliverables
1. `main.lua` — complete, runnable brick breaker game
2. `session_log.md` — full log of the orchestration process including:
   - Task breakdown
   - Each agent call made (model used, task given, result)
   - Decisions made and why
   - Any bugs encountered and how they were resolved
   - Total tokens used if available

## Success Criteria
- Game runs in LÖVE2D without errors
- Ball bounces correctly off paddle, walls, and bricks
- Bricks disappear on hit
- Lives system works
- Game over and level complete states work
- Session log is complete and honest about what worked and what didn't

Read `design_doc.md` before starting.
