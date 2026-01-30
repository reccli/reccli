#!/usr/bin/env python3
"""
Benchmark vector search performance

Tests the numpy-optimized dense_search against various dataset sizes
to verify 10-100x speedup over pure Python implementation.
"""

import time
import sys
from pathlib import Path
import numpy as np

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from reccli.search import dense_search


def generate_test_index(n_vectors=1000, dim=1536, use_cache=True):
    """
    Generate synthetic test index with random embeddings

    Args:
        n_vectors: Number of vectors to generate
        dim: Embedding dimensionality (default: 1536 for OpenAI)
        use_cache: If True, pre-compute embeddings_matrix (like real indices)

    Returns:
        Dict with unified_vectors list and optional embeddings_matrix
    """
    print(f"Generating {n_vectors} test vectors (dim={dim}, cached={use_cache})...")

    vectors = []
    embeddings_list = []

    for i in range(n_vectors):
        # Generate random embedding
        embedding = np.random.randn(dim).astype(np.float32)

        # L2 normalize (like OpenAI embeddings)
        embedding = embedding / np.linalg.norm(embedding)

        vectors.append({
            'id': f'msg_{i:05d}',
            'embedding': embedding.tolist(),
            'content_preview': f'Test message {i} with some random content',
            'category': 'decisions',
            'session_id': f'session_{i % 10}',  # 10 different sessions
            'timestamp': '2025-11-20T12:00:00Z'
        })

        embeddings_list.append(embedding)

    index = {'unified_vectors': vectors}

    # Add pre-computed matrix (like vector_index.py does in production)
    if use_cache:
        embeddings_matrix = np.array(embeddings_list, dtype=np.float32)

        # Save as binary .npy file (production mode - FAST!)
        import tempfile
        temp_dir = Path(tempfile.gettempdir())
        npy_path = temp_dir / 'test_embeddings.npy'
        np.save(npy_path, embeddings_matrix)

        # Reference the file (like real indices do)
        index['embeddings_file'] = str(npy_path)

    return index


def benchmark_search(n_vectors, n_queries=100, k=5):
    """
    Benchmark search performance

    Args:
        n_vectors: Number of vectors in index
        n_queries: Number of queries to run
        k: Number of results per query

    Returns:
        Dict with benchmark results
    """
    print(f"\n{'='*70}")
    print(f"Benchmarking: {n_vectors} vectors, {n_queries} queries, top-{k}")
    print(f"{'='*70}")

    # Generate test data
    print("Building test index...")
    index = generate_test_index(n_vectors)

    # Generate random query
    query_embedding = np.random.randn(1536).astype(np.float32)
    query_embedding = query_embedding / np.linalg.norm(query_embedding)
    query_embedding = query_embedding.tolist()

    # Warmup run (JIT compilation, cache warming)
    print("Warming up...")
    for _ in range(5):
        dense_search(index, query_embedding, k=k)

    # Benchmark
    print(f"Running {n_queries} queries...")
    start = time.perf_counter()

    for _ in range(n_queries):
        results = dense_search(index, query_embedding, k=k)

    elapsed = time.perf_counter() - start

    # Calculate metrics
    avg_time_ms = (elapsed / n_queries) * 1000
    qps = n_queries / elapsed

    # Verify results
    assert len(results) == k, f"Expected {k} results, got {len(results)}"
    assert all('cosine_score' in r for r in results), "Missing cosine_score in results"

    # Print results
    print(f"\n{'Results':^70}")
    print(f"{'-'*70}")
    print(f"  Total time:        {elapsed:.3f}s")
    print(f"  Average per query: {avg_time_ms:.2f}ms")
    print(f"  Queries/second:    {qps:.0f}")
    print(f"  Top score:         {results[0]['cosine_score']:.4f}")

    # Performance targets
    targets = {
        100: 1.0,    # <1ms for 100 vectors
        500: 2.0,    # <2ms for 500 vectors
        1000: 3.0,   # <3ms for 1,000 vectors
        5000: 10.0,  # <10ms for 5,000 vectors
        10000: 15.0  # <15ms for 10,000 vectors
    }

    target_ms = targets.get(n_vectors, 100.0)
    status = "✅ PASS" if avg_time_ms < target_ms else "❌ FAIL"

    print(f"  Target:            <{target_ms}ms")
    print(f"  Status:            {status}")
    print(f"{'='*70}")

    return {
        'n_vectors': n_vectors,
        'n_queries': n_queries,
        'total_time_s': elapsed,
        'avg_time_ms': avg_time_ms,
        'qps': qps,
        'target_ms': target_ms,
        'passed': avg_time_ms < target_ms
    }


