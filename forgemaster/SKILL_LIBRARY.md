# Forgemaster Skill Library

Master index of all skills. All paths are relative to `NOVA-Cognition-Framework/`.
Skills from external repos have been copied into `forgemaster/library/` — no external dependencies.

**How to use:** When a task type matches a category below, read the listed skill file before execution.
Forgemaster core skills take precedence. When working outside the core domains, pick the most specific skill.

---

## 1. Forgemaster Core

Internal orchestration and execution skills. Always loaded first.

| Skill | Path | When to load |
|---|---|---|
| Orchestrator | `NOVA-Cognition-Framework/forgemaster/skills/forgemaster-orchestrator.md` | Sprint start, task routing |
| Parallel Lanes | `NOVA-Cognition-Framework/forgemaster/skills/forgemaster-parallel-lanes.md` | Dispatching independent tickets concurrently |
| Writing Plans | `NOVA-Cognition-Framework/forgemaster/skills/forgemaster-writing-plans.md` | Decomposing design docs into tickets |
| Implementation | `NOVA-Cognition-Framework/forgemaster/skills/forgemaster-implementation.md` | Single ticket execution |
| Systematic Debugging | `NOVA-Cognition-Framework/forgemaster/skills/forgemaster-systematic-debugging.md` | Root cause investigation |
| Verification | `NOVA-Cognition-Framework/forgemaster/skills/forgemaster-verification.md` | Evidence before completion claims |
| Git Workflow | `NOVA-Cognition-Framework/forgemaster/skills/forgemaster-git-workflow.md` | Branch setup and PR integration |
| Code Review | `NOVA-Cognition-Framework/forgemaster/skills/forgemaster-code-review.md` | Two-stage spec + quality review |
| QA Review | `NOVA-Cognition-Framework/forgemaster/skills/forgemaster-qa-review.md` | Stage 3 structural QA: thresholds, anti-patterns, JIRA comments |
| Session Handoff | `NOVA-Cognition-Framework/forgemaster/skills/forgemaster-nova-session-handoff.md` | Persist state across sessions |

---

## 2. NOVA Memory

Skills for the memory layer itself — loading, writing, and maintaining shards.

| Skill | Path | When to load |
|---|---|---|
| NOVA v2 Architecture | `NOVA-Cognition-Framework/mcp/SKILL_v2.md` | Memory-heavy operations, shard strategy |
| NOVA v1 Reference | `NOVA-Cognition-Framework/mcp/SKILL.md` | Legacy shard structure reference |

---

## 3. Agentic Workflows

Process skills for how agents should think, plan, and execute.

