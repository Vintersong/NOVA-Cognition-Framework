/*
 * nova_hotpool.c — L1/L2-style hot pool cache for NOVA shard retrieval.
 *
 * An 8-slot fixed-size cache positioned in front of SQLite. Each slot stores
 * the FNV-1a 32-bit hash of a shard_id, the shard confidence, and its
 * memory-kind index. Eviction policy: lowest confidence first.
 *
 * Build:
 *   gcc -O2 -shared -fPIC -o _nova_hotpool.so nova_hotpool.c
 *
 * The library is named _nova_hotpool.so (underscore prefix) to avoid
 * shadowing nova_hotpool.py in Python's import path.
 *
 * Required headers: <stdint.h> <string.h> <float.h>
 */

#include <stdint.h>
#include <string.h>
#include <float.h>

#define HOTPOOL_SIZE 8

/*
 * HotSlot: 12 bytes per slot.
 * 8 slots = 96 bytes — fits inside 2 × 64-byte cache lines.
 *
 * Layout:
 *   uint32_t id        4 bytes  — FNV-1a 32-bit hash of shard_id string
 *   float    confidence 4 bytes  — meta_tags.confidence, range [0.0, 1.0]
 *   uint8_t  kind       1 byte   — index 0–6 matching Python KIND_MAP
 *   uint8_t  _pad[3]   3 bytes  — explicit padding; total = 12
 */
typedef struct {
    uint32_t id;
    float    confidence;
    uint8_t  kind;
    uint8_t  _pad[3];
} HotSlot;

typedef struct {
    uint64_t hits;
    uint64_t misses;
    uint64_t evictions;
} HotPoolStats;

/* File-scope static globals — zero-initialised by the C runtime. */
static HotSlot      _pool[HOTPOOL_SIZE];
static uint8_t      _valid[HOTPOOL_SIZE];
static HotPoolStats _stats;

/*
 * hotpool_evict_lowest — internal, not exported.
 *
 * Scans valid slots for the one with the lowest confidence score, marks it
 * invalid, increments the eviction counter, and returns its index so the
 * caller (hotpool_insert) can reuse the slot without a second scan.
 *
 * Returns -1 if no valid slots exist (should not occur when called from
 * hotpool_insert after a full-pool check, but guarded for correctness).
 */
static int hotpool_evict_lowest(void)
{
    int   lowest_idx  = -1;
    float lowest_conf = FLT_MAX;
    int   i;

    for (i = 0; i < HOTPOOL_SIZE; i++) {
        if (_valid[i] && _pool[i].confidence < lowest_conf) {
            lowest_conf = _pool[i].confidence;
            lowest_idx  = i;
        }
    }

    if (lowest_idx >= 0) {
        _valid[lowest_idx] = 0;
        _stats.evictions++;
    }

    return lowest_idx;
}

/*
 * hotpool_lookup — exported.
 *
 * Linear scan over all 8 slots. Returns the stored confidence on a hit, or
 * -1.0f on a miss. The -1.0f sentinel is unambiguous because valid confidence
 * values are clamped to [0.0, 1.0] by NOVA's maintenance layer.
 */
float hotpool_lookup(uint32_t hash)
{
    int i;
    for (i = 0; i < HOTPOOL_SIZE; i++) {
        if (_valid[i] && _pool[i].id == hash) {
            _stats.hits++;
            return _pool[i].confidence;
        }
    }
    _stats.misses++;
    return -1.0f;
}

/*
 * hotpool_insert — exported.
 *
 * Inserts a shard into the first empty slot. If all 8 slots are occupied,
 * calls hotpool_evict_lowest() to free the weakest slot before inserting.
 */
void hotpool_insert(uint32_t hash, float confidence, uint8_t kind)
{
    int i;

    for (i = 0; i < HOTPOOL_SIZE; i++) {
        if (!_valid[i]) {
            _pool[i].id         = hash;
            _pool[i].confidence = confidence;
            _pool[i].kind       = kind;
            _valid[i]           = 1;
            return;
        }
    }

    /* All slots full — evict lowest confidence, reuse freed slot. */
    i = hotpool_evict_lowest();
    if (i >= 0) {
        _pool[i].id         = hash;
        _pool[i].confidence = confidence;
        _pool[i].kind       = kind;
        _valid[i]           = 1;
    }
}

/*
 * hotpool_stats — exported.
 *
 * Returns a by-value copy of the internal stats struct. ctypes handles
 * struct-by-value return correctly on x86_64 Linux via the hidden-pointer ABI.
 */
HotPoolStats hotpool_stats(void)
{
    return _stats;
}

/*
 * hotpool_reset — exported.
 *
 * Zeroes all pool slots, validity flags, and counters. Equivalent to the
 * zero-initialised state at program load. Used by tests and bench setup.
 */
void hotpool_reset(void)
{
    memset(_pool,   0, sizeof(_pool));
    memset(_valid,  0, sizeof(_valid));
    memset(&_stats, 0, sizeof(_stats));
}
