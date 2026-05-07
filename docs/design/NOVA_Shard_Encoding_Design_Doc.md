# NOVA Shard State Encoding: Design Document

## Overview

This document formalizes the multi-dimensional epistemic-emotional shard encoding architecture for the NOVA Cognition Framework. The design encodes confidence, valence, arousal, and epistemic state as a single structured integer per shard, enabling native SQLite range queries across the full state space without multi-column joins. The approach is directly inspired by Balatro's card identity encoding pattern and validated against IEEE 754 precision constraints.

---

## 1. The Balatro Encoding Insight

Balatro encodes card identity as a structured float where different decimal positions carry distinct semantic meanings — rank, suit, and modifiers — all extractable through simple arithmetic rather than separate data fields. A card's full identity is a single number; its dimensions are recovered via `floor`, `modulo`, and division operations rather than struct field lookups.

This pattern is directly applicable to NOVA's shard state problem: multiple epistemic and emotional dimensions need to be stored, queried, and compared per shard. The naive approach uses separate columns. The Balatro-inspired approach packs them into a single value, enabling single-predicate range queries across the full state space.

**Key insight**: meaning encoded in position, extracted by rule, with no redundant schema overhead.

---

## 2. Why Not a Float

The first instinct is to mirror Balatro exactly and use a structured float:

```
shard_state = 0.873421  →  confidence=0.87, valence=3, arousal=4, epistemic=2
```

This fails for a fundamental reason: IEEE 754 double precision stores values as binary fractions, not decimal. `0.873421` as a float may be stored as `0.873420999999998` or `0.873421000000002`. Arithmetic extraction via `floor((x * 10000) % 100)` will return wrong dimension values for borderline cases.[cite:64]

Balatro itself demonstrates the failure mode: at Ante 39, required chips overflow to `NaN`, and `NaN` comparisons always return unordered, making game progression impossible. The encoding concept works; the float substrate does not.

**The fix**: use a structured 64-bit integer. Integer arithmetic is exact. SQLite integers never produce NaN. Range queries work identically.

---

## 3. Structured Integer Encoding Specification

### Format

```
shard_state  =  CCCC_V_A_E
             =  confidence(0–9999) | valence(0–9) | arousal(0–9) | epistemic(0–2)
```

Example: `8734752` decodes as:

| Dimension | Encoded | Decoded |
|---|---|---|
| Confidence | `8734` | `8734 / 10000.0 = 0.8734` |
| Valence | `7` | 7 (range 0–9) |
| Arousal | `5` | 5 (range 0–9) |
| Epistemic | `2` | 2 → confirmed (`{0=contradicted, 1=neutral, 2=confirmed}`) |

### Extraction Functions

```python
def decode_state(state: int) -> dict:
    epistemic = state % 10
    arousal   = (state // 10) % 10
    valence   = (state // 100) % 10
    confidence = (state // 1000) / 10000.0
    return {
        "confidence": confidence,
        "valence": valence,
        "arousal": arousal,
        "epistemic": epistemic  # 0=contradicted, 1=neutral, 2=confirmed
    }

def encode_state(confidence: float, valence: int, arousal: int, epistemic: int) -> int:
    c = round(confidence * 10000)  # 0–9999
    return c * 1000 + valence * 100 + arousal * 10 + epistemic
```

### Encoding Rules

- Confidence: `float [0.0, 1.0]` → integer `[0, 9999]` via `round(x * 10000)`
- Valence: `int [0, 9]` — affective tone (0=most negative, 9=most positive)
- Arousal: `int [0, 9]` — activation level (0=dormant, 9=high activation)
- Epistemic: `int {0, 1, 2}` — maps to NOVA's ternary `{-1, 0, 1}` → `{0, 1, 2}`

---

## 4. Ternary Epistemic State Integration

NOVA's existing ternary system (`confirmed / neutral / contradicted`) is preserved as a derived classification layer on top of the encoding, not replaced by it.[cite:16][cite:17]

The two systems are orthogonal:

| Layer | What it encodes | How it's set |
|---|---|---|
| Ternary | Epistemic *classification* — what the shard believes | Derived from confidence thresholds at read time |
| Structured integer | Epistemic *state vector* — the full affective and epistemic context | Written at shard creation/update |

The `epistemic` dimension in the integer directly stores the ternary value (0/1/2 = contradicted/neutral/confirmed), so no separate column is needed. The threshold derivation logic already in the codebase continues to work unchanged.[cite:25]

---

## 5. SQLite Query Semantics

The encoding is designed so that natural sort order matches epistemic priority: confidence is the most significant dimension, epistemic state the least.

### Example Queries

**High-confidence confirmed shards:**
```sql
SELECT * FROM shards
WHERE state BETWEEN 8000002 AND 9999992
  AND state % 10 = 2;
```

**Positive valence, any confidence:**
```sql
SELECT * FROM shards
WHERE (state / 100) % 10 >= 7;
```

**Range across full state space:**
```sql
SELECT * FROM shards
WHERE state BETWEEN 7000000 AND 8999999
ORDER BY state DESC;
```

This replaces multi-column joins with single-predicate arithmetic — the core query advantage of the Balatro-inspired encoding.

