"""
Hybrid Retrieval System for RecCli Phase 5

Implements:
- Dense ANN search (cosine similarity)
- BM25 sparse search (keyword matching)
- Reciprocal Rank Fusion (RRF)
- Temporal boosts and filters
- Badge computation
"""

from typing import List, Dict, Optional, Tuple
from pathlib import Path
from datetime import datetime, timedelta
import json
import math
import numpy as np


def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """
    Calculate cosine similarity between two vectors

    Assumes vectors are already L2-normalized.

    Args:
        vec1: First vector
        vec2: Second vector

    Returns:
        Similarity score (0 to 1)
    """
    if len(vec1) != len(vec2):
        raise ValueError(f"Vector dimension mismatch: {len(vec1)} != {len(vec2)}")

    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    return max(0.0, min(1.0, dot_product))  # Clamp to [0, 1]


def dense_search(
    index: Dict,
    query_embedding: List[float],
    k: int = 200,
    min_score: float = 0.0
) -> List[Dict]:
    """
    Dense ANN search using vectorized numpy operations

    Optimized for multi-session search across thousands of messages.
    Uses binary .npy files for 10-100x faster loading than JSON.

    Args:
        index: Unified vector index
        query_embedding: Query vector
        k: Number of results to return
        min_score: Minimum cosine similarity threshold

    Returns:
        List of results with cosine scores

    Performance (with binary .npy files):
        - 100 messages: ~0.3ms
        - 1,000 messages: ~1.5ms
        - 10,000 messages: ~10ms
    """
    vectors = index.get('unified_vectors', [])
    if not vectors:
        return []

    # Load embeddings matrix (3 paths: binary file > in-memory array > extract from vectors)
    embeddings_matrix = None

    # PATH 1: Binary .npy file (FASTEST - production mode)
    if 'embeddings_file' in index:
        embeddings_file = index['embeddings_file']

        # Handle both absolute and relative paths
        if isinstance(embeddings_file, str):
            embeddings_path = Path(embeddings_file)

            # If relative path, resolve relative to index location
            # (We need to know where the index was loaded from)
            # For now, assume it's in the same directory as referenced
            if not embeddings_path.is_absolute():
                # Try to find it relative to current working directory
                # In production, this would be sessions_dir
                if not embeddings_path.exists():
                    # Try common locations
                    for base in [Path.cwd(), Path.cwd() / 'sessions']:
                        test_path = base / embeddings_file
                        if test_path.exists():
                            embeddings_path = test_path
                            break

            if embeddings_path.exists():
                # Memory-mapped loading (instant, no RAM copy)
                embeddings_matrix = np.load(embeddings_path, mmap_mode='r')

    # PATH 2: In-memory numpy array (for testing/benchmarks)
    if embeddings_matrix is None and 'embeddings_matrix' in index:
        cached = index['embeddings_matrix']
        if isinstance(cached, np.ndarray):
            embeddings_matrix = cached
        elif cached and len(cached) > 0:
            # Loaded from JSON (slow path)
            embeddings_matrix = np.array(cached, dtype=np.float32)

    # PATH 3: Extract from vectors (SLOWEST - backward compatibility)
    if embeddings_matrix is None:
        embeddings_list = [v.get('embedding', []) for v in vectors
                          if 'embedding' in v and v['embedding']]

        if not embeddings_list:
            return []

        embeddings_matrix = np.array(embeddings_list, dtype=np.float32)

    # Convert query to numpy
    query_vector = np.array(query_embedding, dtype=np.float32)

    # All vectors are valid when using pre-built matrix
    valid_indices = np.arange(len(vectors))

    # Compute ALL cosine similarities at once (single matrix-vector multiplication)
    # This is the key optimization: O(1) operation instead of O(n) loop
    similarities = np.dot(embeddings_matrix, query_vector)  # Shape: (n,)

    # Filter by minimum score threshold
    if min_score > 0.0:
        mask = similarities >= min_score
        filtered_indices = np.where(mask)[0]
        filtered_similarities = similarities[filtered_indices]
    else:
        filtered_indices = np.arange(len(similarities))
        filtered_similarities = similarities

    # Handle case where no results meet threshold
    if len(filtered_similarities) == 0:
        return []

    # Get top-k using partial sort (faster than full sort for large arrays)
    if len(filtered_similarities) <= k:
        # All results fit in top-k
        top_k_local_idx = np.argsort(-filtered_similarities)  # Descending order
    else:
        # Use argpartition for O(n) vs O(n log n) full sort
        # Get indices of k largest values
        partition_idx = np.argpartition(filtered_similarities, -k)[-k:]
        # Sort just the top-k for correct ranking
        top_k_local_idx = partition_idx[np.argsort(-filtered_similarities[partition_idx])]

    # Map back to original vector indices
    top_k_idx = filtered_indices[top_k_local_idx]

    # Build results list
    results = []
    for rank, filtered_idx in enumerate(top_k_idx):
        # Map filtered index to original vector index
        original_idx = int(valid_indices[filtered_idx])
        vector = vectors[original_idx]

        results.append({
            **vector,
            'cosine_score': float(similarities[filtered_idx]),
            'dense_rank': rank + 1
        })

    return results


