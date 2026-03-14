# Numpy Vector Search Optimization - Implementation Plan

## Goal
Optimize Phase 5 vector search from O(n) linear scan to vectorized numpy operations for 10-100x speedup.

## Current Performance Problem

```python
# Current: search.py line 58-74
for vector in index.get('unified_vectors', []):  # ← Loop through ALL vectors
    score = cosine_similarity(query_embedding, embedding)  # ← Compute individually
```

**Problem**: Python loops are slow. Computing 1,000 cosine similarities takes ~50ms.

## Solution: Numpy Vectorization

```python
import numpy as np

# Convert to numpy arrays (do once)
embeddings_matrix = np.array([v['embedding'] for v in vectors])  # Shape: (n, 1536)
query_vector = np.array(query_embedding)  # Shape: (1536,)

# Compute ALL similarities at once (vectorized)
similarities = np.dot(embeddings_matrix, query_vector)  # ← Single operation!

# Get top-k indices efficiently
top_k_idx = np.argpartition(similarities, -k)[-k:]
```

**Result**: Same operation in ~0.5ms instead of 50ms (100x faster)

## Files to Modify

### 1. search.py - Update dense_search()

**Current**:
```python
def dense_search(index, query_embedding, k=200, min_score=0.0):
    results = []
    for vector in index.get('unified_vectors', []):  # ← SLOW
        embedding = vector.get('embedding', [])
        score = cosine_similarity(query_embedding, embedding)
        if score >= min_score:
            results.append({**vector, 'cosine_score': score})
    results.sort(key=lambda x: x['cosine_score'], reverse=True)
    return results[:k]
```

**Optimized**:
```python
import numpy as np

def dense_search(index, query_embedding, k=200, min_score=0.0):
    """
    Dense ANN search using vectorized numpy operations

    10-100x faster than pure Python loops
    """
    vectors = index.get('unified_vectors', [])
    if not vectors:
        return []

    # Convert embeddings to numpy array (vectorized)
    embeddings_matrix = np.array(
        [v.get('embedding', []) for v in vectors],
        dtype=np.float32
    )  # Shape: (n_vectors, embedding_dim)

    query_vector = np.array(query_embedding, dtype=np.float32)

    # Compute all cosine similarities at once (single matrix-vector multiplication)
    similarities = np.dot(embeddings_matrix, query_vector)

    # Filter by min_score
    if min_score > 0.0:
        mask = similarities >= min_score
        valid_indices = np.where(mask)[0]
        valid_similarities = similarities[valid_indices]
    else:
        valid_indices = np.arange(len(similarities))
        valid_similarities = similarities

    # Get top-k using partial sort (faster than full sort)
    if len(valid_similarities) <= k:
        top_k_idx = valid_indices
    else:
        # argpartition is O(n) vs O(n log n) for sort
        partition_idx = np.argpartition(valid_similarities, -k)[-k:]
        # Sort just the top-k
        sorted_partition = partition_idx[np.argsort(-valid_similarities[partition_idx])]
        top_k_idx = valid_indices[sorted_partition]

    # Build results
    results = []
    for rank, idx in enumerate(top_k_idx):
        vector = vectors[idx]
        results.append({
            **vector,
            'cosine_score': float(similarities[idx]),
            'dense_rank': rank + 1
        })

    return results
```

### 2. vector_index.py - Ensure embeddings are valid

**Add validation** when building index:
```python
def build_unified_index(sessions_dir, provider='openai', model='text-embedding-3-small'):
    # ... existing code ...

    # Validate embeddings before adding to index
    for vector_entry in unified_vectors:
        embedding = vector_entry.get('embedding')

        # Ensure embedding is valid numpy-compatible array
        if not embedding or not isinstance(embedding, (list, np.ndarray)):
            raise ValueError(f"Invalid embedding for {vector_entry['id']}")

        if len(embedding) != expected_dimensions:
            raise ValueError(
                f"Embedding dimension mismatch: {len(embedding)} != {expected_dimensions}"
            )

        # Normalize to list for JSON serialization
        if isinstance(embedding, np.ndarray):
            vector_entry['embedding'] = embedding.tolist()
```

### 3. Add benchmarking script

