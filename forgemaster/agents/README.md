# Forgemaster Agents — Index

Agent persona definitions used by Forgemaster's orchestration lanes. Each `.md` file is a single agent with frontmatter (`name`, `description`) followed by role instructions. Authoring standard: see `forgemaster/STANDARDS.md`.

All agents conform to the preferred-model routing defined in `forgemaster/AGENTS.md`:
- `claude-sonnet` — architecture, review, complex reasoning
- `gemini-flash` — implementation, boilerplate, structured output
- `claude-haiku` — research, documentation, fast tasks

## Divisions

| Division | Path | Count | Focus |
|---|---|---:|---|
| Academic | `academic/` | 5 | Research specialists across disciplines |
| Design | `design/` | 8 | UI/UX, brand, visual systems |
| Engineering | `engineering/` | 20 | Language/framework specialists, infra, architecture |
| Examples | `examples/` | 5 | Reference agents for authors writing new ones |
| Game Development | `game-development/` | 132 | Full studio pipeline (design, art, code, audio, production) |
| Integrations | `integrations/` | 12 | Third-party services and API bridges |
| Marketing | `marketing/` | 27 | Content, SEO, growth, social |
| openFang | `openFang/` | 1 | openFang reference persona |
| Paid Media | `paid-media/` | 7 | Ads, performance marketing |
| Product | `product/` | 5 | PM, discovery, roadmapping |
| Project Management | `project-management/` | 24 | Agile, scrum, coordination |
| Research | `research/` | 2 | Broad investigation |
| Sales | `sales/` | 8 | Outbound, enablement, CRM |
| Spatial Computing | `spatial-computing/` | 6 | AR/VR/XR specialists |
| Specialized | `specialized/` | 27 | Cross-domain niche roles |
| Strategy | `strategy/` | 16 | Business, competitive, market |
| Support | `support/` | 6 | Customer support, technical support |
| Testing | `testing/` | 8 | QA, automation, performance |

**Total:** 18 divisions, ~319 agents.

## Loading an agent

Agents are loaded by Forgemaster when a ticket's `division` + `skill` combination matches. Manual loads: read the relevant `.md` file directly. The frontmatter is the contract; the body is the persona briefing.

## Authoring new agents

Use `forgemaster/templates/` as a starting point and follow `forgemaster/STANDARDS.md` — `name + description` frontmatter only (no color/emoji/vibe fields). See `examples/` for reference implementations.