---

## 6. Provenance Tagging Extension

Cultural and linguistic provenance is stored as a **separate string field**, not packed into the integer. Cultural cluster is nominal, not ordinal — there is no meaningful arithmetic between `WEIRD` and `Collectivist`, so integer encoding would imply a false ranking.[cite:65][cite:72]

### Schema

```sql
CREATE TABLE shards (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    state       INTEGER NOT NULL,          -- structured integer encoding
    provenance  TEXT,                      -- format: lang|cluster|source
    embedding   BLOB,
    created_at  REAL,
    updated_at  REAL
);
```

### Provenance Format

```
provenance = "en|WEIRD|LLM"
           = language_code | cultural_cluster | source_type
```

| Field | Values | Notes |
|---|---|---|
| `language_code` | ISO 639-1 (`en`, `ro`, `ar`) | Query language at shard creation |
| `cultural_cluster` | `WEIRD`, `Collectivist`, `Traditional`, `Mixed`, `Personal` | Coarse heuristic; auditable presence matters more than precision |
| `source_type` | `LLM`, `Human`, `Academic`, `Web` | Whether knowledge originated from a model or external source |

**Romanian shards** from personal reasoning receive `lang:ro|Personal` provenance, distinguishing them from LLM-retrieved WEIRD-filtered knowledge.[cite:87]

### Why Provenance Matters

Harvard and Cornell research administered the World Values Survey to five GPT models and mapped responses against 107 countries.[cite:64] ChatGPT clustered statistically with Germany, Britain, Australia, and New Zealand — the WEIRD cluster. Post-training RLHF shifted the emotional baseline further, toward brooding and reflective affect. This constitutes two invisible layers of cultural shaping on every retrieved shard.[cite:60]

Provenance tagging makes this bias **auditable rather than hidden**. The shard knows it was shaped by an English-language, LLM-mediated reasoning path. That is the epistemic transparency NOVA is designed to provide.

---

## 7. Shard Content Format

Embedding retrieval precision degrades when a single shard encodes multiple claims. One embedding vector cannot carry two distinct claims with equal retrieval precision.[cite:108] The optimal structure follows the header-anchored pattern:

```
[CLAIM: Cultural bias is structural, not intentional.]
[PROVENANCE: en|WEIRD|LLM]
[STATE: 8734752]

Training data distribution determines ideological skew in LLM outputs.
ChatGPT maps statistically to Germany, Britain, Australia, New Zealand
on the World Values Survey. The bias is invisible without explicit tagging.
```

### Content Rules

- **Header**: one declarative claim, subject-verb-object, 40–120 characters
- **Body**: 1–3 sentences, maximum ~1000 characters total, one topic
- **Sentence structure**: English SVO, short declarative sentences — minimal inflection, meaning carried by position, maximally legible to transformer attention[cite:87]
- **One claim per shard**: related shards connect via typed relationship edges in the knowledge graph

---

## 8. Failure Modes and Mitigations

| Failure Mode | Description | Mitigation |
|---|---|---|
| Extraction drift (float) | `floor((x * 10000) % 100)` returns N±1 for borderline values | Use integer encoding — exact arithmetic, no rounding |
| Dimensional collision | Two distinct states round to same integer | Confidence at 4-digit precision (10,000 slots) makes collision negligible |
| Update corruption | Modifying one dimension requires full recompose | Always decode → modify → re-encode atomically; validate on write |
| Sort order inversion | Schema change promotes a non-confidence dimension to primary | Confidence is hardcoded as most significant; schema changes require migration |
| NaN propagation (float) | Arithmetic on corrupted shard poisons range queries | Not applicable to integer encoding; integers cannot produce NaN |
| Silent provenance loss | Shard created without provenance field | Default to `"en|WEIRD|LLM"` if language is English and source is LLM; never null |

---

## 9. Migration Path: SQLite → Postgres

NOVA currently uses SQLite as its backend.[cite:16] The structured integer encoding and provenance schema are designed to be identical in Postgres. The migration path adds pgvector for semantic similarity search alongside the existing state-based range queries.[cite:130]

```sql
-- Postgres extension of the schema
ALTER TABLE shards ADD COLUMN embedding vector(1536);

-- Hybrid query: epistemic state filter + semantic similarity
SELECT * FROM shards
WHERE state BETWEEN 8000002 AND 9999992
ORDER BY embedding <-> $query_vector
LIMIT 20;
```

This enables epistemic state filtering and semantic similarity in a single round trip — no separate retrieval pipeline, no joins.[cite:128] The pgEdge Vectorizer extension handles automatic vector regeneration when shard content changes, addressing a common oversight in RAG pipelines.[cite:130]

---

## 10. Design Principles Summary

- **Meaning in position, extracted by rule** — the Balatro principle applied to epistemic state
- **Exact substrate** — integers not floats; no binary rounding, no NaN
- **Ternary preserved** — epistemic classification derived at read time, not replaced
- **Nominal data stays nominal** — provenance as string, not integer; no false ordinality
- **Auditable bias** — cultural provenance tagged at creation, queryable, never hidden
- **One claim per shard** — retrieval precision requires atomic semantic units

