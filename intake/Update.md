HeavySkill Hook for Claude Code

Two‑Stage Heavy Thinking as a Readable Skill
1. Overview

This document specifies how to implement HEAVYSKILL—a two‑stage “heavy thinking” pipeline (parallel reasoning then sequential deliberation)—as a hook skill inside Claude Code’s skills library and agentic harness.

The goal is to let Claude Code automatically switch into a more compute‑intensive reasoning mode on difficult, verifiable tasks, while staying dormant for simple or subjective work. The design mirrors the HEAVYSKILL paper’s “readable skill” structure (Activation Conditions, Parallel Reasoning Protocol, Deliberation Prompt, Output Constraints) and adapts it to Claude Code’s skill + subagent model.
2. Background and Motivation
HeavySkill concept (paper)

HEAVYSKILL abstracts complex agentic harnesses into a minimal two‑stage pipeline that can run beneath any orchestrator:

    Parallel reasoning phase

        Spawn KK independent reasoning trajectories for the same problem.

        Each trajectory is a full chain‑of‑thought plus an answer.

    Sequential deliberation phase

        A second model reads all trajectories from a serialized memory cache.

        It analyzes, compares, and synthesizes them into a final answer, rather than doing simple majority voting.

The authors show this “heavy thinking” mode consistently outperforms traditional Best‑of‑N / majority‑vote strategies across STEM, coding, and general reasoning tasks, especially when outputs are objectively verifiable.

They also provide a Claude Code skill file implementing this as a “readable skill” that the orchestrator can load into its context, with: activation conditions, parallel agent spawn instructions, a deliberation prompt, and strict output‑format constraints.
Why a hook skill in Claude Code

Claude Code already organizes capabilities as skills: human‑readable, model‑interpretable documents that define when to activate, how to execute, and what to output.

By packaging HEAVYSKILL as a hook skill:

    You get a reusable inner reasoning primitive that is independent of the rest of the harness.

    Any high‑level workflow (debugging, algorithm design, architecture decisions) can opt into heavy thinking without new orchestration code.

    It matches the paper’s portability claim: the same HEAVYSKILL skill file can run under Claude Code and other harnesses that support skills + subagents.

3. High‑Level Design
Role of the HeavySkill hook

HeavySkill is not a ticket type or standalone agent; it is a reasoning hook the orchestrator can wrap around an existing task when conditions justify extra test‑time compute.

At a high level:

    Orchestrator receives a task and evaluates an activation predicate.

    If predicate is satisfied, it calls the HeavySkill hook instead of running a single agent pass.

    HeavySkill runs the two-stage pipeline:

        Stage 1: spawn KK parallel thinker agents.

        Stage 2: run a deliberation agent over the serialized thinker outputs.

    HeavySkill returns a single final answer back into the original workflow, which continues as normal (e.g., verification, tool use, downstream tickets).

HeavySkill is fully expressed as a structured natural‑language skill file, with no Claude Code‑specific code required beyond using the existing agent / subagent APIs.
4. Activation Conditions

The paper’s skill file defines when HeavySkill should activate vs remain dormant, to avoid wasting compute on trivial tasks.
Recommended activation predicate

Activate HeavySkill when:

    Task type:

        Mathematical/STEM reasoning with verifiable numeric/logical answers.

        Algorithmic/code‑competition style problems.

        Complex logical deduction or multi‑constraint design where correctness is objectively checkable (e.g., all constraints satisfied).

    Difficulty:

        The primary agent is uncertain about its initial approach, or prior attempts produced inconsistent answers.

        The orchestrator classifies the problem as “complex reasoning” (e.g., non‑trivial CoT needed).

    Verifiability:

        The task has clear correctness criteria (explicit problem statement, tests, or a rubric).

Do not activate HeavySkill for:

    Simple factual questions or pure information retrieval.

    Casual conversation, style coaching, or purely preference‑based tasks.

    Straightforward code edits with obvious local fixes.

In Claude Code terms: HeavySkill should be available as a skill that Claude can choose to load when the task falls into the “complex, verifiable reasoning” bucket.
5. Stage 1 — Parallel Reasoning Protocol
Objective

Generate multiple independent reasoning trajectories for a single problem, each fully worked out from scratch, to create a diverse candidate set for deliberation.
Agent spawning

The HEAVYSKILL skill instructs the orchestrator to:

    Extract the core reasoning problem from the user request.

    Spawn KK parallel agents (e.g., K=3–5 for interactive harness, K=8 in batch workflows).

    Each agent receives:

        The same problem statement.

        A directive to solve it independently, showing a complete reasoning chain and final answer.

        Optional guidance to vary strategy (e.g., algebraic vs geometric, brute-force vs clever).

Example agent prompt (adapted from the paper’s skill):

    Solve the following problem step by step.
    Show your complete reasoning and arrive at a final answer.

    Problem: <problem text>

    Think carefully and solve this independently. Show all work.

Key constraints:

    Agents must not share context or see each others’ work. Independence is critical.

    They must produce both thought process and answer, not just the answer.

K selection and diversity

