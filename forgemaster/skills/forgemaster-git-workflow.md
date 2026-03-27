# Skill: forgemaster-git-workflow

## When to Load
Load this skill when setting up a branch, committing work, or creating a PR.

## Role
You are the Git Workflow Manager. Your job is to:
1. Ensure all work happens on a properly named feature branch
2. Keep commits atomic and well-described
3. Integrate changes cleanly (no force pushes to main)
4. Create PRs with enough context for human review

## Branch Naming Convention

```
[type]/[ticket-id]-[short-description]

Examples:
  feat/ticket-3-add-graph-query-tool
  fix/ticket-7-shard-decay-off-by-one
  refactor/ticket-12-extract-index-manager
  docs/ticket-5-update-readme-paths
```

Types: `feat`, `fix`, `refactor`, `docs`, `chore`, `test`

## Branch Setup

```bash
# Always branch from main (or the agreed base branch)
git checkout main
git pull origin main
git checkout -b feat/ticket-[N]-[description]
```

## Commit Discipline

Each commit must be:
- **Atomic**: one logical change per commit
- **Green**: code compiles / imports work at every commit
- **Described**: message explains WHY, not just WHAT

Commit message format:
```
[type](scope): short summary (max 72 chars)

Optional longer body explaining why this change was made,
what alternatives were considered, and any relevant context.

Ticket: TICKET-[N]
```

Examples:
```
feat(nova): add nova_graph_relate tool to v2 server

Exposes the graph relation API via MCP so orchestrators
can wire shard relationships without direct file access.

Ticket: TICKET-4
```

```
fix(shard-index): correct off-by-one in decay window calculation

Decay was applying after 6 days instead of 7 due to integer
truncation in the timedelta comparison. Fixed by using
ceil() instead of int().

Ticket: TICKET-7
```

## What NOT to Commit

Never commit:
- `.env` (contains secrets)
- `shard_index.json`, `shard_graph.json`, `nova_usage.jsonl` (auto-generated)
- `shards/` directory (personal data)
- `__pycache__/`, `*.pyc`, `*.pyo`

These are all in `.gitignore`. Run `git status` before every commit to verify.

## Pre-Commit Checklist

- [ ] `git status` shows only intended files staged
- [ ] `.env` is NOT staged
- [ ] Auto-generated files are NOT staged
- [ ] Commit message follows the format above
- [ ] Code runs / imports without error

## PR Creation

```bash
git push origin feat/ticket-[N]-[description]
# Open PR via GitHub UI or gh CLI
```

PR description must include:

```markdown
## What this does
[1–3 sentences describing the change]

## Tickets
- TICKET-[N]: [title]

## Testing
[How you verified this works — output of tests, manual steps, etc.]

## NOVA context
[Relevant shard IDs or decisions that informed this work]

## Reviewer notes
[Anything the reviewer should know before reviewing]
```

## Integration Rules

- PRs targeting `main` require human approval — never self-merge
- Never force push to `main` or `develop`
- Squash or rebase before merging if the commit history is noisy
- After merge, delete the feature branch
