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
import re
import numpy as np

# ---------------------------------------------------------------------------
# Query-aware preview selection
# ---------------------------------------------------------------------------

# Sentence boundary: ., !, or ? followed by whitespace + an uppercase / opening
# delimiter, OR a paragraph break. Conservative — prefers under-splitting to
# over-splitting since search doesn't need linguistic perfection.
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z"\'\(\[])|\n\n+')

_PREVIEW_STOP_WORDS = {
    'the','a','an','and','or','but','in','on','at','to','for','of','with',
    'by','from','as','is','are','was','were','be','been','being','this','that',
    'it','its','if','then','than','so','do','does','did','can','could','would',
    'should','will','have','has','had',
}


def _split_sentences(text: str, hard_max: int = 500) -> List[str]:
    """Split text into sentences. Long 'sentences' (code blocks, URLs) are
    further broken on newlines so no single unit dwarfs the rest."""
    if not text:
        return []
    parts = _SENTENCE_SPLIT.split(text)
    out: List[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) > hard_max:
            for sub in p.split('\n'):
                sub = sub.strip()
                if sub:
                    out.append(sub[:hard_max])
        else:
            out.append(p)
    return out


def _score_best_sentence(query: str, content: str, target_chars: int = 260) -> Tuple[str, float]:
    """Return (best_sentence, bm25_score) for the query against content.

    Score is 0.0 when there is no meaningful match (returns first-N-chars as sentence).
    Otherwise score is the raw BM25 of the best sentence — unbounded; callers are
    responsible for normalizing across a candidate pool if using it for ranking.
    """
    if not content:
        return "", 0.0

    query_terms = [t for t in query.lower().split() if len(t) > 2 and t not in _PREVIEW_STOP_WORDS]
    if not query_terms:
        return content[:target_chars], 0.0

    sentences = _split_sentences(content)
    if len(sentences) <= 1:
        return content[:target_chars], 0.0

    query_set = set(query_terms)
    tokens_per = [s.lower().split() for s in sentences]
    df: Dict[str, int] = {}
    for tokens in tokens_per:
        for term in query_set & set(tokens):
            df[term] = df.get(term, 0) + 1

    if not df:
        return content[:target_chars], 0.0

    N = len(sentences)
    avg_len = max(1, sum(len(t) for t in tokens_per) / N)
    k1, b = 1.5, 0.75

    best_idx = 0
    best_score = 0.0
    for i, tokens in enumerate(tokens_per):
        score = 0.0
        dl = max(1, len(tokens))
        for term in query_terms:
            if term not in df:
                continue
            tf = tokens.count(term)
            if tf == 0:
                continue
            idf = math.log((N - df[term] + 0.5) / (df[term] + 0.5) + 1.0)
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * (dl / avg_len)))
        if score > best_score:
            best_score = score
            best_idx = i

    if best_score < 0.5:
        return content[:target_chars], 0.0

    best = sentences[best_idx]
    if len(best) < 120 and best_idx + 1 < len(sentences):
        combined = best + " " + sentences[best_idx + 1]
        if len(combined) <= target_chars:
            return combined, best_score
    if len(best) > target_chars:
        return best[:target_chars - 1] + "…", best_score
    return best, best_score


def _pick_best_sentence(query: str, content: str, target_chars: int = 260) -> str:
    """Backward-compatible thin wrapper around _score_best_sentence."""
    return _score_best_sentence(query, content, target_chars)[0]


