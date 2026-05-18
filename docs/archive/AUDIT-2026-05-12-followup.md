# NOVA Audit Follow-up Patch Package — 2026-05-12

Reference patches for the deferred work in `docs/AUDIT-2026-05-12.md`. None of these have been applied — they are drafted here so the next pass can execute them as a focused PR.

## 1. Permission whitelist fix (HIGH, security)

`_ALL_TOOL_NAMES` in `mcp/nova_server.py` is grouped by tool module, not alphabetically sorted. Insert the facts tools after the Wiki block and before Nidhogg so the tuple matches the existing module-grouping style.

```diff
--- a/mcp/nova_server.py
+++ b/mcp/nova_server.py
@@ -295,6 +295,9 @@ _ALL_TOOL_NAMES: tuple[str, ...] = (
     "nova_wiki_get",
     "nova_wiki_list",
     "nova_wiki_lint",
+    # Facts tools (registered via facts.register_facts_tools)
+    "nova_facts_search",
+    "nova_facts_rebuild",
     # Nidhogg tools (registered via nidhogg.register_nidhogg_tools)
     "nidhogg_ingest",
     "nidhogg_scan",
```

## 2. Agent rename map

```
forgemaster/agents/spatial-computing/macos-spatial-metal-engineer.md       → forgemaster/agents/spatial-computing/spatial-computing-macos-spatial-metal-engineer.md
forgemaster/agents/spatial-computing/terminal-integration-specialist.md    → forgemaster/agents/spatial-computing/spatial-computing-terminal-integration-specialist.md
forgemaster/agents/spatial-computing/visionos-spatial-engineer.md          → forgemaster/agents/spatial-computing/spatial-computing-visionos-spatial-engineer.md
forgemaster/agents/spatial-computing/xr-cockpit-interaction-specialist.md  → forgemaster/agents/spatial-computing/spatial-computing-xr-cockpit-interaction-specialist.md
forgemaster/agents/spatial-computing/xr-immersive-developer.md             → forgemaster/agents/spatial-computing/spatial-computing-xr-immersive-developer.md
forgemaster/agents/spatial-computing/xr-interface-architect.md             → forgemaster/agents/spatial-computing/spatial-computing-xr-interface-architect.md

forgemaster/agents/specialized/accounts-payable-agent.md                   → forgemaster/agents/specialized/specialized-accounts-payable-agent.md
forgemaster/agents/specialized/agentic-identity-trust.md                   → forgemaster/agents/specialized/specialized-agentic-identity-trust.md
forgemaster/agents/specialized/agents-orchestrator.md                      → forgemaster/agents/specialized/specialized-agents-orchestrator.md
forgemaster/agents/specialized/automation-governance-architect.md          → forgemaster/agents/specialized/specialized-automation-governance-architect.md
forgemaster/agents/specialized/blockchain-security-auditor.md              → forgemaster/agents/specialized/specialized-blockchain-security-auditor.md
forgemaster/agents/specialized/compliance-auditor.md                       → forgemaster/agents/specialized/specialized-compliance-auditor.md
forgemaster/agents/specialized/corporate-training-designer.md              → forgemaster/agents/specialized/specialized-corporate-training-designer.md
forgemaster/agents/specialized/data-consolidation-agent.md                 → forgemaster/agents/specialized/specialized-data-consolidation-agent.md
forgemaster/agents/specialized/government-digital-presales-consultant.md   → forgemaster/agents/specialized/specialized-government-digital-presales-consultant.md
forgemaster/agents/specialized/healthcare-marketing-compliance.md          → forgemaster/agents/specialized/specialized-healthcare-marketing-compliance.md
forgemaster/agents/specialized/identity-graph-operator.md                  → forgemaster/agents/specialized/specialized-identity-graph-operator.md
forgemaster/agents/specialized/lsp-index-engineer.md                       → forgemaster/agents/specialized/specialized-lsp-index-engineer.md
forgemaster/agents/specialized/recruitment-specialist.md                   → forgemaster/agents/specialized/specialized-recruitment-specialist.md
forgemaster/agents/specialized/report-distribution-agent.md                → forgemaster/agents/specialized/specialized-report-distribution-agent.md
forgemaster/agents/specialized/sales-data-extraction-agent.md              → forgemaster/agents/specialized/specialized-sales-data-extraction-agent.md
forgemaster/agents/specialized/study-abroad-advisor.md                     → forgemaster/agents/specialized/specialized-study-abroad-advisor.md
forgemaster/agents/specialized/supply-chain-strategist.md                  → forgemaster/agents/specialized/specialized-supply-chain-strategist.md
forgemaster/agents/specialized/zk-steward.md                               → forgemaster/agents/specialized/specialized-zk-steward.md
```