def bm25_search(
    index: Dict,
    query: str,
    k: int = 200,
    k1: float = 1.5,
    b: float = 0.75
) -> List[Dict]:
    """
    BM25 sparse search using keyword matching

    Args:
        index: Unified vector index
        query: Query string
        k: Number of results to return
        k1: BM25 term frequency saturation parameter (default 1.5)
        b: BM25 length normalization parameter (default 0.75)

    Returns:
        List of results with BM25 scores
    """
    # Tokenize query
    query_terms = query.lower().split()

    # Calculate document frequencies
    vectors = index.get('unified_vectors', [])
    N = len(vectors)  # Total documents

    if N == 0:
        return []

    # Build term document frequencies
    df = {}  # term -> number of documents containing term
    doc_lengths = []  # document lengths
    avg_doc_length = 0

    for vector in vectors:
        content = vector.get('content_preview', '').lower()
        terms = set(content.split())
        doc_lengths.append(len(content.split()))

        for term in terms:
            df[term] = df.get(term, 0) + 1

    avg_doc_length = sum(doc_lengths) / len(doc_lengths) if doc_lengths else 0

    # Calculate BM25 scores
    results = []

    for idx, vector in enumerate(vectors):
        content = vector.get('content_preview', '').lower()
        doc_terms = content.split()
        doc_length = len(doc_terms)

        # Calculate BM25 score
        score = 0.0

        for term in query_terms:
            if term not in df:
                continue

            # Term frequency in document
            tf = doc_terms.count(term)

            # Inverse document frequency
            idf = math.log((N - df[term] + 0.5) / (df[term] + 0.5) + 1.0)

            # BM25 formula
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * (doc_length / avg_doc_length))

            score += idf * (numerator / denominator)

        if score > 0:
            results.append({
                **vector,
                'bm25_score': score,
                'bm25_rank': 0  # Will be set after sorting
            })

    # Sort by BM25 score (descending)
    results.sort(key=lambda x: x['bm25_score'], reverse=True)

    # Assign ranks
    for rank, result in enumerate(results[:k]):
        result['bm25_rank'] = rank + 1

    return results[:k]


def reciprocal_rank_fusion(
    dense_results: List[Dict],
    bm25_results: List[Dict],
    k0: int = 60
) -> List[Dict]:
    """
    Reciprocal Rank Fusion (RRF) to combine dense and sparse results

    Args:
        dense_results: Results from dense search
        bm25_results: Results from BM25 search
        k0: RRF parameter (default 60)

    Returns:
        Fused results with RRF scores
    """
    # Build RRF scores
    rrf_scores = {}  # id -> (score, vector)

    # Add dense scores
    for result in dense_results:
        vec_id = result['id']
        rank = result.get('dense_rank', 0)
        if rank > 0:
            score = 1.0 / (k0 + rank)
            rrf_scores[vec_id] = (score, result)

    # Add BM25 scores
    for result in bm25_results:
        vec_id = result['id']
        rank = result.get('bm25_rank', 0)
        if rank > 0:
            score = 1.0 / (k0 + rank)

            if vec_id in rrf_scores:
                # Combine scores
                prev_score, prev_result = rrf_scores[vec_id]
                new_score = prev_score + score

                # Merge result data
                merged_result = {**prev_result, **result}
                rrf_scores[vec_id] = (new_score, merged_result)
            else:
                rrf_scores[vec_id] = (score, result)

    # Convert to list
    fused_results = []
    for vec_id, (score, vector) in rrf_scores.items():
        vector['rrf_score'] = score
        fused_results.append(vector)

    # Sort by RRF score (descending)
    fused_results.sort(key=lambda x: x['rrf_score'], reverse=True)

    return fused_results