The paper recommends:

    K = 8 or 16 for offline evaluation and RLVR experiments (benchmarks).

    K = 3–5 in interactive harnesses (Claude Code) for a balance of cost and benefit.

They also show that:

    Performance improves monotonically as K increases (test‑time scaling).

    Naive “Max‑length” selection (favoring longer chains) hurts performance.

    Selecting trajectories that agree on the most frequent answer (“Max‑Answer‑Num”) gives a better candidate set for deliberation, but the main point is that HeavySkill’s sequential deliberation still outperforms simple voting.

6. Memory Cache Serialization

After parallel reasoning, trajectories are serialized into a memory cache string that fits within the deliberation model’s context window.
Structure

The HEAVYSKILL cache is formatted as:

text
Here is a problem, and multiple thinkers try to give their thought processes independently.
====== Problem ======
<problem text>
==== Thinkers Process Start ====
# ----- Thinker #1 -----
<thinker 1 reasoning and answer>
# ----- Thinker #2 -----
<thinker 2 reasoning and answer>
...
==== Thinkers Process End ====

Implementation details:

    Pruning: If full trajectories would overflow context, prune each thinker to the essential parts (final reasoning chunk + answer).

    Shuffling: Shuffle thinker order to avoid position bias.

    This serialized cache becomes the input to the deliberation model.

7. Stage 2 — Sequential Deliberation Protocol
Objective

Have a deliberation model read the problem plus all thinker outputs, then:

    Compare and critique reasoning quality.

    Identify which trajectory (if any) is correct.

    If all are wrong, re‑reason from scratch.

    Produce a final answer with higher accuracy than any single trajectory or simple majority vote.

Deliberation prompt structure

The HEAVYSKILL skill defines a deliberation prompt that:

    Tells the model it is seeing multiple independent thought processes.

    Instructs it to:

        Summarize and compare the thinkers’ reasoning.

        Identify logical errors or gaps.

        Decide which reasoning path(s) are most sound.

        If all thinkers are wrong, “learn from their mistakes” and solve the problem anew.

        Produce a final answer in the correct domain format (e.g., ⋅⋅​ for math, code block for code).

    Explicitly warns not to just solve the problem like another thinker—its job is meta‑analysis and synthesis.

The skill also enforces output constraints: the final user‑visible answer must not include the meta‑analysis; only the final answer is returned, formatted as the domain expects.
Iterative deliberation (optional)

An optional extension is iterative deliberation:

    After one deliberation pass, append the deliberation’s own reasoning as another “expert thinker” into the memory cache.

    Re‑run deliberation for up to N iterations (the paper tests N up to 4).

    This can further improve Heavy‑Mean@K, but the authors note trade‑offs (stability vs gains) and context length constraints.

8. Empirical Behavior and When to Use It

The paper’s experiments show:

    Heavy thinking (parallel + deliberation) consistently beats:

        Mean performance of individual trajectories (M@K).

        Majority voting (V@K).

    Heavy‑Mean@4 often approaches the theoretical Pass@K upper bound on STEM benchmarks.

    Heavy‑Pass@4 (potential if you can pick any of the deliberation outputs) can even exceed the raw P@K from parallel reasoning alone, meaning deliberation can “re‑reason” a correct answer not present in any single trajectory.

However:

    Gains are largest on objective, verifiable tasks (STEM, code, IFEval).

    Gains are smaller or sometimes neutral on preference‑oriented tasks (chitchat / Arena‑style dialogue).

For Claude Code, this supports the activation policy: use HeavySkill for hard, verifiable problems, not for general chat.
9. Concrete Claude Code Skill File Shape

At the Claude Code level, HEAVYSKILL is delivered as a skill document roughly matching the structure in Figures 8–10 of the paper:

    Title / Overview: explain heavy thinking and the two‑stage pipeline.

    When to activate: conditions for complex, verifiable tasks, and explicit “do not activate” cases.

    Stage 1 – Parallel Reasoning Protocol:

        How to spawn K agents.

        Agent prompts and independence constraints.

    Stage 2 – Sequential Deliberation Protocol:

        How to feed the memory cache.

        Deliberation prompt with meta‑analysis instructions.

    Execution in Claude Code harness:

        Use the Agent tool to spawn parallel thinkers; orchestrator waits for all, then runs the deliberation step itself (do not delegate Stage 2).

    Output format constraints:

        Only final answer, properly formatted for the domain; no meta‑analysis shown to end user.

Because this is plain text with no harness‑specific code, it can be injected into any Claude Code project that supports skills and subagent spawning, matching the paper’s portability claim.
10. Optional: Integration Notes for NOVA / Forgemaster

If you’re integrating HeavySkill into a NOVA/Forgemaster‑style system (your own framework):

    Treat HeavySkill as a hook skill that wraps existing ticket execution, similar to how Forgemaster core skills like forgemaster-parallel-lanes describe routing behavior.

    After deliberation, write a NOVA shard with:

        Content: final answer.

        Source: heavyskill.

        Metadata: number of thinkers, number of deliberation passes, confidence.

        Epistemic state based on deliberation confidence (e.g., confirmed / neutral).

This pairs the paper’s test‑time reasoning improvement with NOVA’s long‑term memory and shard‑based confidence decay.