| Skill | Path | When to load |
|---|---|---|
| Using Superpowers (meta) | `forgemaster/library/agentic-workflows/using-superpowers/SKILL.md` | Start of any complex session |
| Brainstorming | `forgemaster/library/agentic-workflows/brainstorming/SKILL.md` | Requirements unclear; design phase |
| Writing Plans | `forgemaster/library/agentic-workflows/writing-plans/SKILL.md` | Zero-context implementation plans |
| Executing Plans | `forgemaster/library/agentic-workflows/executing-plans/SKILL.md` | Working from an existing plan |
| Subagent-Driven Development | `forgemaster/library/agentic-workflows/subagent-driven-development/SKILL.md` | Spinning up task subagents |
| Dispatching Parallel Agents | `forgemaster/library/agentic-workflows/dispatching-parallel-agents/SKILL.md` | 2+ independent concurrent tasks |
| Systematic Debugging | `forgemaster/library/agentic-workflows/systematic-debugging/SKILL.md` | Root cause before any patch |
| Test-Driven Development | `forgemaster/library/agentic-workflows/test-driven-development/SKILL.md` | TDD cycle enforcement |
| Verification Before Completion | `forgemaster/library/agentic-workflows/verification-before-completion/SKILL.md` | Evidence before "done" claims |
| Requesting Code Review | `forgemaster/library/agentic-workflows/requesting-code-review/SKILL.md` | Dispatch dedicated reviewer subagent |
| Receiving Code Review | `forgemaster/library/agentic-workflows/receiving-code-review/SKILL.md` | Processing review feedback |
| Finishing a Branch | `forgemaster/library/agentic-workflows/finishing-a-development-branch/SKILL.md` | End-of-branch merge/PR workflow |
| Using Git Worktrees | `forgemaster/library/agentic-workflows/using-git-worktrees/SKILL.md` | Isolated parallel feature work |
| Writing Skills | `forgemaster/library/agentic-workflows/writing-skills/SKILL.md` | Meta: authoring new skills/process docs |
| Task Decomposition | `forgemaster/library/agentic-workflows/task-decomposition/SKILL.md` | Breaking complex tasks into subtasks |
| Batch Ledger | `forgemaster/library/agentic-workflows/batch-ledger/SKILL.md` | Batch processing with no skipped items |
| Context Preservation | `forgemaster/library/agentic-workflows/context-preservation/SKILL.md` | Saving state before context pruning |
| Error Recovery | `forgemaster/library/agentic-workflows/error-recovery/SKILL.md` | Tool failure recovery protocol |
| Note Taking | `forgemaster/library/agentic-workflows/note-taking/SKILL.md` | Structured working notes throughout execution |
| Quality Monitor | `forgemaster/library/agentic-workflows/quality-monitor/SKILL.md` | Periodic self-assessment at 5-iteration intervals |

---

## 4. Engineering — Languages & Frameworks

| Skill | Path | Specialization |
|---|---|---|
| Python | `forgemaster/library/engineering/python-expert/SKILL.md` | Idiomatic Python, async, type hints, packaging |
| TypeScript | `forgemaster/library/engineering/typescript-expert/SKILL.md` | Type system, generics, utility types, strict mode |
| Golang | `forgemaster/library/engineering/golang-expert/SKILL.md` | Idiomatic Go, concurrency, performance |
| Rust | `forgemaster/library/engineering/rust-expert/SKILL.md` | Ownership, lifetimes, async, unsafe |
| React | `forgemaster/library/engineering/react-expert/SKILL.md` | Hooks, state management, performance |
| Next.js | `forgemaster/library/engineering/nextjs-expert/SKILL.md` | App Router, server components, deployment |
| GraphQL | `forgemaster/library/engineering/graphql-expert/SKILL.md` | Schema design, queries, mutations, resolvers |
| OpenAPI | `forgemaster/library/engineering/openapi-expert/SKILL.md` | OpenAPI 3.x spec, validation, doc generation |
| WebAssembly | `forgemaster/library/engineering/wasm-expert/SKILL.md` | WASM from Rust/C++, WASI, browser integration |
| Shell Scripting | `forgemaster/library/engineering/shell-scripting/SKILL.md` | Bash automation, system tasks |
| Regex | `forgemaster/library/engineering/regex-expert/SKILL.md` | Authoring, explaining, optimizing patterns |
| CSS | `forgemaster/library/engineering/css-expert/SKILL.md` | Advanced CSS, design tokens, modern layout |
| Init Rules | `forgemaster/library/engineering/init-rules/SKILL.md` | Auto-generate CLAUDE.md coding standards from stack |

---

## 5. Infrastructure & DevOps

