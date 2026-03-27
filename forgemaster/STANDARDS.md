# Forgemaster Content Standards

**Version**: 1.0  
**Scope**: All files under `forgemaster/` — skills, agents, hooks, CLAUDE.md/AGENTS.md, slash commands, rules, and templates.

This document is the single source of truth for how every content type in this system must be structured. When authoring or reviewing files, validate against these schemas.

---

## Audit Summary

| Directory       | Files | Purpose                                      |
|-----------------|-------|----------------------------------------------|
| `agents/`       | 357   | Agent persona definitions (18 domain folders)|
| `library/`      | 208   | Reusable SKILL.md files (13 domain categories)|
| `slash-commands/`| 84   | Claude slash-command prompts                 |
| `workflows/`    | 56    | Step-by-step workflow processes              |
| `templates/`    | 60    | Project scaffolding and planning templates   |
| `rules/`        | 20    | Coding standards and hooks config            |
| `docs/`         | 18    | Reference indexes and architecture notes     |
| **TOTAL**       | **814**| All content files                           |

---

## 1. SKILL.md Standard

Skills live in `library/<domain>/SKILL.md` (or named descriptively, e.g. `brainstorming.md`).

### Required Frontmatter

```yaml
---
name: <domain>.<skill-name>          # kebab-case, dot-qualified by domain
version: "1.0"                       # semver string
description: >                       # one sentence, action-oriented, max 160 chars
  What this skill does and when to trigger it.
tags: [tag1, tag2, tag3]             # 2–5 lowercase tags for indexing
domain: engineering                  # matches library/ subdirectory name
author: forgemaster                  # original source or "forgemaster"
type: skill                          # always "skill" for library files
---
```

### Required Body Sections

```markdown
## Overview
One paragraph. What problem this skill solves. When the agent MUST use it.
Include any hard gates or mandatory triggers.

## When to Use
- Bullet list of trigger conditions (e.g. "before any implementation task")
- Include anti-patterns / when NOT to use

## Capabilities
- Bullet list of what the agent can do when this skill is active
- Keep concrete — reference specific commands, patterns, or tools

## Procedure
Numbered steps or structured sub-sections.
Use fenced code blocks for commands, schemas, or examples.

## Examples
At least one brief example showing the skill in action.
Use > blockquotes for agent responses, ``` for code.

## References
- Link to related skills: `@forgemaster/library/<domain>/<skill>.md`
- External sources (short title + URL)
```

### Rules
- File name: `<skill-name>.md` (kebab-case), or `SKILL.md` if it's the primary skill for that folder
- Max file size: 300 lines. Split into sub-skills if larger
- No inline HTML
- Hard gates must use fenced `<HARD-GATE>` blocks to stand out visually

---

## 2. Agent Definition Standard

Agent files live in `agents/<division>/<agent-name>.md`.

### Required Frontmatter

```yaml
---
name: <AgentName>                    # TitleCase display name
role: <brief role title>             # e.g. "Senior Frontend Engineer"
division: engineering                # matches agents/ subdirectory
tier: specialist                     # specialist | orchestrator | reviewer | researcher
model_preference: claude-sonnet      # claude-sonnet | gemini-flash | gpt-4o | any
description: >
  One sentence: what this agent does and its key differentiator.
emoji: 🛠️                           # single emoji representing the agent
color: "#3B82F6"                     # hex color for UI theming
tags: [tag1, tag2]                   # 2–5 tags for discovery
---
```

### Required Body Sections

```markdown
# <AgentName> — <Role Title>

> <vibe quote> — one punchy line that captures the agent's philosophy

## Identity
- **Personality**: How this agent communicates and approaches problems
- **Expertise**: Core knowledge domains and experience frame
- **Boundaries**: What this agent explicitly will not do

## Mission
Primary objective in 2–3 sentences.
What does "done" look like for this agent?

## Capabilities
### Core Skills
- ...

### Workflow
1. Step one
2. Step two
3. ...

## Deliverables
Concrete outputs this agent produces:
- [ ] Deliverable A — description
- [ ] Deliverable B — description

## Escalation
When to hand off to another agent or ask the human:
- **Escalate to human**: when [condition]
- **Hand off to**: `@<other-agent>` when [condition]