Total: 6 renames in `spatial-computing/`, 18 in `specialized/` (24 total).

## 3. Move list — agents → resources

| Source | Target | Bucket |
|---|---|---|
| `forgemaster/agents/examples/` | `forgemaster/resources/examples/agents/` | examples |
| `forgemaster/agents/strategy/` | `forgemaster/resources/runbooks/strategy/` | runbooks |
| `forgemaster/agents/research/` | `forgemaster/resources/research/agents/` | research |
| `forgemaster/agents/game-development/blender/` | `forgemaster/resources/engine-reference/game-development/blender/` | engine-reference |
| `forgemaster/agents/game-development/godot/` | `forgemaster/resources/engine-reference/game-development/godot/` | engine-reference |
| `forgemaster/agents/game-development/roblox-studio/` | `forgemaster/resources/engine-reference/game-development/roblox-studio/` | engine-reference |
| `forgemaster/agents/game-development/studio-docs/` | `forgemaster/resources/engine-reference/game-development/studio-docs/` | engine-reference |
| `forgemaster/agents/game-development/unity/` | `forgemaster/resources/engine-reference/game-development/unity/` | engine-reference |
| `forgemaster/agents/game-development/unreal-engine/` | `forgemaster/resources/engine-reference/game-development/unreal-engine/` | engine-reference |
| `forgemaster/agents/README.md` | `forgemaster/resources/templates/agents/README.md` | templates |
| `forgemaster/agents/game-development/studio-rules/ai-code.md` | `forgemaster/resources/templates/game-development/studio-rules/ai-code.md` | templates |
| `forgemaster/agents/game-development/studio-rules/data-files.md` | `forgemaster/resources/templates/game-development/studio-rules/data-files.md` | templates |
| `forgemaster/agents/game-development/studio-rules/design-docs.md` | `forgemaster/resources/templates/game-development/studio-rules/design-docs.md` | templates |
| `forgemaster/agents/game-development/studio-rules/engine-code.md` | `forgemaster/resources/templates/game-development/studio-rules/engine-code.md` | templates |
| `forgemaster/agents/game-development/studio-rules/gameplay-code.md` | `forgemaster/resources/templates/game-development/studio-rules/gameplay-code.md` | templates |
| `forgemaster/agents/game-development/studio-rules/narrative.md` | `forgemaster/resources/templates/game-development/studio-rules/narrative.md` | templates |
| `forgemaster/agents/game-development/studio-rules/network-code.md` | `forgemaster/resources/templates/game-development/studio-rules/network-code.md` | templates |
| `forgemaster/agents/game-development/studio-rules/prototype-code.md` | `forgemaster/resources/templates/game-development/studio-rules/prototype-code.md` | templates |
| `forgemaster/agents/game-development/studio-rules/shader-code.md` | `forgemaster/resources/templates/game-development/studio-rules/shader-code.md` | templates |
| `forgemaster/agents/game-development/studio-rules/test-standards.md` | `forgemaster/resources/templates/game-development/studio-rules/test-standards.md` | templates |
| `forgemaster/agents/game-development/studio-rules/ui-code.md` | `forgemaster/resources/templates/game-development/studio-rules/ui-code.md` | templates |
| `forgemaster/agents/integrations/README.md` | `forgemaster/resources/templates/integrations/README.md` | templates |
| `forgemaster/agents/integrations/aider/README.md` | `forgemaster/resources/templates/integrations/aider/README.md` | templates |
| `forgemaster/agents/integrations/antigravity/README.md` | `forgemaster/resources/templates/integrations/antigravity/README.md` | templates |
| `forgemaster/agents/integrations/claude-code/README.md` | `forgemaster/resources/templates/integrations/claude-code/README.md` | templates |
| `forgemaster/agents/integrations/cursor/README.md` | `forgemaster/resources/templates/integrations/cursor/README.md` | templates |
| `forgemaster/agents/integrations/gemini-cli/README.md` | `forgemaster/resources/templates/integrations/gemini-cli/README.md` | templates |
| `forgemaster/agents/integrations/github-copilot/README.md` | `forgemaster/resources/templates/integrations/github-copilot/README.md` | templates |
| `forgemaster/agents/integrations/mcp-memory/README.md` | `forgemaster/resources/templates/integrations/mcp-memory/README.md` | templates |
| `forgemaster/agents/integrations/openclaw/README.md` | `forgemaster/resources/templates/integrations/openclaw/README.md` | templates |
| `forgemaster/agents/integrations/opencode/README.md` | `forgemaster/resources/templates/integrations/opencode/README.md` | templates |
| `forgemaster/agents/integrations/windsurf/README.md` | `forgemaster/resources/templates/integrations/windsurf/README.md` | templates |

