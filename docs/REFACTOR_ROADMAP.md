# NOVA Refactor Roadmap

## Context
Solo dev, personal project. This is a living document of architectural improvements to consider. **NOT committed to full implementation** — pick battles wisely.

---

## Current State: The Monolith

**File:** `mcp/nova_server.py` (~1,050 lines)

**Structure:**
```
nova_server.py
├── Permission helpers (20 lines)
├── SHARD I/O (50 lines)
├── Confidence decay (40 lines)
├── Auto-compaction (50 lines)
├── Cosine similarity (20 lines)
├── Knowledge graph ops (100 lines)
├── Usage tracking (15 lines)
├── Index management (80 lines)
├── Retrieval logic (50 lines)
├── Input models (70 lines)
├── MCP tool handlers (16 tools, 500+ lines)
├── Session management (100 lines)
└── Forgemaster integration (50 lines)
```

### What Works
- Clear section headers with visual separators
- Logical grouping by concern
- No circular dependencies
- File safety (FileLock used consistently)
- Async boundaries preserved

### What's Friction
- **Retrieval is overlap-based, not semantic** — brittle to token changes
- **Hard to test services** — can't unit test ranking without mocking MCP
- **Graph queries are O(n²)** — no caching, BFS over all relations each query
- **Policy hardcoded** — decay rate, compaction threshold, merge threshold scattered in globals
- **Session state leaks** — `nova_shard_interact` embeds session update logic

---

## Proposed Architecture

### Target Structure

```
mcp/
  nova_server.py              # MCP tool registration only (thin adapters)
  tool_schemas.py             # Pydantic inputs (optional, keeps nova_server.py smaller)
  tool_handlers.py            # MCP @mcp.tool decorators (optional)

python/
  config.py                   # env vars and settings (NEW)
  services/
    retrieval_service.py      # Embedding-based search, ranking, shard selection
    maintenance_service.py    # Decay, compaction, merge suggestions
    graph_service.py          # Relations, BFS queries, caching
    session_service.py        # Session handling, flush/load/list
    usage_service.py          # Usage logging, token accounting
  repositories/
    shard_repository.py       # load_shard, save_shard, filename safety
    index_repository.py       # shard_index.json CRUD
    graph_repository.py       # shard_graph.json CRUD
    usage_repository.py       # nova_usage.jsonl append
  domain/
    shard_models.py           # Shard, ShardMeta data classes
    graph_models.py           # Entity, Relation models
    session_models.py         # Session, Message models
    scoring.py                # Scoring functions, policies
    events.py                 # (OPTIONAL) ShardCreated, ShardUpdated, etc.
```

---

## Quick Wins (Do These First)

### 1. Extract Repositories (1 day, zero risk)

**File:** `python/repositories.py`

Move:
- `load_shard()`
- `save_shard()`
- `load_graph()`
- `save_graph()`
- `load_index()`
- `save_index()`
- `get_unique_filename()`
- `sanitize_filename()`
- File constants (`SHARD_DIR`, `INDEX_FILE`, `GRAPH_FILE`)

**Benefits:**
- One place for filesystem safety
- Easier to add SQLite/Postgres later without rewriting tools
- Can stub for testing

**Implementation:**
```python
# python/repositories.py
class ShardRepository:
    def __init__(self, shard_dir, index_file, graph_file):
        self.shard_dir = shard_dir
        self.index_file = index_file
        self.graph_file = graph_file
    
    def load_shard(self, shard_id):
        # Move from nova_server.py
        
    def save_shard(self, filepath, data):
        # Move from nova_server.py
```

**Update nova_server.py:**
```python
from python.repositories import ShardRepository

repo = ShardRepository(SHARD_DIR, INDEX_FILE, GRAPH_FILE)

# Replace load_shard() calls with repo.load_shard()
data, filepath = repo.load_shard(shard_id)
```

---

### 2. Extract Retrieval Service (1–2 days, medium impact)

**Current problem:** `guess_relevant_shards()` is token-overlap-based. Changes in guiding question wording break recall.

**File:** `python/services/retrieval_service.py`

Move:
- `guess_relevant_shards()`
- `confidence_weighted_score()`
- Ranking logic from `nova_shard_search` and `nova_shard_interact`