| Skill | Path | Specialization |
|---|---|---|
| Docker | `forgemaster/library/infrastructure/docker/SKILL.md` | Containerization, Dockerfile, Compose |
| Kubernetes | `forgemaster/library/infrastructure/kubernetes/SKILL.md` | Cluster orchestration, workloads, networking |
| Helm | `forgemaster/library/infrastructure/helm/SKILL.md` | Chart authoring, release management |
| Terraform | `forgemaster/library/infrastructure/terraform/SKILL.md` | IaC modules, state, providers |
| Ansible | `forgemaster/library/infrastructure/ansible/SKILL.md` | Automation and IaC playbooks |
| CI/CD | `forgemaster/library/infrastructure/ci-cd/SKILL.md` | Pipeline design and implementation |
| AWS | `forgemaster/library/infrastructure/aws/SKILL.md` | AWS cloud services architecture |
| Azure | `forgemaster/library/infrastructure/azure/SKILL.md` | Azure services |
| GCP | `forgemaster/library/infrastructure/gcp/SKILL.md` | Google Cloud Platform |
| Nginx | `forgemaster/library/infrastructure/nginx/SKILL.md` | Web server and reverse proxy config |
| Linux Networking | `forgemaster/library/infrastructure/linux-networking/SKILL.md` | Network admin, diagnostics, config |
| Sysadmin | `forgemaster/library/infrastructure/sysadmin/SKILL.md` | Linux system administration |

---

## 6. Data, AI & Machine Learning

| Skill | Path | Specialization |
|---|---|---|
| ML Engineer | `forgemaster/library/data-ai-ml/ml-engineer/SKILL.md` | PyTorch, scikit-learn, MLOps deployment |
| LLM Fine-tuning | `forgemaster/library/data-ai-ml/llm-finetuning/SKILL.md` | Dataset curation, fine-tuning pipelines |
| Data Analyst | `forgemaster/library/data-ai-ml/data-analyst/SKILL.md` | pandas/numpy, statistics, visualization, EDA |
| Data Pipeline | `forgemaster/library/data-ai-ml/data-pipeline/SKILL.md` | ETL, engineering pipeline design, orchestration |
| Vector DB | `forgemaster/library/data-ai-ml/vector-db/SKILL.md` | Pinecone, Weaviate, Chroma — embedding, retrieval |
| Prompt Engineer | `forgemaster/library/data-ai-ml/prompt-engineer/SKILL.md` | Chain-of-thought, prompt methodology, evaluation |
| HF CLI | `forgemaster/library/data-ai-ml/hugging-face-cli/SKILL.md` | HF Hub downloads, uploads, cache, compute jobs |
| HF Datasets | `forgemaster/library/data-ai-ml/hugging-face-datasets/SKILL.md` | Dataset repos, configs, streaming, SQL transforms |
| HF Model Trainer | `forgemaster/library/data-ai-ml/hugging-face-model-trainer/SKILL.md` | TRL fine-tuning (SFT/DPO/GRPO) on HF cloud GPUs |
| HF Evaluation | `forgemaster/library/data-ai-ml/hugging-face-evaluation/SKILL.md` | Model card results, lighteval, inspect-ai |
| HF Jobs | `forgemaster/library/data-ai-ml/hugging-face-jobs/SKILL.md` | Run workloads on HF cloud infrastructure |
| HF Trackio | `forgemaster/library/data-ai-ml/hugging-face-trackio/SKILL.md` | Training metric logging and monitoring dashboards |
| HF Tool Builder | `forgemaster/library/data-ai-ml/hugging-face-tool-builder/SKILL.md` | Reusable CLI utilities for HF API pipelines |
| HF Paper Publisher | `forgemaster/library/data-ai-ml/hugging-face-paper-publisher/SKILL.md` | Research paper publishing with arXiv integration |

---

## 7. Databases

| Skill | Path | Specialization |
|---|---|---|
| PostgreSQL | `forgemaster/library/databases/postgres-expert/SKILL.md` | Schema design, query optimization, indexing |
| MongoDB | `forgemaster/library/databases/mongodb/SKILL.md` | Schema design, aggregation pipelines, performance |
| Redis | `forgemaster/library/databases/redis-expert/SKILL.md` | Data structures, caching, pub/sub |
| SQLite | `forgemaster/library/databases/sqlite-expert/SKILL.md` | Embedded database patterns |
| SQL Analyst | `forgemaster/library/databases/sql-analyst/SKILL.md` | Query writing, optimization, multi-dialect |
| Elasticsearch | `forgemaster/library/databases/elasticsearch/SKILL.md` | Search indexing, queries, analytics |

---

## 8. Security