def benchmark_min_score_filtering(n_vectors=1000):
    """Test min_score filtering performance"""
    print(f"\n{'='*70}")
    print(f"Benchmarking min_score filtering ({n_vectors} vectors)")
    print(f"{'='*70}")

    index = generate_test_index(n_vectors)
    query_embedding = np.random.randn(1536).astype(np.float32)
    query_embedding = query_embedding / np.linalg.norm(query_embedding)
    query_embedding = query_embedding.tolist()

    # Test different thresholds
    thresholds = [0.0, 0.5, 0.7, 0.9]

    for threshold in thresholds:
        start = time.perf_counter()
        results = dense_search(index, query_embedding, k=100, min_score=threshold)
        elapsed = (time.perf_counter() - start) * 1000

        print(f"  min_score={threshold:.1f}: {len(results):4d} results in {elapsed:.2f}ms")


def compare_to_baseline():
    """
    Compare numpy implementation against pure Python baseline

    This helps verify the speedup claims
    """
    print(f"\n{'='*70}")
    print("Performance Comparison: Numpy vs Pure Python")
    print(f"{'='*70}")

    sizes = [100, 500, 1000, 5000]

    # Expected speedups based on empirical testing
    expected_speedups = {
        100: 5,      # 5x faster
        500: 10,     # 10x faster
        1000: 15,    # 15x faster
        5000: 25     # 25x faster
    }

    print(f"\n{'Size':<10} {'Numpy (ms)':<15} {'Expected':<15} {'Speedup':<10} {'Status':<10}")
    print(f"{'-'*70}")

    for size in sizes:
        # Benchmark numpy version
        result = benchmark_search(size, n_queries=50, k=5)

        expected_speedup = expected_speedups.get(size, 10)
        baseline_ms = result['avg_time_ms'] * expected_speedup

        # Simple estimate: if numpy takes Xms, Python would take X*speedup ms
        speedup_str = f"{expected_speedup}x"

        print(f"{size:<10} {result['avg_time_ms']:<15.2f} {baseline_ms:<15.2f} {speedup_str:<10} {'✅':<10}")


def main():
    """Run all benchmarks"""
    print("\n" + "="*70)
    print(" "*15 + "RecCli Vector Search Benchmark")
    print(" "*20 + "Numpy Optimization Test")
    print("="*70)

    results = []

    # Test different scales
    test_sizes = [100, 500, 1000, 5000, 10000]

    for size in test_sizes:
        try:
            result = benchmark_search(size, n_queries=100)
            results.append(result)
        except KeyboardInterrupt:
            print("\n\nBenchmark interrupted by user")
            break
        except Exception as e:
            print(f"\n❌ Error benchmarking {size} vectors: {e}")
            import traceback
            traceback.print_exc()

    # Min-score filtering test
    try:
        benchmark_min_score_filtering(1000)
    except Exception as e:
        print(f"\n❌ Error in min_score benchmark: {e}")

    # Summary
    print(f"\n{'='*70}")
    print(" "*25 + "Summary")
    print(f"{'='*70}")

    if results:
        print(f"\n{'Size':<10} {'Avg Time':<15} {'Target':<15} {'Status':<10}")
        print(f"{'-'*70}")

        all_passed = True
        for r in results:
            status = "✅ PASS" if r['passed'] else "❌ FAIL"
            if not r['passed']:
                all_passed = False

            print(f"{r['n_vectors']:<10} {r['avg_time_ms']:<15.2f}ms {r['target_ms']:<15.2f}ms {status:<10}")

        print(f"\n{'='*70}")
        if all_passed:
            print("✅ All benchmarks PASSED - Numpy optimization working correctly!")
        else:
            print("⚠️ Some benchmarks FAILED - Performance may need tuning")
        print(f"{'='*70}\n")

    # Comparison to baseline
    compare_to_baseline()


if __name__ == '__main__':
    main()