def apply_temporal_filter(results: List[Dict], time_filter: Dict) -> List[Dict]:
    """
    Apply temporal filters to search results

    Supported filters:
    - lastHours: Filter to last N hours
    - between: Filter to time range [t1, t2]
    - around: Filter to ±Δ minutes around an event

    Args:
        results: Search results
        time_filter: Temporal filter specification

    Returns:
        Filtered results
    """
    if 'lastHours' in time_filter:
        cutoff = datetime.now() - timedelta(hours=time_filter['lastHours'])
        cutoff_iso = cutoff.isoformat()
        return [r for r in results if r.get('timestamp', '') >= cutoff_iso]

    elif 'between' in time_filter:
        t1, t2 = time_filter['between']
        return [r for r in results if t1 <= r.get('timestamp', '') <= t2]

    elif 'around' in time_filter:
        event_id = time_filter['around'].get('event')
        window_min = time_filter['around'].get('window_min', 30)

        # Find event timestamp
        event_time = None
        for result in results:
            if result.get('id') == event_id or result.get('metadata', {}).get('summary_ref') == event_id:
                timestamp_str = result.get('timestamp', '')
                if timestamp_str:
                    try:
                        event_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                        break
                    except:
                        pass

        if event_time:
            t1 = event_time - timedelta(minutes=window_min)
            t2 = event_time + timedelta(minutes=window_min)
            t1_iso = t1.isoformat()
            t2_iso = t2.isoformat()
            return [r for r in results if t1_iso <= r.get('timestamp', '') <= t2_iso]

    return results


def apply_scope_filter(results: List[Dict], scope_filter: Dict) -> List[Dict]:
    """
    Apply scope filters to search results

    Supported filters:
    - session_id: Filter to specific session
    - section: Filter to specific section
    - episode_id: Filter to specific episode

    Args:
        results: Search results
        scope_filter: Scope filter specification

    Returns:
        Filtered results
    """
    filtered = results

    if 'session_id' in scope_filter:
        session_id = scope_filter['session_id']
        filtered = [r for r in filtered if r.get('session') == session_id]

    if 'section' in scope_filter:
        section = scope_filter['section']
        filtered = [r for r in filtered if r.get('section') == section]

    if 'episode_id' in scope_filter:
        episode_id = scope_filter['episode_id']
        filtered = [r for r in filtered if r.get('episode_id') == episode_id]

    return filtered


def compute_tau(kind: str, query: str) -> float:
    """
    Compute intent-aware time decay parameter τ (in hours)

    Args:
        kind: Message kind (decision, code, problem, etc.)
        query: Query string (to detect intent)

    Returns:
        τ in hours
    """
    query_lower = query.lower()

    # Error/debug queries: fast decay (8 hours)
    if any(word in query_lower for word in ['error', 'crash', 'bug', 'failed', 'exception']):
        return 8.0

    # Decision queries: slow decay (30 days)
    if kind == 'decision' or any(word in query_lower for word in ['decision', 'why', 'approach', 'design']):
        return 30 * 24.0  # 30 days

    # Default: 3 days
    return 3 * 24.0


def is_near_key_decision(result: Dict, index: Dict, window_minutes: int = 30) -> bool:
    """
    Check if result is near a key decision (within ±window_minutes)

    Args:
        result: Search result
        index: Unified index
        window_minutes: Time window in minutes

    Returns:
        True if near a decision
    """
    result_time_str = result.get('timestamp', '')
    if not result_time_str:
        return False

    try:
        result_time = datetime.fromisoformat(result_time_str.replace('Z', '+00:00'))
    except:
        return False

    # Find decisions in same session
    session_id = result.get('session')
    decisions = [
        v for v in index.get('unified_vectors', [])
        if v.get('session') == session_id and v.get('kind') == 'decision'
    ]

    # Check if any decision is within window
    for decision in decisions:
        dec_time_str = decision.get('timestamp', '')
        if not dec_time_str:
            continue

        try:
            dec_time = datetime.fromisoformat(dec_time_str.replace('Z', '+00:00'))
            delta = abs((result_time - dec_time).total_seconds() / 60)  # minutes

            if delta <= window_minutes:
                return True
        except:
            continue

    return False