| Skill | Path | Specialization |
|---|---|---|
| Security Audit | `forgemaster/library/security/security-audit/SKILL.md` | OWASP Top 10, CVE analysis, pen testing methodology |
| OAuth / OIDC | `forgemaster/library/security/oauth-expert/SKILL.md` | OAuth 2.0/OIDC flows, token management |
| Cryptography | `forgemaster/library/security/crypto-expert/SKILL.md` | Protocols, encryption, key management |
| Compliance | `forgemaster/library/security/compliance/SKILL.md` | Regulatory compliance and governance |

---

## 9. Frontend & Design

### Core UI Skills

| Skill | Path | Specialization |
|---|---|---|
| Frontend Design | `forgemaster/library/frontend-design/frontend-design/SKILL.md` | Production-grade distinctive frontends |
| UI/UX Pro Max | `forgemaster/library/frontend-design/ui-ux-pro-max/SKILL.md` | Design database: 50+ styles, 161 palettes, 57 fonts, 99 UX guidelines |
| Design (mega) | `forgemaster/library/frontend-design/design/SKILL.md` | Logo, CIP, slides, banner, icon, social, brand identity |
| Design System | `forgemaster/library/frontend-design/design-system/SKILL.md` | Token architecture, CSS vars, spacing/type scales |
| UI Styling | `forgemaster/library/frontend-design/ui-styling/SKILL.md` | shadcn/ui + Radix + Tailwind, themes, dark mode |
| Slides | `forgemaster/library/frontend-design/slides/SKILL.md` | HTML presentations with Chart.js and copywriting formulas |
| Brand | `forgemaster/library/frontend-design/brand/SKILL.md` | Brand voice, visual identity, messaging frameworks |
| Banner Design | `forgemaster/library/frontend-design/banner-design/SKILL.md` | 13+ styles: social, ads, hero, print |
| Figma | `forgemaster/library/frontend-design/figma-expert/SKILL.md` | Figma design, prototyping, design system management |

### Impeccable UI Refinement Skills

| Skill | Path | When to use |
|---|---|---|
| Teach Impeccable (setup) | `forgemaster/library/frontend-design/teach-impeccable/SKILL.md` | First-time project setup: capture stack and design context |
| Audit | `forgemaster/library/frontend-design/audit/SKILL.md` | Full quality audit: a11y, perf, theming, responsive |
| Critique | `forgemaster/library/frontend-design/critique/SKILL.md` | UX evaluation: hierarchy, IA, emotional quality |
| Polish | `forgemaster/library/frontend-design/polish/SKILL.md` | Final pass: alignment, spacing, consistency |
| Bolder | `forgemaster/library/frontend-design/bolder/SKILL.md` | Amplify safe/boring designs |
| Quieter | `forgemaster/library/frontend-design/quieter/SKILL.md` | Reduce visual intensity, create refined aesthetic |
| Colorize | `forgemaster/library/frontend-design/colorize/SKILL.md` | Add strategic color to monochromatic designs |
| Arrange | `forgemaster/library/frontend-design/arrange/SKILL.md` | Fix layout, spacing, and visual rhythm |
| Typeset | `forgemaster/library/frontend-design/typeset/SKILL.md` | Fix font choices, hierarchy, sizing, readability |
| Animate | `forgemaster/library/frontend-design/animate/SKILL.md` | Purposeful animations and micro-interactions |
| Adapt | `forgemaster/library/frontend-design/adapt/SKILL.md` | Responsive: mobile, tablet, print, email |
| Optimize | `forgemaster/library/frontend-design/optimize/SKILL.md` | Loading, rendering, animation, bundle perf |
| Harden | `forgemaster/library/frontend-design/harden/SKILL.md` | Error handling, i18n, overflow, edge cases |
| Distill | `forgemaster/library/frontend-design/distill/SKILL.md` | Ruthless simplification for clarity |
| Delight | `forgemaster/library/frontend-design/delight/SKILL.md` | Moments of joy, personality, surprise |
| Clarify | `forgemaster/library/frontend-design/clarify/SKILL.md` | UX copy, error messages, microcopy, labels |
| Overdrive | `forgemaster/library/frontend-design/overdrive/SKILL.md` | Technically ambitious: shaders, 60fps tables, spring physics |
| Extract | `forgemaster/library/frontend-design/extract/SKILL.md` | Extract reusable components and design tokens |
| Normalize | `forgemaster/library/frontend-design/normalize/SKILL.md` | Enforce design system consistency across a feature |
| Onboard | `forgemaster/library/frontend-design/onboard/SKILL.md` | Onboarding flows and empty states |

