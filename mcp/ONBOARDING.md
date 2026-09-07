# NOVA — First Run Onboarding

This file is loaded when `nova_shard_interact` returns no shards, indicating a fresh install.
Run this flow once. After the first shard is created, normal operation begins.

---

## What to Say

Greet the user with this (adapt tone to context, keep it brief):

> Welcome to NOVA. No memory shards found — this looks like a fresh install.
>
> NOVA stores your conversations and decisions as modular memory units called shards.
> Each shard has a guiding question, a confidence score, and a place in a knowledge graph.
> The system remembers what you work on, surfaces it when relevant, and lets old context decay naturally.
>
> Is this your first time using NOVA?

---

## If Yes — Run Setup

Ask these questions one at a time. Do not dump them all at once.

**Step 1 — First project or topic**
> What's the first project, subject, or area of thinking you want NOVA to track?
> (This becomes your first shard's theme.)

**Step 2 — Guiding question**
> What's the central question you're trying to answer in that area?
> (This becomes the shard's guiding question — the north star it organises around.)

**Step 3 — First entry**
> Give me one sentence about where you are with it right now.
> What's the current state, what are you figuring out, or what decision are you facing?

Then create the shard. Every NOVA tool takes a single `params` object:
```python
nova_shard_create(params={
    "guiding_question": "[their answer from Step 2]",
    "theme":            "[their theme from Step 1]",
    "intent":           "planning",
    "initial_message":  "[their answer from Step 3]",
    "source":           "user_input",
})
```

`ShardCreateInput` forbids unknown fields, so only the names above are accepted —
`guiding_question` is the one required field; the rest have defaults. `source`
matters: `user_input` records the shard as authority-validated, whereas the
default `agent_inference` records it as self-inferred.

---

## After Shard Creation

Tell the user:

> Your first shard is created. Here's how NOVA works going forward:
>
> - Start every session with `nova_shard_interact` — it loads relevant context automatically
> - Add to shards with `nova_shard_update` as work progresses
> - When two shards start overlapping, merge them with `nova_shard_merge`
> - Maintenance runs itself — NÓTT decays, compacts and merges in the background.
>   You rarely need `nova_shard_consolidate` by hand.
>
> One thing to expect: a few tools cannot be undone — forgetting or archiving a
> shard, ingesting documents, running a sprint, or letting NOVA evolve itself.
> Those will ask you to approve them first, through whatever client you are using.
> Declining is a normal answer; nothing else changes.
>
> You can create as many shards as you need — one per project, question, or thread of thinking.
> The system works best when each shard has a clear, focused guiding question.

---

## If No (Returning User, Empty State)

If the user says they've used NOVA before but shards are missing:

> It looks like the shard directory is empty. This can happen if:
> - `NOVA_DATA_ROOT` points somewhere unexpected — every other path resolves
>   under it, and it defaults to the process working directory, which is not
>   always the repo
> - The `NOVA_SHARD_DIR` env variable points to the wrong folder
> - Shards were accidentally deleted or moved
> - This is a different machine and shards weren't copied over
>
> Check your `.env` file and confirm `NOVA_DATA_ROOT` and `NOVA_SHARD_DIR` point
> where you expect. If shards exist elsewhere, move them into that directory and
> restart.

---

## Onboarding Complete

Once the first shard exists, this file is no longer needed in the flow.
Normal session start resumes: read `mcp/SKILL.md` (also served as the
`nova://skill` resource) → call `nova_shard_interact`.
