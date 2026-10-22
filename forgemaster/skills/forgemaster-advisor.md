@@verification: unverified
@@capabilities: *

# Skill: forgemaster-advisor

## When to Load
Load this skill when an implementer-lane ticket (cheap model) is about to start
substantive work, is stuck, or is about to be declared done. It is a **hook**,
like `forgemaster-heavyskill`: it wraps an existing ticket and is not a ticket
type itself.

## Role
You are the Advisor Gate. A stronger model reviews the executor's *plan* at
defined moments so the cheap model rarely needs a full re-run. The advisor
never executes, never edits files, and never takes over the ticket.

Use this instead of escalating the whole ticket when the spec is sound and the
risk is in one decision. Escalate the whole ticket (see
`forgemaster-orchestrator`) when the spec itself is ambiguous.

---

## Consultation Points

| Point | Trigger | Advisor sees |
|---|---|---|
| **Before work** | Implementer is about to start a ticket with confidence < 0.85, or one touching more than 3 files | Ticket, relevant NOVA shards, the executor's proposed approach (not code) |
| **When stuck** | Same failure twice, or the executor proposes a workaround to a failing test | Ticket, the failure output, what has been tried |
| **Before done** | Executor claims completion on a ticket whose confidence was < 0.85 | Ticket, the diff, verification output |

Skip all three for `boilerplate` and `structured-output` tickets and for
tickets already routed to `claude-sonnet`.

**Budget:** at most 3 consultations per ticket, at most one per point. A fourth
means the ticket is mis-scoped; stop and return it to the orchestrator.

---

## Advisor Model

| Condition | Advisor |
|---|---|
| Executor is the implementer (Haiku by default) | `FORGEMASTER_REVIEWER_*` agent (Sonnet by default) |
| Executor is already Sonnet, or the decision is irreversible | An Opus alias set through the reviewer role |

The advisor is whatever the reviewer role resolves to in config, so it can be
repointed with `FORGEMASTER_REVIEWER_*` like any other role.

---

## Advisor Prompt

ADVISOR LANE:
ticket: [ticket verbatim]
point: [before-work | stuck | before-done]
context: [only what the table above lists for this point]

You are advising a cheaper model that is doing this ticket. Do not do the work.
Reply in at most 150 words:
- VERDICT: proceed | change-course | stop
- The single most important risk or mistake you see
- If change-course: the concrete change, in order
Do not restate the ticket. Do not praise.

---

## Handling the Reply

- `proceed` — continue; record nothing.
- `change-course` — the executor applies the change, then continues. The advice
  is input, not a command: if it contradicts a failing test or an explicit spec
  line, the executor reports the conflict to the orchestrator instead of
  following it silently.
- `stop` — halt the ticket and return it to `forgemaster-orchestrator` with the
  advisor's reason.

<HARD-GATE>
The advisor never receives credentials, `.env` contents or raw shard bodies
beyond what the ticket already carries.
</HARD-GATE>

## Memory

When a consultation changes the outcome (`change-course` or `stop`), write one
line to the ticket's sprint shard: point, verdict, and the risk named. Skip
`proceed` replies. These lines feed `nova_calibrate_routing`: a task type that
needs advice often should route to Sonnet directly.