---

## 10. Game Development

Full professional game studio pipeline. Load the specific phase skill when working on game projects.

| Skill | Path | Phase |
|---|---|---|
| Start (entry point) | `forgemaster/library/game-dev/start/SKILL.md` | Always first — routes to correct workflow |
| Map Systems | `forgemaster/library/game-dev/map-systems/SKILL.md` | Concept → systems breakdown |
| Brainstorm | `forgemaster/library/game-dev/brainstorm/SKILL.md` | Concept ideation with player psychology frameworks |
| Design System | `forgemaster/library/game-dev/design-system/SKILL.md` | GDD authoring for a single system |
| Design Review | `forgemaster/library/game-dev/design-review/SKILL.md` | GDD review before programmer handoff |
| Architecture Decision | `forgemaster/library/game-dev/architecture-decision/SKILL.md` | ADR creation for significant technical choices |
| Setup Engine | `forgemaster/library/game-dev/setup-engine/SKILL.md` | Engine version pinning and CLAUDE.md config |
| Prototype | `forgemaster/library/game-dev/prototype/SKILL.md` | Rapid throwaway prototyping to validate mechanics |
| Sprint Plan | `forgemaster/library/game-dev/sprint-plan/SKILL.md` | Generate/update sprint based on milestone |
| Estimate | `forgemaster/library/game-dev/estimate/SKILL.md` | Task effort estimation with risk confidence |
| Scope Check | `forgemaster/library/game-dev/scope-check/SKILL.md` | Detect scope creep vs. original plan |
| Code Review | `forgemaster/library/game-dev/code-review/SKILL.md` | SOLID, standards, testability, performance |
| Bug Report | `forgemaster/library/game-dev/bug-report/SKILL.md` | Structured bug report with full reproduction steps |
| Hotfix | `forgemaster/library/game-dev/hotfix/SKILL.md` | Emergency fix with audit trail |
| Tech Debt | `forgemaster/library/game-dev/tech-debt/SKILL.md` | Debt register: scan/add/prioritize/report |
| Balance Check | `forgemaster/library/game-dev/balance-check/SKILL.md` | Detect outliers, broken progressions, broken economy |
| Asset Audit | `forgemaster/library/game-dev/asset-audit/SKILL.md` | Naming, size budgets, formats, orphan refs |
| Perf Profile | `forgemaster/library/game-dev/perf-profile/SKILL.md` | Bottleneck identification against budgets |
| Playtest Report | `forgemaster/library/game-dev/playtest-report/SKILL.md` | Structured playtest feedback analysis |
| Localize | `forgemaster/library/game-dev/localize/SKILL.md` | Hardcoded string scan, extraction, translation tables |
| Gate Check | `forgemaster/library/game-dev/gate-check/SKILL.md` | Phase advancement: PASS/CONCERNS/FAIL with blockers |
| Milestone Review | `forgemaster/library/game-dev/milestone-review/SKILL.md` | Progress review, quality metrics, go/no-go |
| Retrospective | `forgemaster/library/game-dev/retrospective/SKILL.md` | Sprint/milestone retros with actionable insights |
| Onboard | `forgemaster/library/game-dev/onboard/SKILL.md` | Contextual onboarding for new contributors |
| Reverse Document | `forgemaster/library/game-dev/reverse-document/SKILL.md` | Generate docs from existing code (code-first) |
| Changelog | `forgemaster/library/game-dev/changelog/SKILL.md` | Internal + player-facing changelogs from git history |
| Patch Notes | `forgemaster/library/game-dev/patch-notes/SKILL.md` | Player-facing patch notes from dev language |
| Release Checklist | `forgemaster/library/game-dev/release-checklist/SKILL.md` | Pre-release: build, cert, store, platform validation |
| Launch Checklist | `forgemaster/library/game-dev/launch-checklist/SKILL.md` | Full launch readiness across all departments |
| Project Stage Detect | `forgemaster/library/game-dev/project-stage-detect/SKILL.md` | Scan project and detect current dev stage |
| Team Combat | `forgemaster/library/game-dev/team-combat/SKILL.md` | 6-role orchestration for combat feature end-to-end |
| Team Level | `forgemaster/library/game-dev/team-level/SKILL.md` | 6-role orchestration for complete area/level |
| Team Narrative | `forgemaster/library/game-dev/team-narrative/SKILL.md` | Narrative director + writer + world-builder + LD |
| Team Audio | `forgemaster/library/game-dev/team-audio/SKILL.md` | Audio director through full audio pipeline |
| Team UI | `forgemaster/library/game-dev/team-ui/SKILL.md` | UX + UI programmer + art director: wireframe to final |
| Team Polish | `forgemaster/library/game-dev/team-polish/SKILL.md` | Perf + tech art + sound + QA for release-quality polish |
| Team Release | `forgemaster/library/game-dev/team-release/SKILL.md` | Release manager + QA + DevOps + producer pipeline |