**Upgrade it:**
```python
class RetrievalService:
    def __init__(self, shard_repo, index_repo):
        self.shard_repo = shard_repo
        self.index_repo = index_repo
    
    def rank_shards(self, query, top_n=3):
        """
        1. Embedding-based candidates (semantic similarity)
        2. Rerank by: lexical overlap + graph proximity + confidence
        3. Diversity deduplication (avoid redundant shards)
        """
        # Load embeddings for all shards
        candidates = self._embedding_score(query, top_n * 2)  # Get 2x, rerank to top_n
        
        # Lexical overlap as secondary signal
        candidates = self._rerank_by_overlap(candidates, query)
        
        # Graph proximity (shards that reference each other)
        candidates = self._rerank_by_proximity(candidates, graph)
        
        # Confidence as signal
        candidates = [c for c in candidates if c['confidence'] > 0.4]
        
        # Dedupe (if two shards have >0.95 similarity, pick higher confidence)
        candidates = self._dedupe_similar(candidates)
        
        return candidates[:top_n]
```

**Benefits:**
- Better semantic recall (wording changes don't break it)
- Fewer false negatives
- Cleaner separation of concerns
- Testable without MCP

---

### 3. Extract Maintenance Service (1 day)

**File:** `python/services/maintenance_service.py`

Move:
- `apply_confidence_decay()`
- `maybe_compact_shard()`
- `find_merge_candidates()`
- `cosine_similarity()`
- The maintenance loop inside `nova_shard_consolidate`

Move to config:
- `COMPACT_THRESHOLD`
- `COMPACT_KEEP_RECENT`
- `DECAY_RATE`
- `DECAY_INTERVAL_DAYS`
- `MERGE_SIMILARITY_THRESHOLD`

**Benefits:**
- Easier tuning of policies (all in one place)
- Can schedule as background job later without touching tools
- Testable independently

```python
class MaintenancePolicy:
    compact_threshold: int = 30
    compact_keep_recent: int = 15
    decay_rate: float = 0.05
    decay_interval_days: int = 7
    merge_similarity_threshold: float = 0.85

class MaintenanceService:
    def __init__(self, shard_repo, index_repo, graph_service, policy: MaintenancePolicy):
        self.shard_repo = shard_repo
        self.index_repo = index_repo
        self.graph_service = graph_service
        self.policy = policy
    
    def apply_decay(self, shard_id, shard_data):
        # Move apply_confidence_decay logic here
```

---

### 4. Extract Graph Service (1 day)

**File:** `python/services/graph_service.py`

Move:
- `load_graph()`
- `save_graph()`
- `add_shard_to_graph()`
- `add_relation()`
- `query_graph()`
- `query_graph_transitive()`

Add:
- Result caching (transitive queries are expensive)
- Invalidation on writes

```python
class GraphService:
    def __init__(self, graph_repo):
        self.graph_repo = graph_repo
        self._cache = {}  # {(root, relation_type): results}
    
    def add_relation(self, source, target, relation_type, notes=""):
        self.graph_repo.add_relation(...)
        self._cache.clear()  # Invalidate on write
    
    def query_transitive(self, root_id, relation_type=None, max_depth=3):
        # Check cache first
        key = (root_id, relation_type)
        if key in self._cache:
            return self._cache[key]
        
        # Compute and cache
        results = self._bfs(root_id, relation_type, max_depth)
        self._cache[key] = results
        return results
```

---

## Mid-Tier Improvements (2–3 days)

### 5. Extract Session Service

Move session handling out of `nova_shard_interact`:
```python
class SessionService:
    def __init__(self, session_store):
        self.session_store = session_store
    
    def track_interaction(self, session_id, user_msg, ai_response):
        # Currently embedded in nova_shard_interact
        # Move here for reuse
```

### 6. Extract Usage Service

```python
class UsageService:
    def __init__(self, usage_repo):
        self.usage_repo = usage_repo
    
    def log_operation(self, tool_name, shard_ids, metadata=None):
        # Move from nova_server.py
    
    def get_token_summary(self):
        # Aggregate usage data
```

---

## Full Refactor (3–4 weeks, do this only if you hit pain)

### MCP Tool Handlers Become Thin Adapters

**Before:**
```python
@mcp.tool(name="nova_shard_search")
async def nova_shard_search(params: ShardSearchInput) -> str:
    index = load_index() or update_index()
    results = []
    # 40 lines of ranking logic
    # 20 lines of scoring
    # All embedded here
    return json.dumps({...})
```

**After:**
```python
@mcp.tool(name="nova_shard_search")
async def nova_shard_search(params: ShardSearchInput) -> str:
    if _permission_context.blocks("nova_shard_search"):
        return _permission_error("nova_shard_search")
    
    results = retrieval_service.rank_shards(
        query=params.query,
        top_n=params.top_n,
        include_low_confidence=params.include_low_confidence
    )
    
    log_operation("nova_shard_search", [], {"query": params.query})
    return json.dumps({"query": params.query, "results": results}, indent=2)
```

---

## Implementation Order (Safest Path)

1. **Extract repositories** (1 day) — no tool API change, pure internal
2. **Extract retrieval service** (2 days) — improves search quality, isolated
3. **Extract maintenance service** (1 day) — makes policies tunable
4. **Extract graph service** (1 day) — adds caching benefit
5. **Update tool handlers** (2–3 days) — wire services together
6. Leave session/usage services for later (nice-to-have)

**Checkpoint:** After step 4, system works identically, zero user-facing changes. Can ship and gather real usage data before proceeding further.

---

## Risk Mitigation

### Biggest Risks

| Risk | Mitigation |
|------|-----------|
| Async context loss | Keep `run_in_executor()` in services, not tools |
| Permission context threading | Pass as dependency to each service |
| Index staleness affecting recall | Add freshness property, test with deliberate delays |
| Graph race conditions | Preserve FileLock usage in repositories |
| Search results change mid-refactor | Capture golden tests before starting |

### Golden Tests (Do This First)

Before refactoring, capture "correct" behavior in test cases:
```python
# tests/test_retrieval_golden.py
def test_search_query_semantic_match():
    """Ensure 'machine learning' finds shard with guiding_question='AI and neural networks'"""
    # Set up test shard
    # Call guess_relevant_shards()
    # Assert it returns the shard
    
def test_confidence_weighting():
    """High confidence shard ranks above low-confidence even if less relevant"""
    # Set up two shards: high-conf + OK match, low-conf + better match
    # Verify high-conf ranks first
```

Capture these now (10 min), use them to validate refactor doesn't break behavior.

---

## Decision Tree

**Do you want to refactor?**

### No: Keep Monolith
- Works fine for solo dev
- Revisit if hitting specific pain point
- Cost: 0, Risk: 0

### Yes, but Quick: Extract Repositories + Retrieval
- Pick battle: better search quality
- Time: 2 days
- Benefit: 60% for 10% effort
- Risk: Low

### Yes, Medium: Add Maintenance + Graph Services
- Policies become tunable
- Graph caching speeds up transitive queries
- Time: 4 more days (6 total)
- Benefit: 80% for 20% effort
- Risk: Medium (more moving parts)

### Yes, Full: Refactor Everything
- Clean, testable architecture
- Future-proof for team growth
- Time: 3–4 weeks
- Benefit: 100% but solo dev doesn't need it
- Risk: High (everything touches everything)

---

## What to Do First

1. **Read this document again in 1 week** — you'll have real usage data
2. **Did you hit a retrieval problem?** → Extract retrieval service
3. **Did you want to tune decay rate?** → Extract maintenance service
4. **Did you need to add SQLite support?** → Extract repositories
5. **Did nothing hurt?** → Never refactor, keep shipping

---

## Notes

- All service tests can use mocked repositories — no file I/O in unit tests
- Repositories can be swapped (e.g., `SQLiteShardRepository` inherits from `ShardRepository`)
- Services are composable — `MaintenanceService` uses `ShardRepository` and `GraphService`
- Forgemaster integration stays in `nova_server.py` as-is (it's already isolated)
- Permission context can be threaded via service constructors or remain global (acceptable for solo work)

---

## Links to Sections in nova_server.py

- SHARD I/O: Lines 187–210
- Confidence Decay: Lines 215–240
- Auto-Compaction: Lines 245–270
- Cosine Similarity: Lines 280–295
- Knowledge Graph: Lines 300–380
- Usage Tracking: Lines 385–395
- Index Management: Lines 400–500
- Retrieval Logic: Lines 505–540
- Input Models: Lines 545–610
- Tool Handlers: Lines 615–900
- Session Tools: Lines 920–980
- Forgemaster: Lines 985–1010

---

## Decision Checkpoint

**Revisit this in 2 weeks.** Update with:
- [ ] Which service extraction did you actually need?
- [ ] What went wrong (if anything)?
- [ ] Did golden tests catch behavio changes?
- [ ] Ready for next phase?