**Notes from Codex's live inspection:**
- No standalone `engine-reference/` directory exists under `game-development/`; that content is under `studio-docs/engine-reference/` and is covered by the `studio-docs/` row.
- The `integrations/` subdirectory contains only `README.md` files (no persona frontmatter), so all are proposed for the `templates` bucket.

## 4. Unified frontmatter schema proposal

Sampled schemas: base (`academic/academic-historian.md`), metadata-extended (`marketing/marketing-seo-specialist.md`, `product/product-manager.md`, `specialized/specialized-mcp-builder.md`), and technical-extended (`game-development/studio-agents/technical-director.md`, `unity-specialist.md`).

**Required fields**

- `id` (string): stable kebab-case identifier, preferably matching the filename without `.md`.
- `name` (string): display name used in routing and human-facing lists.
- `division` (string): top-level agent division, e.g. `academic`, `marketing`, `game-development`.
- `description` (string): concise routing description and scope.

**Optional fields**

- `tools` (list[string]): allowed tool names when the persona needs an explicit tool policy.
- `model` (string): model override for specialized execution.
- `max_turns` (integer): execution cap when different from orchestrator default.
- `disallowed_tools` (list[string]): explicit deny list for high-risk personas.
- `memory` (string): memory behavior such as `user`.
- `color` (string): UI color token or hex value.
- `emoji` (string): UI glyph for catalogs.
- `vibe` (string): short tone/working-style summary.
- `tags` (list[string]): searchable capabilities or domains.

**Migration matrix**

| Existing field | Unified field | Notes |
|---|---|---|
| `name` | `name` | Preserve display text. |
| filename stem | `id` | New required stable identifier. |
| parent directory | `division` | New required field inferred from path. |
| `description` | `description` | Preserve as-is. |
| `tools` | `tools` | Convert comma-string to YAML list. |
| `model` | `model` | Preserve. |
| `maxTurns` | `max_turns` | Rename to snake_case. |
| `disallowedTools` | `disallowed_tools` | Rename to snake_case. |
| `memory` | `memory` | Preserve. |
| `color`, `emoji`, `vibe` | same names (flat) | Preserve metadata-extended fields flat for low-friction migration. |

**Sample unified frontmatter**

```yaml
---
id: game-development-technical-director
name: Technical Director
division: game-development
description: Owns architecture-level technical decisions, technology evaluation, and cross-system technical risk.
tools:
  - Read
  - Glob
  - Grep
  - Write
  - Edit
  - Bash
  - WebSearch
model: opus
max_turns: 30
memory: user
color: slate
tags:
  - architecture
  - performance
  - risk-management
---
```