def _enrich_and_rerank(
    sessions_dir: Path,
    results: List[Dict],
    query: str,
    top_k: int,
    sentence_boost: float = 0.0,
) -> List[Dict]:
    """Enrich content_previews with best sentences; optionally re-rank.

    Default behavior (sentence_boost=0.0): enrichment only — content_preview
    gets replaced with the query-relevant sentence, but final_score ordering
    is preserved. This is the conservative, zero-risk mode and is what ships
    to users.

    With sentence_boost > 0, deep candidates with strong sentence matches can
    leapfrog shallow top-ranked hits via an additive blend (norm-squared for
    non-linear damping of weak matches). EMPIRICAL NOTE: enabling the boost
    on small candidate pools (e.g. LongMemEval haystacks with only ~60-70
    messages total) caused regressions on enumeration-style questions —
    re-ranking concentrated results on fewer distinct sessions at the expense
    of diversity. Kept off by default until we have evidence it helps.
    """
    if not query or not results:
        return results[:top_k]

    from ..session.devsession import DevSession

    session_cache: Dict[str, DevSession] = {}
    enriched: List[Dict] = []

    for result in results:
        role = result.get('role', '')
        # Spans/summary-items keep their curated previews and don't get a boost
        if role in ('span', 'summary'):
            result = {**result, 'sentence_score': 0.0}
            enriched.append(result)
            continue

        session_id = result.get('session')
        msg_index = result.get('message_index')
        if not session_id or msg_index is None:
            result = {**result, 'sentence_score': 0.0}
            enriched.append(result)
            continue

        if session_id not in session_cache:
            session_file = sessions_dir / f"{session_id}.devsession"
            if not session_file.exists():
                result = {**result, 'sentence_score': 0.0}
                enriched.append(result)
                continue
            try:
                session_cache[session_id] = DevSession.load(session_file, verify_checksums=False)
            except Exception:
                result = {**result, 'sentence_score': 0.0}
                enriched.append(result)
                continue

        session = session_cache[session_id]
        conv = session.conversation
        if not (0 <= msg_index < len(conv)):
            result = {**result, 'sentence_score': 0.0}
            enriched.append(result)
            continue

        msg = conv[msg_index]
        full_content = msg.get('content', '') or ''
        if role == 'tool' and msg.get('tool_response'):
            full_content = f"{full_content}\n{msg['tool_response']}"

        best_sentence, sentence_score = _score_best_sentence(query, full_content)

        updated = {**result, 'sentence_score': sentence_score}
        if len(full_content) > 260 and best_sentence and best_sentence != (result.get('content_preview') or ''):
            updated['content_preview'] = best_sentence
        enriched.append(updated)

    # Blend sentence_score into final_score. Additive, scaled to pool's max
    # final_score, and NON-LINEAR (squared norm) so weak matches barely move
    # while strong matches can lift a deep hit into the top_k window.
    #
    # Concretely: a sentence at 30% of the pool's best gets only (0.3**2)=9% boost
    # while one at 90% gets (0.9**2)=81% boost. This prevents over-disruption of
    # rankings that were already correctly scored by the full pipeline, while
    # still rescuing deep hits where the match is clearly strong.
    max_sentence = max((r.get('sentence_score', 0.0) for r in enriched), default=0.0)
    max_final = max((r.get('final_score', 0.0) for r in enriched), default=0.0)
    if max_sentence > 0 and max_final > 0:
        for r in enriched:
            norm = r.get('sentence_score', 0.0) / max_sentence
            r['final_score'] = r.get('final_score', 0.0) + max_final * sentence_boost * (norm ** 2)

    enriched.sort(key=lambda x: x.get('final_score', 0.0), reverse=True)
    return enriched[:top_k]


def _candidate_pool_size(top_k: int) -> int:
    """Pool to scan for sentence re-ranking. Capped so very large top_k doesn't
    blow up disk I/O; floored at 30 so small top_k still gets a meaningful re-rank."""
    return min(max(top_k * 4, 30), 60)


def _log(sessions_dir: Path, component: str, message: str, severity: str = "warning") -> None:
    """Forward to hooks._log_issue with the project_root derived from sessions_dir.

    Sessions live at <project_root>/devsession/, so project_root is sessions_dir.parent.
    """
    try:
        from ..hooks.session_recorder import _log_issue
        _log_issue(component, message, severity=severity, project_root=sessions_dir.parent)
    except Exception:
        pass  # Logger must never take down the caller


def _msg_id_to_index(msg_id: str) -> Optional[int]:
    """Convert a msg_NNN identifier to its 0-based conversation index."""
    if not msg_id or not msg_id.startswith("msg_"):
        return None
    try:
        return int(msg_id[4:]) - 1
    except ValueError:
        return None