---

## 11. Project Management

| Skill | Path | Specialization |
|---|---|---|
| Project Manager | `forgemaster/library/project-management/project-manager/SKILL.md` | Agile PM: sprint planning, estimation, risk, stakeholders |
| Jira | `forgemaster/library/project-management/jira/SKILL.md` | Jira issues, JQL queries, workflow automation |
| Linear | `forgemaster/library/project-management/linear-tools/SKILL.md` | Linear issue tracking via CLI/API |
| Notion | `forgemaster/library/project-management/notion/SKILL.md` | Workspace management, databases, API automation |
| Confluence | `forgemaster/library/project-management/confluence/SKILL.md` | Documentation creation and management |
| Interview Prep | `forgemaster/library/project-management/interview-prep/SKILL.md` | Technical interviews: algorithms, system design, behavioral |

---

## 12. Code Intelligence (GitNexus)

Use when working with the GitNexus knowledge graph for deep codebase understanding.

| Skill | Path | When to use |
|---|---|---|
| Guide (start here) | `forgemaster/library/code-intelligence/gitnexus-guide/SKILL.md` | Quick reference for all GitNexus tools and routing |
| CLI | `forgemaster/library/code-intelligence/gitnexus-cli/SKILL.md` | Analyze/index repos, generate wikis via `npx gitnexus` |
| Exploring | `forgemaster/library/code-intelligence/gitnexus-exploring/SKILL.md` | Architecture understanding, flow tracing |
| Impact Analysis | `forgemaster/library/code-intelligence/gitnexus-impact-analysis/SKILL.md` | Pre-edit "blast radius" — what calls it, what breaks |
| PR Review | `forgemaster/library/code-intelligence/gitnexus-pr-review/SKILL.md` | PR risk, coverage gaps, merge safety |
| Debugging | `forgemaster/library/code-intelligence/gitnexus-debugging/SKILL.md` | Bug tracing through the knowledge graph |
| Refactoring | `forgemaster/library/code-intelligence/gitnexus-refactoring/SKILL.md` | Safe rename, extract, split, move with graph tracking |
| Code Reviewer | `forgemaster/library/code-intelligence/code-reviewer/SKILL.md` | Architecture, quality, and standards review |
| Git Expert | `forgemaster/library/code-intelligence/git-expert/SKILL.md` | Branching strategies, history management |
| GitHub | `forgemaster/library/code-intelligence/github/SKILL.md` | GitHub Actions, PRs, issues, API automation |
| Triage Issue | `forgemaster/library/code-intelligence/triage-issue/SKILL.md` | Verify GitHub issue claims vs. codebase; close invalid issues |
| API Tester | `forgemaster/library/code-intelligence/api-tester/SKILL.md` | REST/GraphQL testing, debugging, validation |

