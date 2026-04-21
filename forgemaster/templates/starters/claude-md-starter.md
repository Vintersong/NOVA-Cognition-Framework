---
name: [project-name]
description: >
  [What this project/system does. Include approach to memory/orchestration if applicable.]
version: "1.0"
agent_roles:
  - orchestrator
  - implementer
  - reviewer
  - researcher
preferred_models:
  claude-sonnet: [architecture, review, complex-reasoning]
  gemini-flash: [implementation, boilerplate, structured-output]
  claude-haiku: [research, documentation]
tools_allowed:
  - bash
  - git
  - mcp_nova
context_files:
  - forgemaster/AGENTS.md
  - forgemaster/SKILL_LIBRARY.md
max_context_usage: 0.5
verification_priority: high
---

# [Project Name]

## Project Context
- **Purpose**: [What this project is for]
- **Stack**: [Key technologies]
- **Status**: active

## Agent Configuration
[How agents in this project collaborate. Reference sub-agents with `@` paths.]

- Primary: `@agents/[division]/[agent-name].md`
- Reviewer: `@agents/[division]/[reviewer-agent].md`

## Collaboration Protocol

**Default pattern**: Question → Options → Decision → Draft → Approval

Human-in-the-loop gates:
1. **Design approval** — before any implementation begins
2. **Plan sign-off** — before writing code
3. **PR review** — before any merge

Agents MUST ask "May I write to [filepath]?" before file edits.

## Tool Permissions
**Allowed**: [list tools]  
**Forbidden**: [list any restricted tools for this project]

## Skill References
```
@forgemaster/library/[domain]/[skill].md
@forgemaster/library/[domain]/[skill].md
```

## Memory Configuration
- **Shard**: `[project-name]`
- **Retention**: persistent
- **NOVA endpoint**: stdio (mcp_nova)