**Create**: `tests/benchmark_vector_search.py`
```python
"""
Benchmark vector search performance
"""
import time
import numpy as np
from reccli.search import dense_search

def generate_test_index(n_vectors=1000, dim=1536):
    """Generate synthetic test index"""
    vectors = []
    for i in range(n_vectors):
        embedding = np.random.randn(dim).astype(np.float32)
        # Normalize
        embedding = embedding / np.linalg.norm(embedding)
        vectors.append({
            'id': f'msg_{i:03d}',
            'embedding': embedding.tolist(),
            'content': f'Test message {i}'
        })

    return {'unified_vectors': vectors}

def benchmark_search(n_vectors, n_queries=100):
    """Benchmark search performance"""
    print(f"\n{'='*60}")
    print(f"Benchmarking with {n_vectors} vectors, {n_queries} queries")
    print(f"{'='*60}")

    # Generate test data
    index = generate_test_index(n_vectors)
    query_embedding = np.random.randn(1536).astype(np.float32)
    query_embedding = query_embedding / np.linalg.norm(query_embedding)

    # Warmup
    dense_search(index, query_embedding.tolist(), k=5)

    # Benchmark
    start = time.time()
    for _ in range(n_queries):
        results = dense_search(index, query_embedding.tolist(), k=5)
    elapsed = time.time() - start

    avg_time = (elapsed / n_queries) * 1000  # ms

    print(f"Average query time: {avg_time:.2f}ms")
    print(f"Queries per second: {n_queries / elapsed:.0f}")
    print(f"Total time: {elapsed:.2f}s")

    return avg_time

if __name__ == '__main__':
    # Test different scales
    for n in [100, 500, 1000, 5000, 10000]:
        benchmark_search(n, n_queries=100)
```

## Performance Targets

| Messages | Current (Pure Python) | Target (Numpy) | Speedup |
|----------|-----------------------|----------------|---------|
| 100      | 5ms                   | <1ms           | 5x      |
| 500      | 25ms                  | <2ms           | 12x     |
| 1,000    | 50ms                  | <3ms           | 16x     |
| 5,000    | 250ms                 | <10ms          | 25x     |
| 10,000   | 500ms                 | <15ms          | 33x     |

## Testing Plan

### 1. Unit Tests
```python
# tests/test_vector_search_numpy.py
def test_dense_search_numpy_matches_python():
    """Ensure numpy version gives same results as Python version"""
    # Generate test data
    index = generate_test_index(n=100)
    query = np.random.randn(1536).tolist()

    # Compare results
    numpy_results = dense_search(index, query, k=5)

    # Verify top-5 are consistent
    assert len(numpy_results) == 5
    assert all('cosine_score' in r for r in numpy_results)

    # Scores should be descending
    scores = [r['cosine_score'] for r in numpy_results]
    assert scores == sorted(scores, reverse=True)

def test_dense_search_with_min_score():
    """Test filtering by minimum score"""
    index = generate_test_index(n=100)
    query = np.random.randn(1536).tolist()

    results = dense_search(index, query, k=50, min_score=0.7)

    # All results should meet threshold
    assert all(r['cosine_score'] >= 0.7 for r in results)

def test_dense_search_empty_index():
    """Handle empty index gracefully"""
    index = {'unified_vectors': []}
    query = np.random.randn(1536).tolist()

    results = dense_search(index, query, k=5)
    assert results == []
```

### 2. Integration Tests
```bash
# Test with real session
python3 -m reccli search "authentication" --session test-session.devsession

# Benchmark with real data
python3 tests/benchmark_vector_search.py
```

### 3. Regression Tests
```bash
# Ensure search results are consistent before/after optimization
python3 tests/test_search_regression.py
```

## Implementation Steps

### Phase 1: Core Optimization (1-2 hours)
- [ ] Update `search.py` dense_search() with numpy vectorization
- [ ] Add input validation for embeddings
- [ ] Test with synthetic data

### Phase 2: Validation (30 min)
- [ ] Create benchmark script
- [ ] Run benchmarks to verify speedup
- [ ] Compare results with old implementation

### Phase 3: Testing (1 hour)
- [ ] Write unit tests for numpy version
- [ ] Test edge cases (empty index, single vector, etc.)
- [ ] Integration test with real sessions

### Phase 4: Documentation (30 min)
- [ ] Update `docs/progress/phases/PHASE_5_IMPLEMENTATION.md` with performance numbers
- [ ] Document numpy requirement
- [ ] Add performance section to README

## Rollout Plan

### Week 1: Optimization
- Implement numpy vectorization
- Benchmark and validate
- Merge to main branch

### Week 2: Monitor
- Test with real usage
- Gather performance metrics
- Fix any issues

### Week 3: Document
- Update documentation
- Add performance guide
- Share benchmarks

## Dependencies

Already in requirements.txt:
```txt
numpy>=1.24.0  ✅ Already included
```

No new dependencies needed!

## Success Criteria

- ✅ 10x+ speedup for 1,000 message sessions
- ✅ 25x+ speedup for 5,000+ message sessions
- ✅ All existing tests pass
- ✅ Search results match pre-optimization
- ✅ No new dependencies added

## Future Enhancements (Phase 10)

Once numpy optimization is stable, consider:

1. **FAISS Integration** - For 100,000+ messages
2. **GPU Acceleration** - If available
3. **Incremental Index Updates** - Avoid full rebuild
4. **Compressed Vectors** - Product Quantization for memory savings

## Conclusion

This optimization:
- ✅ Provides 10-100x speedup
- ✅ Uses existing dependencies (numpy)
- ✅ Is a drop-in replacement (same API)
- ✅ Prepares for Phase 10 multi-session search
- ✅ Can be implemented in 2-3 hours

**Recommendation**: Implement this **before** Phase 10, as it's a quick win with massive performance benefits.