## Success Criteria
How to know this agent fulfilled its mission:
- ...
```

### Rules
- One agent per file
- `tier: orchestrator` agents must define a `## Coordination` section listing sub-agents they spawn
- Agent files MUST NOT contain implementation code — only behavior/persona specs
- Avoid first-person in frontmatter; use first-person only in body prose

---

## 3. Hooks Standard

Hooks live in `rules/hooks/<name>-hooks.json`.

### Schema

```json
{
  "version": "1.0",
  "description": "Short description of what these hooks do",
  "hooks": {
    "<EventName>": [
      {
        "id": "unique-hook-id",
        "description": "What this hook does",
        "matcher": "regex|keyword pattern for trigger",
        "type": "command",
        "command": "path/to/script arg1 arg2",
        "async": false,
        "timeout_ms": 5000,
        "conditions": {
          "require_files": ["optional/path/that/must/exist"],
          "env_vars": ["OPTIONAL_ENV_VAR"]
        }
      }
    ]
  }
}
```

### Supported Event Names

| Event | Trigger |
|-------|---------|
| `SessionStart` | Claude session initialization |
| `PreToolCall` | Before any tool is called |
| `PostToolCall` | After any tool call completes |
| `OnFileEdit` | When a file is written |
| `OnError` | When an error is detected |
| `SessionEnd` | When session ends or compacts |

### Rules
- Hook `id` field: `<event>-<purpose>` in kebab-case (e.g. `session-start-load-context`)
- Commands use environment variables for paths, never absolute user-specific paths
- `async: true` only for non-critical logging hooks
- All hooks must have `description` and `timeout_ms`

---

## 4. CLAUDE.md / AGENTS.md Standard

`CLAUDE.md` is placed at the project root (or domain root for multi-agent setups).  
`AGENTS.md` is the Forgemaster system-wide orchestration config.

### Required Frontmatter (YAML frontmatter for AGENTS.md)

```yaml
---
name: <system-or-project-name>
description: >
  What this agent system does. Include memory/orchestration approach.
version: "1.0"
agent_roles:
  - orchestrator
  - implementer
  - reviewer
  - researcher
preferred_models:
  claude-sonnet: [architecture, review, complex-reasoning]
  gemini-flash: [implementation, boilerplate, structured-output]
  gpt-4o: [research, documentation, web-search]
tools_allowed:
  - bash
  - git
  - mcp_nova          # list only what is actually permitted
context_files:        # files that must be loaded at session start
  - forgemaster/AGENTS.md
  - forgemaster/SKILL_LIBRARY.md
max_context_usage: 0.5
verification_priority: high   # high | medium | low
---
```

### Required Body Sections (for CLAUDE.md)

```markdown
# <Project Name>

## Project Context
- **Purpose**: What this project is for
- **Stack**: Key technologies
- **Status**: active | maintenance | archived

## Agent Configuration
How agents in this project collaborate.
Reference sub-agent files: `@agents/<division>/<name>.md`

## Collaboration Protocol
The required human-in-the-loop checkpoints:
1. [Gate 1]: e.g. "Design doc approval before implementation"
2. [Gate 2]: e.g. "Plan sign-off before writing code"
3. [Gate 3]: e.g. "PR review before merge"

**Default interaction pattern**: Question → Options → Decision → Draft → Approval

## Tool Permissions
Explicit list of allowed and forbidden tools in this project context.

## Skill References
Active skills for this context (Claude Code `@` file references):
- `@forgemaster/library/<domain>/<skill>.md`

## Memory Configuration
How NOVA/MCP memory is configured for this project.
- Shard: `<project-name>`
- Retention: session | persistent
```

### Rules
- `CLAUDE.md` at project root must have a `## Collaboration Protocol` section — no autonomous execution without documented gates
- Avoid duplicating content from `AGENTS.md` in project `CLAUDE.md` — use `@` references
- `max_context_usage` must be ≤ 0.7 to leave room for responses

---

## 5. Slash Command Standard

Slash commands live in `slash-commands/<name>.md` or `slash-commands/<group>/<name>.md`.

### Required Frontmatter

```yaml
---
description: >
  One sentence shown in command picker. Action-oriented. Max 100 chars.
usage: "/command-name [optional-arg]"
group: gsd                           # group folder name, or "global" if top-level
deprecated: false                    # true if replaced by a skill
replaces: ""                         # if deprecated, name the replacement
---
```

### Required Body

```markdown
<!-- The body IS the prompt sent to the model. Write it as instructions. -->

You are performing the <action> command. [Instructional prose...]

## Steps
1. ...
2. ...

## Output Format
Describe the expected response format.
```