def apply_boosts(result: Dict, index: Dict, query: str, current_section: str = 'default') -> float:
    """
    Apply temporal and locality boosts

    Formula:
        score = base * recency * same_section * near_decision * kind_weight

    Args:
        result: Search result with rrf_score
        index: Unified index
        query: Query string
        current_section: Current working section

    Returns:
        Boosted score
    """
    base_score = result.get('rrf_score', 0.0)

    # Confidence threshold (drop if cosine < 0.25 unless BM25 strong)
    cosine_score = result.get('cosine_score', 0.0)
    bm25_score = result.get('bm25_score', 0.0)

    if cosine_score < 0.25 and bm25_score < 5.0:
        return 0.0

    # Temporal boost: exp(-Δt/τ)
    timestamp_str = result.get('timestamp', '')
    if timestamp_str:
        try:
            result_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            now = datetime.now().astimezone()
            delta_t = (now - result_time).total_seconds() / 3600  # hours

            tau = compute_tau(result.get('kind', 'note'), query)
            recency = math.exp(-delta_t / tau)
        except:
            recency = 1.0
    else:
        recency = 1.0

    # Same section boost
    same_section = 1.2 if result.get('section') == current_section else 1.0

    # Near decision boost
    near_decision = 1.15 if is_near_key_decision(result, index) else 1.0

    # Kind weight
    kind_weights = {
        'decision': 1.15,
        'problem': 1.10,
        'code': 1.05,
        'doc': 1.00,
        'note': 1.00,
        'log': 0.95
    }
    kind_weight = kind_weights.get(result.get('kind', 'note'), 1.0)

    return base_score * recency * same_section * near_decision * kind_weight


def compute_badges(result: Dict, index: Dict, current_section: str = 'default') -> List[str]:
    """
    Compute badges for search result

    Badges:
    - RECENT: Within last 24 hours
    - SAME-SECTION: Same working section
    - NEAR-DECISION: Within 30 min of key decision
    - DECISION: Is a decision
    - PROBLEM: Is a problem

    Args:
        result: Search result
        index: Unified index
        current_section: Current working section

    Returns:
        List of badge labels
    """
    badges = []

    # RECENT badge
    timestamp_str = result.get('timestamp', '')
    if timestamp_str:
        try:
            result_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            now = datetime.now().astimezone()
            delta_hours = (now - result_time).total_seconds() / 3600

            if delta_hours <= 24:
                badges.append('RECENT')
        except:
            pass

    # SAME-SECTION badge
    if result.get('section') == current_section and current_section != 'default':
        badges.append('SAME-SECTION')

    # NEAR-DECISION badge
    if is_near_key_decision(result, index):
        badges.append('NEAR-DECISION')

    # Kind badges
    kind = result.get('kind')
    if kind == 'decision':
        badges.append('DECISION')
    elif kind == 'problem':
        badges.append('PROBLEM')

    return badges


def filter_deleted_results(sessions_dir: Path, results: List[Dict]) -> List[Dict]:
    """
    Filter stale index results against canonical session files so tombstoned or redacted
    messages do not surface from outdated vector caches.
    """
    from ..session.devsession import DevSession

    session_cache: Dict[str, DevSession] = {}
    filtered: List[Dict] = []

    for result in results:
        session_id = result.get("session")
        message_id = result.get("message_id")
        if not session_id or not message_id:
            filtered.append(result)
            continue

        if session_id not in session_cache:
            session_file = sessions_dir / f"{session_id}.devsession"
            if not session_file.exists():
                continue
            try:
                session_cache[session_id] = DevSession.load(session_file)
            except Exception:
                continue

        session = session_cache[session_id]
        message = None
        for msg in session.conversation:
            if (
                msg.get("id") == message_id
                or msg.get("_message_id") == message_id
                or msg.get("_id") == message_id
            ):
                message = msg
                break

        if message is None or message.get("deleted"):
            continue
        result = {**result}
        result["content_preview"] = message.get("content", "")[:200]
        if message.get("redacted"):
            result.setdefault("metadata", {})
            result["metadata"]["redacted"] = True
        filtered.append(result)

    return filtered