def validate_index_dimensions(index: Dict) -> Tuple[bool, Optional[str]]:
    """Validate that index embeddings have a consistent dimension.

    Returns (ok, reason). If ok is False, reason describes the mismatch.
    Caller should treat mismatch as a signal to force rebuild rather than
    mix dimensions — cosine on mixed dims raises in cosine_similarity().
    """
    declared = index.get('embedding', {}).get('dimensions')
    vectors = index.get('unified_vectors') or []
    if not vectors or declared is None:
        return True, None

    # Prefer matrix path if present — cheap to check
    mat = index.get('embeddings_matrix')
    if mat is not None:
        try:
            shape = getattr(mat, 'shape', None)
            if shape and len(shape) == 2 and shape[1] != declared:
                return False, f"embeddings_matrix dim {shape[1]} != declared {declared}"
        except Exception:
            pass
        return True, None

    # Else check inline vectors
    for v in vectors:
        emb = v.get('embedding')
        if emb:
            if len(emb) != declared:
                return False, f"vector {v.get('id', '?')} dim {len(emb)} != declared {declared}"
            break  # One check is enough — all vectors share a dim by construction
    return True, None


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
        tool_resp = (vector.get('tool_response_preview') or '').lower()
        full_text = f"{content} {tool_resp}" if tool_resp else content
        terms = set(full_text.split())
        doc_lengths.append(len(full_text.split()))

        for term in terms:
            df[term] = df.get(term, 0) + 1

    avg_doc_length = sum(doc_lengths) / len(doc_lengths) if doc_lengths else 0
    if avg_doc_length <= 0:
        avg_doc_length = 1.0

    # Calculate BM25 scores
    results = []

    for idx, vector in enumerate(vectors):
        content = vector.get('content_preview', '').lower()
        tool_resp = (vector.get('tool_response_preview') or '').lower()
        full_text = f"{content} {tool_resp}" if tool_resp else content
        doc_terms = full_text.split()
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


