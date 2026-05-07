# Research Program — NOVA Autoresearch Loop

Inspired by Karpathy's autoresearch pattern. This document IS the workflow.
The agent reads this file, proposes the next query, runs it, scores the result,
then updates this document before repeating.

**NEVER STOP. Run until interrupted with Ctrl+C.**

---

## Goal

Build a high-quality NOVA shard knowledge base on LLM agent architecture,
orchestration patterns, and memory systems — topics directly useful for
improving the NOVA-Cognition-Framework.

---

## Metric

Each research output is scored 1–10 by the model itself on this rubric:
- **9–10**: Specific, actionable, cites mechanisms or numbers, no filler
- **7–8**: Solid content, mostly specific, minor vagueness
- **5–6**: Correct but generic — textbook-level, nothing new
- **3–4**: Vague, circular, or padding-heavy
- **1–2**: Wrong, incoherent, or refused to answer

**Keep threshold: 7. Score < 7 → discard.**

---

## Constraints

- One focused question per iteration — no kitchen-sink queries
- Prefer specific mechanism questions over "explain X" questions
- If a direction yields 2 consecutive discards, mark it as exhausted and move on
- Do not repeat questions already in `results.tsv`
- Keep answers under 600 words — the model should be dense, not verbose

---

## Research Directions

### Active
- LLM agent memory architectures (episodic, semantic, procedural — how each is implemented)
- Confidence scoring without logit access (sampling tricks, self-consistency)
- Compaction strategies for long conversation history
- Knowledge graph structures for agent memory (what schema works at scale)
- Tool use patterns — when to parallelize vs serialize tool calls
- Retrieval augmentation vs compiled knowledge (RAG vs wiki pattern)

### Completed
*(populated automatically by the loop)*

### Exhausted (2+ discards)
*(populated automatically by the loop)*

---

## Successful Findings
*(populated automatically — summaries of kept shards)*
- [2026-04-07 17:27] (score 9) **How do retrieval augmentation techniques (e.g., dynamic reranking with cross-attention or hybrid dense-sparse embeddings) integrate with episodic buffers in LLM agents to prioritize recent tool interactions over stale or irrelevant external knowledge during iterative decision-making under time-sensitive constraints?**
  → Retrieval-Augmented Generation (RAG) and its extensions—such as **dynamic reranking, cross-attention mechanisms, and hyb
- [2026-04-07 17:27] (score 9) **How do compaction strategies (e.g., pruning, compression algorithms like delta encoding or vector quantization) optimize memory retention in LLM agents while preserving critical episodic and semantic knowledge during iterative tool invocation under high context-window constraints?**
  → Compaction strategies are crucial for optimizing memory retention in **Large Language Model (LLM) agents**—especially wh
- [2026-04-07 17:27] (score 9) **How do confidence thresholds (e.g., temperature-based vs. entropy-based) in LLM agents interact with sampling strategies (e.g., nucleus sampling vs. top-*k*) to dynamically adjust decision confidence during iterative tool invocation under noisy or ambiguous external feedback?**
  → Confidence thresholds and sampling strategies in **Large Language Model (LLM) agents** play a critical role in balancing
- [2026-04-07 17:26] (score 9) **How do procedural memory mechanisms (e.g., rule-based vs. heuristic-driven) interact with episodic buffers in LLM agents to prioritize task-relevant tool invocations during iterative decision-making under partial observation?**
  → The interaction between **procedural memory mechanisms** (rule-based vs. heuristic-driven) and **episodic buffers** in L
- [2026-04-07 17:26] (score 9) **How do tool-use parallelization strategies (e.g., race-condition mitigation vs. sequential batching) affect the coherence of multi-agent workflows in LLM orchestration when dealing with conflicting tool outputs during iterative decision-making?**
  → The use of **tool-use parallelization strategies**—such as **race-condition mitigation**, **sequential batching**, or **
- [2026-04-07 17:26] (score 9) **How do hybrid memory systems (combining episodic buffers with semantic graph embeddings) resolve conflicts in LLM agents during multi-step tool chaining when external knowledge is sparse?**
  → Hybrid memory systems that combine **episodic buffers** (short-term, event-based memories) with **semantic graph embeddi
- [2026-04-07 17:25] (score 9) **How do knowledge graph structures (e.g., hierarchical vs. flat schemas) affect the scalability of episodic memory in LLM agents during sequential tool invocation with limited context windows?**
  → The structure of **knowledge graphs (KGs)**—particularly their **hierarchical vs. flat schema designs**—significantly in
- [2026-04-07 17:25] (score 8) **How do self-consistency sampling techniques compare to bootstrapped confidence thresholds in LLM agents for mitigating hallucinations in multi-step reasoning tasks with limited tool access?**
  → Self-consistency sampling (SC) and bootstrapped confidence thresholds are both techniques designed to reduce hallucinati
- [2026-04-07 17:25] (score 8) **How do retrieval augmentation techniques (e.g., dense retrievers vs. sparse vectors) interact with procedural memory in LLM agents to influence decision-making during iterative tool use?**
  → Retrieval-Augmented Generation (RAG)—and more broadly, **retrieval-augmentation techniques** (such as dense retrievers v

---

## Failed Attempts
*(populated automatically — what didn't score well and why)*
- [2026-04-07 17:24] (score 0) **How do LLM agents implement and balance episodic memory retention with semantic memory consolidation during long-term task persistence across multiple tool interactions?**

---

## Notes for the Loop

When proposing the next query:
1. Read the Active directions above
2. Pick the direction with the fewest entries in results.tsv
3. Formulate a specific, mechanistic question — not "explain X" but "how does X handle Y when Z"
4. Avoid questions already answered (check Successful Findings)