### Rules
- File name is the command trigger: `do-task.md` → `/do-task`
- Deprecated commands must include `deprecated: true` and `replaces:` pointing to the skill
- No slash commands should duplicate an existing SKILL.md — link to the skill instead
- Max 50 lines per command; longer logic belongs in a workflow file

---

## 6. Rules / Coding Standard Files

Rules live in `rules/coding-standards/<base|languages|frameworks>/<name>.md`.

### Structure

```markdown
# <Language/Framework/Topic> Standards

> **Scope**: [What code or context these rules apply to]

## <Category>
- Rule stated as positive imperative ("Do X") or negative ("Never Y")
- One rule per bullet
- Include rationale in parentheses when non-obvious

## <Category 2>
...

## Enforcement
Note any linter, formatter, or tool that enforces these rules automatically.
```

### Rules
- No frontmatter on coding-standard files (they are referenced directly, not indexed)
- Keep opinionated rules separate from style rules (use H2 to distinguish)
- Cross-reference other standards: `See [TypeScript](./typescript.md) §Naming`

---

## 7. Workflow Files

Workflows live in `workflows/<name>.md`.

### Required Frontmatter

```yaml
---
name: <workflow-name>
description: >
  One sentence: what process this workflow orchestrates.
triggers: ["manual", "on-milestone", "on-task-complete"]
related_commands: ["/gsd-do", "/gsd-ship"]
related_agents: ["project-manager", "implementer"]
---
```

### Required Body

```markdown
## Purpose
What business problem this workflow solves.

## Prerequisites
- What must be true before starting
- Required files, context, or approvals

## Steps
### Phase 1: <Name>
1. ...
2. ...

### Phase 2: <Name>
1. ...

## Exit Criteria
How to know the workflow is complete.

## Artifacts
Files or outputs produced by this workflow.
```

---

## 8. Template Files

Templates live in `templates/<group>/<name>.md`.

### Required Frontmatter

```yaml
---
name: <template-name>
description: What document this template produces.
type: template
group: spec-kit                      # subfolder group name
fill_in_fields: ["PROJECT_NAME", "OWNER", "DATE"]
---
```

### Required Body

Use `[PLACEHOLDER]` syntax for all fill-in fields. Provide a brief comment before each section explaining what belongs there.

---

## Validation Checklist

Before committing any new or edited file, confirm:

- [ ] Frontmatter is present and complete
- [ ] All required sections exist
- [ ] File is under size limit (skills: 300 lines, agents: 200 lines, commands: 50 lines)
- [ ] Name fields use correct case convention (skills: `domain.name`, agents: `TitleCase`)
- [ ] No absolute user-specific paths in any file
- [ ] Agent files contain no implementation code
- [ ] Deprecated slash commands have `deprecated: true` and point to replacement
- [ ] CLAUDE.md files have a `## Collaboration Protocol` section with human gates

---

## Naming Conventions Quick Reference

| Type | Convention | Example |
|------|-----------|---------|
| Skill `name` field | `domain.skill-name` | `engineering.code-review` |
| Agent `name` field | `TitleCase` | `SeniorEngineer` |
| File names | `kebab-case.md` | `code-review.md` |
| Hook IDs | `event-purpose` kebab | `session-start-load-context` |
| Slash command file | matches trigger | `do-task.md` → `/do-task` |
| Template placeholders | `[ALL_CAPS]` | `[PROJECT_NAME]` |
| Tag values | lowercase, no spaces | `engineering`, `code-review` |

---

## File Placement Quick Reference

```
forgemaster/
├── agents/<division>/<agent-name>.md    # Agent persona definition
├── library/<domain>/<skill-name>.md     # Reusable skill
├── rules/
│   ├── coding-standards/<base|languages|frameworks>/<lang>.md
│   └── hooks/<name>-hooks.json          # Event hooks config
├── slash-commands/[<group>/]<cmd>.md    # Slash command prompt
├── workflows/<workflow-name>.md         # Multi-step process
├── templates/<group>/<template>.md      # Fill-in scaffold
├── docs/<reference-name>.md             # External source index
├── AGENTS.md                            # System orchestration config
├── CLAUDE.md                            # Project-level Claude config (at root)
└── SKILL_LIBRARY.md                     # Master skill index (auto-generated)
```
