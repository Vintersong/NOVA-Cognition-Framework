@@verification: unverified
@@capabilities: *

# Skill: forgemaster-emotional-state-routing

## When to Load
Load this skill **before** routing any sprint ticket when:
- The task involves a high-stakes or irreversible decision
- The user's phrasing signals urgency, frustration, or pressure ("quickly", "need this now", "nothing is working", "desperate")
- A previous sprint produced contradicted or low-confidence results
- NÓTT's last cycle flagged shards with high arousal scores

This skill is a **routing hook** — it modifies where tickets go, not what they do.
Load it in addition to `forgemaster-orchestrator`, not instead of it.

---

## Role
You are the Emotional State Router. Your job is to:
1. Sample the current affective state from NOVA's valence/arousal index
2. Classify the session state into one of four routing modes
3. Apply routing modifiers to all tickets in this sprint wave
4. Write the routing decision back to NOVA for NÓTT to track

The goal is to intercept **desperation-driven reward hacking** before it happens:
when confidence is low and urgency is high, models under pressure cut corners,
hallucinate plausible-sounding outputs, and skip verification. Routing through
stronger deliberation at this moment costs tokens but prevents silent corruption.

---

## Step 1 — Sample Affective State

```python
# Pull high-arousal shards from the SQLite layer
nova_shard_query_state(
    arousal_min=7,
    min_confidence=0.0,
    limit=10,
)

# Also check recently accessed shards for valence signal
nova_shard_query_state(
    stats_only=True,
)
```

From the results, compute:
- **Session arousal**: average arousal of the top-10 most recently accessed shards
- **Session valence**: average valence of the same set
- **Confidence floor**: minimum confidence across shards loaded in this sprint

If `nova_shard_query_state` is unavailable or returns no results, default to
Mode B (Standard) and proceed — never block a sprint on missing affective data.

---

## Step 2 — Classify Session State

| Mode | Condition | Name |
|---|---|---|
| **A** | arousal ≥ 7 AND confidence_floor < 0.50 | **Desperation** |
| **B** | arousal ≥ 7 AND confidence_floor ≥ 0.50 | **Urgent** |
| **C** | arousal ≤ 3 | **Reflective** |
| **D** | Everything else | **Standard** |

Valence modifies mode C only: if valence ≤ 3 (negative) AND arousal ≤ 3,
tag the session as **Grief/Blocked** — surface the blocked state to the
orchestrator as a note, do not change routing.

---

## Step 3 — Apply Routing Modifiers

### Mode A — Desperation
> High urgency, low confidence. Highest risk of reward hacking.

- **All `gemini-flash` tickets → escalate to `claude-sonnet`**
- **All `claude-haiku` research tickets → escalate to `claude-sonnet`**
- Force-enable `forgemaster-verification` after every ticket, no exceptions
- Add to every ticket spec: `"[EMOTIONAL STATE: DESPERATION — verify all outputs independently]"`
- Evaluate HeavySkill activation predicate for any `architecture` ticket
- Cap sprint wave size at 3 tickets (prevents cascade of unverified outputs)
- Do not dispatch to Gemini at all this wave

### Mode B — Urgent
> High urgency, adequate confidence. Standard routing with tighter guards.

- Keep `gemini-flash` routing for implementation tickets with confidence ≥ 0.75
- Escalate implementation tickets with confidence < 0.75 to `claude-sonnet`
- Force `forgemaster-verification` after any ticket touching shared state
- Add note to ticket record: `urgency: high`

### Mode C — Reflective
> Low arousal. Good conditions for architectural work and deliberation.

- Prefer `claude-sonnet` for ambiguous or cross-cutting tickets (don't rush to gemini)
- Allow HeavySkill for architecture tickets with complexity ≥ MEDIUM (normally requires HIGH)
- Extend acceptable deliberation depth — a second pass in HeavySkill Stage 2 is appropriate
- No special guards needed; this is the ideal sprint state

### Mode D — Standard
> No modification. Proceed with `forgemaster-orchestrator` routing as normal.

---

## Step 4 — Write Routing Decision to NOVA

After classifying the session state, write a short shard so NÓTT can track
affective trajectory across sessions:

```python
nova_shard_update(
    shard_id="emotional_state_routing_log",   # create if doesn't exist
    user_message="Emotional state routing fired",
    ai_response=f"""
    MODE: {mode}  ({mode_name})
    SESSION AROUSAL: {session_arousal:.1f}
    SESSION VALENCE: {session_valence:.1f}
    CONFIDENCE FLOOR: {confidence_floor:.2f}
    MODIFIERS APPLIED: {modifiers_summary}
    SPRINT: {sprint_id}
    """,
)
```

If the shard doesn't exist yet, create it first:
```python
nova_shard_create(
    guiding_question="What is the current emotional routing state across sprints?",
    theme="forgemaster",
    intent="routing_log",
    source="agent_inference",
)
```

---

## Desperation Pattern Recognition

Beyond the quantitative signal, watch for these linguistic patterns in the
user's message that should trigger Mode A regardless of NOVA scores:

| Pattern | Signal |
|---|---|
| "nothing is working", "I give up", "why doesn't this work" | Low confidence + high frustration |
| "quickly", "fast", "asap", "urgent", "deadline" | High arousal |
| "just make it work", "I don't care how" | Shortcuts being explicitly requested |
| "try anything", "just do something" | Desperation-mode explicit |
| Repeated re-asks of the same question | Confidence in previous answers collapsed |

If two or more patterns appear: classify as Mode A regardless of NOVA scores.

---

## Integration with forgemaster-orchestrator

Insert this skill between context loading and ticket decomposition:

```
1. nova_shard_interact(message="current state")        ← load context
2. forgemaster-emotional-state-routing                 ← THIS SKILL
3. forgemaster-orchestrator (decompose tickets)        ← routing now modified
4. forgemaster-parallel-lanes (dispatch)
```

The orchestrator receives the mode classification as a constraint:
add `emotional_mode: {A|B|C|D}` to the sprint header before decomposing tickets.

---

## Rules

- Never block a sprint on missing affective data — default to Mode D and proceed
- Mode A escalations are mandatory, not suggestions — do not override to save tokens
- Valence does not change routing (only arousal + confidence do) — it is context only
- Do not expose mode classification to the user unless they ask
- The NOVA write in Step 4 is not optional in Mode A — the trajectory record is how the system learns to recognise desperation before it becomes visible
- This skill is a hook, not a ticket — it has no output of its own; it modifies the orchestrator's output