def _compute_bm25_weight(bm25_results: List[Dict], query: str) -> float:
    """Compute adaptive BM25 weight based on query term specificity.

    When query terms are rare in the corpus (high IDF), BM25 is more
    discriminative than dense embeddings. Boost BM25 contribution in those cases.

    Returns a weight multiplier for BM25 scores in RRF (1.0 = equal, >1 = BM25 favored).
    """
    if not bm25_results:
        return 1.0

    # Check score spread — a large gap between top and median BM25 scores
    # indicates the query terms are highly discriminative (domain-specific).
    scores = [r.get('bm25_score', 0) for r in bm25_results]
    if len(scores) < 3:
        return 1.0
    # Need at least one positive score to compute spread
    if scores[0] <= 0:
        return 1.0

    top_score = scores[0]  # already sorted descending
    median_score = scores[len(scores) // 2]

    if median_score <= 0:
        # Most docs don't match at all — query is very specific, BM25 dominant
        return 2.5

    spread_ratio = top_score / median_score
    if spread_ratio > 4.0:
        return 2.0  # Strong keyword signal
    elif spread_ratio > 2.0:
        return 1.5  # Moderate keyword signal

    return 1.0


def reciprocal_rank_fusion(
    dense_results: List[Dict],
    bm25_results: List[Dict],
    k0: int = 60,
    bm25_weight: float = 1.0,
) -> List[Dict]:
    """
    Reciprocal Rank Fusion (RRF) to combine dense and sparse results

    Args:
        dense_results: Results from dense search
        bm25_results: Results from BM25 search
        k0: RRF parameter (default 60)
        bm25_weight: Multiplier for BM25 RRF contribution (>1 = BM25 favored)

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

    # Add BM25 scores (with adaptive weight)
    for result in bm25_results:
        vec_id = result['id']
        rank = result.get('bm25_rank', 0)
        if rank > 0:
            score = bm25_weight * (1.0 / (k0 + rank))

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

    Precedence:
      1. Result kind (decisions decay slowly regardless of query wording)
      2. Query intent (only if kind is generic: note/log/code/doc)
      3. Default (3 days)

    Args:
        kind: Message kind (decision, code, problem, etc.) — the *result's* kind
        query: Query string (used only if kind is not intrinsically decision-like)

    Returns:
        τ in hours
    """
    # Kind-first: a decision is a decision regardless of how you searched for it
    if kind == 'decision':
        return 30 * 24.0  # 30 days
    if kind == 'problem':
        return 14 * 24.0  # 14 days — problems stay relevant longer than code

    query_lower = query.lower()

    # Error/debug queries: fast decay (8 hours) — only for generic-kind results
    if any(word in query_lower for word in ['error', 'crash', 'bug', 'failed', 'exception']):
        return 8.0

    # Decision-intent queries: slow decay even for generic-kind results
    if any(word in query_lower for word in ['decision', 'why', 'approach', 'design']):
        return 30 * 24.0

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

    has_dense = cosine_score > 0.0
    if has_dense and cosine_score < 0.25 and bm25_score < 5.0:
        return 0.0
    if not has_dense and bm25_score < 0.5:
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

        # Fall back to index-based lookup for messages without ID fields (e.g. from save_session_notes)
        if message is None:
            msg_idx = result.get("message_index")
            if msg_idx is not None and 0 <= msg_idx < len(session.conversation):
                message = session.conversation[msg_idx]

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
    current_section: str = 'default',
    file_path: Optional[str] = None,
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
        file_path: Filter results to messages that reference this file path

    Returns:
        List of search results with badges
    """
    # Load index, auto-building if missing
    index_path = sessions_dir / 'index.json'
    if not index_path.exists():
        # Attempt auto-build before giving up
        session_files = list(sessions_dir.glob("*.devsession")) if sessions_dir.exists() else []
        if session_files:
            try:
                from .vector_index import build_unified_index
                build_unified_index(sessions_dir, verbose=False)
            except Exception as e:
                _log(sessions_dir, "search", f"auto-build of unified index failed: {e}", severity="error")
        if not index_path.exists():
            return []

    with open(index_path, 'r') as f:
        index = json.load(f)

    # Validate embedding dimensions — a mismatch means dense search will raise.
    ok, reason = validate_index_dimensions(index)
    if not ok:
        _log(sessions_dir, "search", f"embedding dimension mismatch ({reason}); run rebuild_index", severity="error")

    # Dense search (requires embeddings — gracefully degrade to BM25-only if unavailable)
    dense_results = []
    try:
        if provider is None:
            from .embeddings import get_embedding_provider
            provider = get_embedding_provider()
        query_embedding = provider.embed(query)
        dense_results = dense_search(index, query_embedding, k=200)
    except Exception as e:
        _log(sessions_dir, "search.dense", f"dense search failed, falling back to BM25-only: {e}")

    # BM25 sparse search (always available — no API needed)
    bm25_results = bm25_search(index, query, k=200)

    # Adaptive BM25 weighting: boost BM25 when query terms are domain-specific
    bm25_weight = _compute_bm25_weight(bm25_results, query)

    # Reciprocal Rank Fusion (works with dense-only, BM25-only, or both)
    rrf_results = reciprocal_rank_fusion(dense_results, bm25_results, k0=60, bm25_weight=bm25_weight)

    # 4. Apply temporal filters
    if time:
        rrf_results = apply_temporal_filter(rrf_results, time)

    # 5. Apply scope filters
    if scope:
        rrf_results = apply_scope_filter(rrf_results, scope)

    # 5.6. File path filter — keep only results whose content mentions the file
    if file_path:
        rrf_results = _filter_by_file_path(rrf_results, file_path)

    # 5.5. Cross-check stale vector hits against the canonical session file.
    rrf_results = filter_deleted_results(sessions_dir, rrf_results)

    # 6. Apply boosts
    for result in rrf_results:
        result['final_score'] = apply_boosts(result, index, query, current_section)

    # 7. Filter out zero-scored results
    rrf_results = [r for r in rrf_results if r['final_score'] > 0]

    # 8. Sort by final score and return top k
    rrf_results.sort(key=lambda x: x['final_score'], reverse=True)

    # 9. Smart previews — enrich top_k previews with query-relevant sentences.
    #    Candidate pool is kept at top_k; re-ranking mode (sentence_boost > 0)
    #    is available in _enrich_and_rerank but off by default after empirical
    #    evidence it hurt enumeration queries on small haystacks.
    final = _enrich_and_rerank(sessions_dir, rrf_results[:top_k], query, top_k)

    # 10. Add badges
    for result in final:
        result['badges'] = compute_badges(result, index, current_section)

    return final


def search_expanded(
    sessions_dir: Path,
    query: str,
    top_k: int = 30,
    time: Optional[Dict] = None,
    scope: Optional[Dict] = None,
    provider=None,
    current_section: str = 'default',
    file_path: Optional[str] = None,
    max_variations: int = 3,
) -> List[Dict]:
    """Multi-query search with synonym expansion and result fusion (MMC).

    Runs the original query plus synonym-expanded variations through BM25,
    then fuses all result sets via RRF. Dense search runs once since
    embeddings already capture semantic similarity.

    Args:
        sessions_dir: Path to .devsessions directory
        query: Search query
        top_k: Number of results
        time: Temporal filter
        scope: Scope filter
        provider: Embedding provider
        current_section: Current working section
        file_path: File path filter
        max_variations: Max synonym variations to generate

    Returns:
        List of search results with badges
    """
    from .query_expansion import expand_query

    queries = expand_query(query, max_variations=max_variations)

    # Load index once
    index_path = sessions_dir / 'index.json'
    if not index_path.exists():
        session_files = list(sessions_dir.glob("*.devsession")) if sessions_dir.exists() else []
        if session_files:
            try:
                from .vector_index import build_unified_index
                build_unified_index(sessions_dir, verbose=False)
            except Exception as e:
                _log(sessions_dir, "search_expanded", f"auto-build of unified index failed: {e}", severity="error")
        if not index_path.exists():
            return []

    with open(index_path, 'r') as f:
        index = json.load(f)

    ok, reason = validate_index_dimensions(index)
    if not ok:
        _log(sessions_dir, "search_expanded", f"embedding dimension mismatch ({reason}); run rebuild_index", severity="error")

    # Dense search: run once with original query (embeddings capture synonymy)
    dense_results = []
    try:
        if provider is None:
            from .embeddings import get_embedding_provider
            provider = get_embedding_provider()
        query_embedding = provider.embed(query)
        dense_results = dense_search(index, query_embedding, k=200)
    except Exception as e:
        _log(sessions_dir, "search_expanded.dense", f"dense search failed, falling back to BM25-only: {e}")

    # BM25: run for each query variation, collect all results
    all_bm25 = []
    for q in queries:
        bm25_results = bm25_search(index, q, k=200)
        all_bm25.extend(bm25_results)

    # Deduplicate by id, keeping best BM25 score per document
    best_bm25 = {}
    for r in all_bm25:
        vid = r['id']
        if vid not in best_bm25 or r.get('bm25_score', 0) > best_bm25[vid].get('bm25_score', 0):
            best_bm25[vid] = r

    # Re-rank the deduplicated set
    deduped = sorted(best_bm25.values(), key=lambda x: x.get('bm25_score', 0), reverse=True)
    for rank, r in enumerate(deduped):
        r['bm25_rank'] = rank + 1

    # RRF fusion
    bm25_weight = _compute_bm25_weight(deduped, query)
    rrf_results = reciprocal_rank_fusion(dense_results, deduped, k0=60, bm25_weight=bm25_weight)

    # Standard post-processing pipeline
    if time:
        rrf_results = apply_temporal_filter(rrf_results, time)
    if scope:
        rrf_results = apply_scope_filter(rrf_results, scope)
    if file_path:
        rrf_results = _filter_by_file_path(rrf_results, file_path)
    rrf_results = filter_deleted_results(sessions_dir, rrf_results)

    for result in rrf_results:
        result['final_score'] = apply_boosts(result, index, query, current_section)
    rrf_results = [r for r in rrf_results if r['final_score'] > 0]
    rrf_results.sort(key=lambda x: x['final_score'], reverse=True)

    final = _enrich_and_rerank(sessions_dir, rrf_results[:top_k], query, top_k)

    for result in final:
        result['badges'] = compute_badges(result, index, current_section)

    return final


def _filter_by_file_path(results: List[Dict], file_path: str) -> List[Dict]:
    """Filter search results to those mentioning a specific file path."""
    # Match on filename or relative path (e.g. "webhook/route.ts" or "route.ts")
    basename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
    return [
        r for r in results
        if file_path in (r.get("content_preview") or r.get("content") or "")
        or basename in (r.get("content_preview") or r.get("content") or "")
        or file_path in str(r.get("tool_name", ""))
    ]


def search_by_file(
    sessions_dir: Path,
    file_path: str,
    top_k: int = 20,
) -> List[Dict]:
    """Find all conversation messages that reference a specific file.

    Scans all .devsession files directly (no index needed) for messages
    that mention the file path or basename in their content or tool_name.

    Args:
        sessions_dir: Path to .devsessions directory
        file_path: File path to search for (full or partial)
        top_k: Maximum results to return

    Returns:
        List of matching messages with session context
    """
    from ..session.devsession import DevSession

    basename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
    results = []

    session_files = sorted(
        list(sessions_dir.glob("*.devsession")) + list(sessions_dir.glob(".live_*.devsession")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    for sf in session_files:
        try:
            session = DevSession.load(sf, verify_checksums=False)
        except Exception:
            continue

        for idx, msg in enumerate(session.conversation):
            content = msg.get("content", "")
            tool_name = msg.get("tool_name", "")
            if file_path in content or basename in content or file_path in tool_name:
                results.append({
                    "session": sf.stem,
                    "session_file": str(sf),
                    "message_index": idx,
                    "role": msg.get("role", "?"),
                    "tool_name": tool_name,
                    "content_preview": content[:300],
                    "timestamp": msg.get("timestamp", ""),
                    "result_id": f"{sf.stem}_msg_{idx:03d}",
                })

        if len(results) >= top_k:
            break

    return results[:top_k]


def search_by_time_range(
    sessions_dir: Path,
    start_time: str,
    end_time: str,
    query: Optional[str] = None,
    top_k: int = 20,
    provider=None,
) -> List[Dict]:
    """Find conversation messages within a time range.

    Scans .devsession files for messages with timestamps in [start_time, end_time].
    Optionally filters by a query string for relevance.

    Args:
        sessions_dir: Path to .devsessions directory
        start_time: ISO timestamp (inclusive) e.g. "2026-03-29" or "2026-03-29T10:00:00"
        end_time: ISO timestamp (inclusive) e.g. "2026-03-29T23:59:59"
        query: Optional text filter — only return messages containing this string
        top_k: Maximum results to return
        provider: Embedding provider (unused, for future semantic filtering)

    Returns:
        List of matching messages with session context
    """
    from ..session.devsession import DevSession

    # Normalize short dates to full-day ranges
    if len(start_time) == 10:  # "2026-03-29"
        start_time = start_time + "T00:00:00"
    if len(end_time) == 10:
        end_time = end_time + "T23:59:59"

    results = []

    session_files = sorted(
        list(sessions_dir.glob("*.devsession")) + list(sessions_dir.glob(".live_*.devsession")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    query_lower = query.lower() if query else None

    for sf in session_files:
        try:
            session = DevSession.load(sf, verify_checksums=False)
        except Exception:
            continue

        for idx, msg in enumerate(session.conversation):
            ts = msg.get("timestamp", "")
            if not ts:
                continue

            if ts < start_time or ts > end_time:
                continue

            content = msg.get("content", "")
            if query_lower and query_lower not in content.lower():
                continue

            # When a query is provided, pick the most relevant sentence as the preview.
            # Without a query, fall back to first-300-char truncation.
            if query and len(content) > 300:
                preview = _pick_best_sentence(query, content, target_chars=300)
            else:
                preview = content[:300]

            results.append({
                "session": sf.stem,
                "session_file": str(sf),
                "message_index": idx,
                "role": msg.get("role", "?"),
                "tool_name": msg.get("tool_name", ""),
                "content_preview": preview,
                "timestamp": ts,
                "result_id": f"{sf.stem}_msg_{idx:03d}",
            })

        if len(results) >= top_k:
            break

    return results[:top_k]


_SUMMARY_ITEM_PREFIXES = ("dec_", "chg_", "prb_", "iss_", "nxt_")


def _find_summary_item(summary: Optional[Dict], item_id: str) -> Optional[Dict]:
    """Look up a summary item by its ID across all categories."""
    if not summary:
        return None
    for category in ("decisions", "code_changes", "problems_solved", "open_issues", "next_steps"):
        for item in summary.get(category, []):
            if item.get("id") == item_id:
                return item
    return None


def _find_span(spans: list, span_id: str) -> Optional[Dict]:
    """Look up a span by its ID."""
    for span in spans:
        if span.get("id") == span_id:
            return span
    return None


def expand_result(sessions_dir: Path, result_id: str, context_window: int = 5) -> Optional[Dict]:
    """
    Expand a search result to show full context using tri-layer traversal.

    The expansion strategy depends on what was hit:
    - Summary item (dec_*, chg_*, etc.): follow message_range for exact slice,
      highlight references, include linked spans
    - Span (spn_*): use span's start_index/end_index for the semantic region
    - Message (msg_*): use ±context_window around the message

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

    msg_id = target_vector.get('message_id', '')
    conv_len = len(session.conversation)

    # --- Summary item hit: traverse summary → message_range → conversation ---
    if any(msg_id.startswith(p) for p in _SUMMARY_ITEM_PREFIXES):
        summary_item = _find_summary_item(session.summary, msg_id)
        if summary_item:
            # Collect linked spans first — used both for range fallback and returned payload
            linked_spans = []
            for spn_id in summary_item.get("span_ids", []):
                span = _find_span(session.spans, spn_id)
                if span:
                    linked_spans.append(span)

            # Safe-fallback cascade for missing/malformed message_range
            # (spec: references → span → full range with degraded flag)
            mr = summary_item.get("message_range") or {}
            start_idx = mr.get("start_index")
            end_idx = mr.get("end_index")
            degraded = False

            if start_idx is None or end_idx is None or end_idx <= start_idx:
                # 1. Try references
                ref_indices = [
                    i for i in (_msg_id_to_index(r) for r in summary_item.get("references", []))
                    if i is not None
                ]
                if ref_indices:
                    start_idx = min(ref_indices)
                    end_idx = max(ref_indices) + 1
                # 2. Try first linked span
                elif linked_spans:
                    span0 = linked_spans[0]
                    start_idx = span0.get("start_index", 0)
                    end_idx = span0.get("end_index") or conv_len
                # 3. Full range, flagged degraded
                else:
                    start_idx = 0
                    end_idx = conv_len
                    degraded = True

            # Clamp to conversation bounds
            start_idx = max(0, min(start_idx, conv_len))
            end_idx = max(start_idx, min(end_idx, conv_len))

            context_messages = session.conversation[start_idx:end_idx]

            return {
                'result': target_vector,
                'session': session_id,
                'hit_type': 'summary_item',
                'summary_item': summary_item,
                'linked_spans': linked_spans,
                'references': summary_item.get("references", []),
                'context_start': start_idx,
                'context_end': end_idx,
                'context_messages': context_messages,
                'degraded_range': degraded,
                'summary': session.summary,
            }

    # --- Span hit: use span boundaries ---
    if msg_id.startswith("spn_"):
        span = _find_span(session.spans, msg_id)
        if span:
            start_idx = span.get("start_index", 0)
            end_idx = span.get("end_index") or (start_idx + context_window + 1)
            start_idx = max(0, min(start_idx, conv_len))
            end_idx = max(start_idx, min(end_idx, conv_len))

            context_messages = session.conversation[start_idx:end_idx]

            return {
                'result': target_vector,
                'session': session_id,
                'hit_type': 'span',
                'span': span,
                'references': span.get("references", []),
                'context_start': start_idx,
                'context_end': end_idx,
                'context_messages': context_messages,
                'summary': session.summary,
            }

    # --- Message hit: ±context_window ---
    msg_index = target_vector.get('message_index', 0)
    if 0 <= msg_index < conv_len and session.conversation[msg_index].get("deleted"):
        return None

    start_idx = max(0, msg_index - context_window)
    end_idx = min(conv_len, msg_index + context_window + 1)

    context_messages = session.conversation[start_idx:end_idx]

    return {
        'result': target_vector,
        'session': session_id,
        'hit_type': 'message',
        'message_index': msg_index,
        'context_start': start_idx,
        'context_end': end_idx,
        'context_messages': context_messages,
        'summary': session.summary,
    }