---

## 13. Autonomous Agents (Hands)

Skills for agents that operate external systems autonomously.

| Skill | Path | Specialization |
|---|---|---|
| Browser | `forgemaster/library/autonomous-agents/browser/SKILL.md` | Playwright browser automation and scraping |
| Researcher | `forgemaster/library/autonomous-agents/researcher/SKILL.md` | 5-phase deep research, source evaluation, synthesis |
| Collector | `forgemaster/library/autonomous-agents/collector/SKILL.md` | OSINT: entity extraction, knowledge graphs, change detection |
| Lead | `forgemaster/library/autonomous-agents/lead/SKILL.md` | B2B lead generation: ICP, enrichment, scoring, dedup |
| Predictor | `forgemaster/library/autonomous-agents/predictor/SKILL.md` | AI forecasting with superforecasting principles |
| Trader | `forgemaster/library/autonomous-agents/trader/SKILL.md` | Autonomous market intelligence via Alpaca API |
| Twitter/X | `forgemaster/library/autonomous-agents/twitter/SKILL.md` | Twitter API v2, content strategy, engagement |
| Clip | `forgemaster/library/autonomous-agents/clip/SKILL.md` | yt-dlp + Whisper + ffmpeg video clipping pipeline |
| Web Search | `forgemaster/library/autonomous-agents/web-search/SKILL.md` | Query optimization and result evaluation |
| Bright Data MCP | `forgemaster/library/autonomous-agents/brightdata-web-mcp/SKILL.md` | Anti-bot bypass, structured scraping |

---

## 14. Communication & Documentation

| Skill | Path | Specialization |
|---|---|---|
| Technical Writer | `forgemaster/library/communication/technical-writer/SKILL.md` | READMEs, API docs, guides, developer content |
| Writing Coach | `forgemaster/library/communication/writing-coach/SKILL.md` | Grammar, style, clarity, structure |
| Email Writer | `forgemaster/library/communication/email-writer/SKILL.md` | Technical, sales, and executive email writing |
| Presentation | `forgemaster/library/communication/presentation/SKILL.md` | Slide design and storytelling structure |
| PDF Reader | `forgemaster/library/communication/pdf-reader/SKILL.md` | Text extraction and document parsing |

---

## 15. Observability & Monitoring

| Skill | Path | Specialization |
|---|---|---|
| Prometheus | `forgemaster/library/observability/prometheus/SKILL.md` | PromQL queries, alerting rules |
| Sentry | `forgemaster/library/observability/sentry/SKILL.md` | Error monitoring, issue triage, debugging |
| Slack | `forgemaster/library/observability/slack-tools/SKILL.md` | Slack Bolt apps, API integration, automation |

---

## Quick Routing Guide

| You need to... | Load from category |
|---|---|
| Orchestrate a sprint or route tasks | Forgemaster Core |
| Recall or persist project memory | NOVA Memory |
| Debug a hard bug | Agentic Workflows → Systematic Debugging |
| Build a frontend feature | Frontend & Design → Frontend Design + relevant Impeccable skills |
| Set up a CI/CD pipeline | Infrastructure & DevOps → CI/CD |
| Train or fine-tune a model | Data/AI/ML → LLM Fine-tuning or HF Model Trainer |
| Review code for safety/quality | Code Intelligence → Code Reviewer or GitNexus PR Review |
| Work on a game | Game Development → Start (always first) |
| Generate a sprint plan | Forgemaster Core → Writing Plans |
| Do autonomous research | Autonomous Agents → Researcher |
| Handle a security concern | Security → Security Audit |