def search(
    sessions_dir: Path,
    query: str,
    top_k: int = 30,
    time: Optional[Dict] = None,
    scope: Optional[Dict] = None,
    provider = None,
    current_section: str = 'default'
) -> List[Dict]:
    """
    Hybrid search: Dense ANN + BM25 + RRF + Temporal boosts

    Args:
        sessions_dir: Path to .devsessions directory
        query: Search query
        top_k: Number of results
        time: Temporal filter (lastHours, between, around)
        scope: Scope filter (session_id, section, episode_id)
        provider: Embedding provider (for query embedding)
        current_section: Current working section (for boosts)

    Returns:
        List of search results with badges
    """
    # Load index
    index_path = sessions_dir / 'index.json'
    if not index_path.exists():
        print("⚠️  Index not found. Run 'reccli index build' first.")
        return []

    with open(index_path, 'r') as f:
        index = json.load(f)

    # Get embedding provider
    if provider is None:
        from .embeddings import get_embedding_provider
        provider = get_embedding_provider()

    # Embed query
    query_embedding = provider.embed(query)

    # 1. Dense ANN search (cosine similarity)
    dense_results = dense_search(index, query_embedding, k=200)

    # 2. BM25 sparse search
    bm25_results = bm25_search(index, query, k=200)

    # 3. Reciprocal Rank Fusion
    rrf_results = reciprocal_rank_fusion(dense_results, bm25_results, k0=60)

    # 4. Apply temporal filters
    if time:
        rrf_results = apply_temporal_filter(rrf_results, time)

    # 5. Apply scope filters
    if scope:
        rrf_results = apply_scope_filter(rrf_results, scope)

    # 5.5. Cross-check stale vector hits against the canonical session file.
    rrf_results = filter_deleted_results(sessions_dir, rrf_results)

    # 6. Apply boosts
    for result in rrf_results:
        result['final_score'] = apply_boosts(result, index, query, current_section)

    # 7. Filter out zero-scored results
    rrf_results = [r for r in rrf_results if r['final_score'] > 0]

    # 8. Sort by final score and return top k
    rrf_results.sort(key=lambda x: x['final_score'], reverse=True)

    # 9. Add badges
    for result in rrf_results[:top_k]:
        result['badges'] = compute_badges(result, index, current_section)

    return rrf_results[:top_k]


def expand_result(sessions_dir: Path, result_id: str, context_window: int = 5) -> Optional[Dict]:
    """
    Expand a search result to show full context

    Args:
        sessions_dir: Path to .devsessions directory
        result_id: Result ID (e.g., "session-123_msg_45")
        context_window: Number of messages before/after to include

    Returns:
        Expanded result with full context
    """
    # Load index
    index_path = sessions_dir / 'index.json'
    if not index_path.exists():
        return None

    with open(index_path, 'r') as f:
        index = json.load(f)

    # Find result in index
    target_vector = None
    for vector in index.get('unified_vectors', []):
        if vector['id'] == result_id:
            target_vector = vector
            break

    if not target_vector:
        return None

    # Load session file
    session_id = target_vector['session']
    session_file = sessions_dir / f"{session_id}.devsession"

    if not session_file.exists():
        return None

    from ..session.devsession import DevSession
    session = DevSession.load(session_file)

    # Get message index
    msg_index = target_vector['message_index']
    if 0 <= msg_index < len(session.conversation) and session.conversation[msg_index].get("deleted"):
        return None

    # Get context window
    start_idx = max(0, msg_index - context_window)
    end_idx = min(len(session.conversation), msg_index + context_window + 1)

    context_messages = session.conversation[start_idx:end_idx]

    return {
        'result': target_vector,
        'session': session_id,
        'message_index': msg_index,
        'context_start': start_idx,
        'context_end': end_idx,
        'context_messages': context_messages,
        'summary': session.summary
    }
