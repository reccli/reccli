# Phase 5: Vector Embeddings & Search - Implementation

**Goal**: Semantic search over sessions with hybrid recall (dense + sparse) and time-aware boosts

**Duration**: 2-3 days

**Status**: 🟡 Ready to implement

---

## Overview

Phase 5 implements the unified vector index system that enables intelligent cross-session context retrieval. This combines:
- **Dense search** (semantic/vector similarity)
- **Sparse search** (BM25 keyword matching)
- **Temporal indexing** (time as first-class index)
- **Hybrid scoring** (RRF + temporal boosts)

---

## Architecture Decision

**Individual session files + unified index** (from unified_vector_index.md):

```
.devsessions/
├── session-20241027-143045.devsession   # Individual session files
├── session-20241028-091230.devsession
├── session-20241029-153420.devsession
└── index.json                           # Unified vector index (all sessions)
```

**Why this architecture**:
- ✅ Each session = manageable file (~100KB-1MB)
- ✅ Clean git diffs (one session per commit)
- ✅ Cross-session search via unified index
- ✅ Corruption affects one session, not all
- ✅ Easy to archive old sessions

---

## Index Schema v1.1.0

Based on unified_vector_index.md with temporal extensions from PROJECT_PLAN.md:

```json
{
  "format": "devsession-index",
  "version": "1.1.0",
  "created_at": "2025-11-02T00:00:00Z",
  "last_updated": "2025-11-02T00:00:00Z",
  "total_sessions": 10,
  "total_messages": 1847,
  "total_vectors": 1847,

  "embedding": {
    "provider": "openai",
    "model": "text-embedding-3-small",
    "dimensions": 1536,
    "distance_metric": "cosine"
  },

  "unified_vectors": [
    {
      "id": "span_7a1e",
      "session": "session-20241027-143045",
      "message_id": "msg_045",
      "message_index": 45,
      "timestamp": "2024-10-27T14:45:23Z",

      // Temporal indexing (new in v1.1)
      "section": "billing-retry",
      "episode_id": 15,
      "t_start": "2024-10-27T14:42:00Z",
      "t_end": "2024-10-27T14:49:59Z",
      "t_day": "2024-10-27",
      "t_hour": "2024-10-27T14",

      // Content
      "role": "assistant",
      "kind": "decision",  // decision | code | problem | note | log | doc
      "content_preview": "I recommend a modal...",
      "text_hash": "blake3:...",

      // Embedding
      "embedding": [0.123, -0.456, ...],  // 1536-dim
      "embed_model": "text-embedding-3-small",
      "embed_provider": "openai",
      "embed_dim": 1536,
      "embed_ts": "2025-11-02T00:00:00Z",

      // Metadata
      "metadata": {
        "summary_ref": "dec_001",
        "tokens": 234
      }
    }
  ],

  "session_manifest": [
    {
      "session_id": "session-20241027-143045",
      "file": "session-20241027-143045.devsession",
      "date": "2024-10-27",
      "created_at": "2024-10-27T14:30:45Z",
      "duration_seconds": 8067,
      "message_count": 187,
      "vector_range": {
        "start": 0,
        "end": 186
      },
      "summary": "Built Stripe Connect integration for automated payouts",
      "tags": ["stripe", "payments", "integration"],
      "has_decisions": true,
      "has_problems": true
    }
  ],

  "statistics": {
    "total_duration_hours": 42.5,
    "average_session_length_minutes": 68,
    "most_active_days": ["2024-10-27", "2024-10-29", "2024-11-01"],
    "total_decisions": 23,
    "total_problems_solved": 15,
    "total_code_changes": 87
  }
}
```

### New Fields (v1.1.0)

**Temporal indexing**:
- `section`: Current work section (e.g., "billing-retry", "auth-refactor")
- `episode_id`: Episode number (from heuristic detection)
- `t_start`/`t_end`: Span time range (ISO timestamps)
- `t_day`/`t_hour`: Partitioning keys for fast filtering

**Embedding provenance**:
- `embed_model`, `embed_provider`, `embed_dim`, `embed_ts`: Track embedding metadata
- `text_hash`: Content hash for cache invalidation (blake3)

**Content classification**:
- `kind`: Message type (decision/code/problem/note/log/doc)

---

## Implementation Tasks

### Task 1: Embedding Provider Abstraction

**File**: `reccli/embeddings.py`

```python
class EmbeddingProvider:
    """Base class for embedding providers"""

    def embed(self, text: str) -> List[float]:
        raise NotImplementedError

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError

    @property
    def dimensions(self) -> int:
        raise NotImplementedError

    @property
    def model_name(self) -> str:
        raise NotImplementedError


class OpenAIEmbeddings(EmbeddingProvider):
    """OpenAI text-embedding-3-small (default)"""

    def __init__(self, api_key: str = None, model: str = "text-embedding-3-small"):
        import openai
        self.client = openai.OpenAI(api_key=api_key)
        self.model = model
        self._dimensions = 1536 if "small" in model else 3072

    def embed(self, text: str) -> List[float]:
        response = self.client.embeddings.create(
            model=self.model,
            input=text
        )
        return response.data[0].embedding

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        # Batch at 256-512 tokens/chunk for cost efficiency
        response = self.client.embeddings.create(
            model=self.model,
            input=texts
        )
        return [item.embedding for item in response.data]

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_name(self) -> str:
        return self.model


class LocalEmbeddings(EmbeddingProvider):
    """Local sentence-transformers (optional)"""

    def __init__(self, model: str = "sentence-transformers/all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model)
        self._model_name = model
        self._dimensions = self.model.get_sentence_embedding_dimension()

    def embed(self, text: str) -> List[float]:
        return self.model.encode(text).tolist()

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return self.model.encode(texts).tolist()

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_name(self) -> str:
        return self._model_name


def get_embedding_provider(config: Dict = None) -> EmbeddingProvider:
    """Factory function for embedding providers"""
    if not config:
        config = {}

    provider = config.get('provider', 'openai')

    if provider == 'openai':
        return OpenAIEmbeddings(
            api_key=config.get('api_key'),
            model=config.get('model', 'text-embedding-3-small')
        )
    elif provider == 'local':
        return LocalEmbeddings(
            model=config.get('model', 'sentence-transformers/all-MiniLM-L6-v2')
        )
    else:
        raise ValueError(f"Unknown embedding provider: {provider}")
```

**Subtasks**:
- [ ] Create `EmbeddingProvider` base class
- [ ] Implement `OpenAIEmbeddings` (default)
- [ ] Implement `LocalEmbeddings` (optional, using sentence-transformers)
- [ ] Add factory function `get_embedding_provider()`
- [ ] Support batch embedding (256-512 tokens/chunk)
- [ ] Add caching based on `text_hash`

---

### Task 2: Generate Embeddings for Session

**File**: `reccli/devsession.py` (extend)

```python
def generate_embeddings(self, provider: EmbeddingProvider = None) -> int:
    """
    Generate embeddings for all messages in conversation

    Returns:
        Number of messages embedded
    """
    if not provider:
        from .embeddings import get_embedding_provider
        provider = get_embedding_provider()

    # Check if already embedded with same model
    if self.vector_index:
        existing_model = self.vector_index.get('embedding', {}).get('model')
        if existing_model == provider.model_name:
            print(f"⚠ Embeddings already exist for model {existing_model}")
            return 0

    # Batch embed all messages
    texts = [msg['content'] for msg in self.conversation]
    print(f"Generating embeddings for {len(texts)} messages...")

    embeddings = provider.embed_batch(texts)

    # Attach embeddings to messages
    for msg, embedding in zip(self.conversation, embeddings):
        msg['embedding'] = embedding
        msg['embed_model'] = provider.model_name
        msg['embed_provider'] = 'openai'  # or detect from provider
        msg['embed_dim'] = provider.dimensions
        msg['embed_ts'] = datetime.now().isoformat()

    print(f"✓ Generated {len(embeddings)} embeddings")
    return len(embeddings)
```

**Subtasks**:
- [ ] Add `generate_embeddings()` method to DevSession
- [ ] Embed summary items first (decisions/problems/next_steps)
- [ ] Add per-message embeddings
- [ ] Attach embedding metadata to each message
- [ ] Cache based on text_hash (don't regenerate)

---

### Task 3: Build Unified Index

**File**: `reccli/vector_index.py` (new)

Implement the full index building logic from unified_vector_index.md:

```python
def build_unified_index(sessions_dir: Path) -> Dict:
    """
    Build unified vector index from all .devsession files
    """
    print("🔍 Building unified vector index...")

    index = {
        'format': 'devsession-index',
        'version': '1.1.0',
        'created_at': datetime.now().isoformat(),
        'last_updated': datetime.now().isoformat(),
        'total_sessions': 0,
        'total_messages': 0,
        'total_vectors': 0,
        'embedding': {
            'provider': 'openai',
            'model': 'text-embedding-3-small',
            'dimensions': 1536,
            'distance_metric': 'cosine'
        },
        'unified_vectors': [],
        'session_manifest': [],
        'statistics': {
            'total_duration_hours': 0,
            'total_decisions': 0,
            'total_problems_solved': 0,
            'total_code_changes': 0
        }
    }

    # Get all session files, sorted chronologically
    session_files = sorted(
        sessions_dir.glob('*.devsession'),
        key=lambda f: f.name
    )

    vector_offset = 0

    for session_file in session_files:
        print(f"  Processing {session_file.name}...")

        # Load session
        from .devsession import DevSession
        session = DevSession.load(session_file)
        session_id = session_file.stem

        # Extract vectors from conversation
        for msg in session.conversation:
            if 'embedding' not in msg:
                continue

            # Classify message type
            msg_type = classify_message_type(msg, session.summary)

            # Add to unified index
            index['unified_vectors'].append({
                'id': f"{session_id}_{msg['id']}",
                'session': session_id,
                'message_id': msg['id'],
                'message_index': msg.get('index', 0),
                'timestamp': msg.get('timestamp', ''),

                # Temporal
                'section': 'default',  # TODO: extract from session
                'episode_id': 0,  # TODO: detect episodes
                't_start': msg.get('timestamp', ''),
                't_end': msg.get('timestamp', ''),
                't_day': msg.get('timestamp', '')[:10],
                't_hour': msg.get('timestamp', '')[:13],

                # Content
                'role': msg['role'],
                'kind': msg_type,
                'content_preview': msg['content'][:200],
                'text_hash': compute_text_hash(msg['content']),

                # Embedding
                'embedding': msg['embedding'],
                'embed_model': msg.get('embed_model', 'unknown'),
                'embed_provider': msg.get('embed_provider', 'unknown'),
                'embed_dim': msg.get('embed_dim', 0),
                'embed_ts': msg.get('embed_ts', ''),

                # Metadata
                'metadata': {
                    'summary_ref': find_summary_ref(msg, session.summary),
                    'tokens': count_tokens(msg['content'])
                }
            })

        # Add to session manifest
        message_count = len([m for m in session.conversation if 'embedding' in m])

        index['session_manifest'].append({
            'session_id': session_id,
            'file': session_file.name,
            'date': session.metadata.get('created_at', '')[:10],
            'created_at': session.metadata.get('created_at', ''),
            'duration_seconds': session.get_duration(),
            'message_count': message_count,
            'vector_range': {
                'start': vector_offset,
                'end': vector_offset + message_count - 1
            },
            'summary': session.summary.get('overview', 'No summary') if session.summary else 'No summary',
            'tags': extract_tags(session),
            'has_decisions': len(session.summary.get('decisions', [])) > 0 if session.summary else False,
            'has_problems': len(session.summary.get('problems_solved', [])) > 0 if session.summary else False
        })

        # Update statistics
        index['statistics']['total_duration_hours'] += session.get_duration() / 3600
        if session.summary:
            index['statistics']['total_decisions'] += len(session.summary.get('decisions', []))
            index['statistics']['total_problems_solved'] += len(session.summary.get('problems_solved', []))
            index['statistics']['total_code_changes'] += len(session.summary.get('code_changes', []))

        vector_offset += message_count
        index['total_sessions'] += 1

    index['total_messages'] = vector_offset
    index['total_vectors'] = vector_offset

    # Save index
    index_path = sessions_dir / 'index.json'
    with open(index_path, 'w') as f:
        json.dump(index, f, indent=2)

    print(f"✓ Index built: {index['total_sessions']} sessions, {index['total_vectors']} vectors")
    return index
```

**Subtasks**:
- [ ] Create `build_unified_index()` function
- [ ] Implement `classify_message_type()` helper
- [ ] Implement `find_summary_ref()` helper
- [ ] Implement `extract_tags()` helper
- [ ] Implement `compute_text_hash()` (blake3)
- [ ] Save index to `.devsessions/index.json`

---

### Task 4: Incremental Index Updates

**File**: `reccli/vector_index.py`

```python
def update_index_with_new_session(sessions_dir: Path, session: DevSession) -> Dict:
    """
    Add new session to unified index (incremental update)
    """
    print(f"📝 Updating index with {session_file.stem}...")

    index_path = sessions_dir / 'index.json'

    # Load existing index
    if index_path.exists():
        with open(index_path, 'r') as f:
            index = json.load(f)
    else:
        # First session - build from scratch
        return build_unified_index(sessions_dir)

    session_id = session_file.stem
    vector_offset = len(index['unified_vectors'])

    # Extract vectors from new session (same logic as build)
    # ...

    # Update index
    index['last_updated'] = datetime.now().isoformat()
    index['total_sessions'] += 1
    index['total_messages'] += message_count
    index['total_vectors'] += message_count

    # Save updated index
    with open(index_path, 'w') as f:
        json.dump(index, f, indent=2)

    print(f"✓ Index updated: {index['total_sessions']} sessions, {index['total_vectors']} vectors")
    return index
```

**Subtasks**:
- [ ] Implement `update_index_with_new_session()`
- [ ] Load existing index
- [ ] Append new vectors
- [ ] Update manifest and statistics
- [ ] Save updated index

---

### Task 5: Hybrid Retrieval (Dense + BM25 + RRF)

**File**: `reccli/search.py` (new)

```python
def search(
    sessions_dir: Path,
    query: str,
    top_k: int = 30,
    time: Dict = None,
    scope: Dict = None
) -> List[Dict]:
    """
    Hybrid search: Dense ANN + BM25 + RRF + Temporal boosts

    Args:
        sessions_dir: Path to .devsessions directory
        query: Search query
        top_k: Number of results
        time: Temporal filter (lastHours, between, around)
        scope: Scope filter (session_id, section, episode_id)

    Returns:
        List of search results with badges
    """
    # Load index
    index_path = sessions_dir / 'index.json'
    with open(index_path, 'r') as f:
        index = json.load(f)

    # 1. Dense ANN search (cosine similarity)
    dense_results = dense_search(index, query, k=200)

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

    # 6. Apply boosts
    for result in rrf_results:
        result['score'] = apply_boosts(result, index, query)

    # 7. Sort by final score and return top k
    rrf_results.sort(key=lambda x: x['score'], reverse=True)

    # 8. Add badges
    for result in rrf_results[:top_k]:
        result['badges'] = compute_badges(result, index)

    return rrf_results[:top_k]
```

**Scoring Formula** (from unified_vector_index.md):

```python
def apply_boosts(result: Dict, index: Dict, query: str) -> float:
    """
    Apply temporal and locality boosts

    score = base * recency * same_section * near_decision * kind_weight
    """
    base_score = result['rrf_score']

    # Temporal boost: exp(-Δt/τ)
    delta_t = compute_time_delta(result['timestamp'])
    tau = compute_tau(result['kind'], query)  # Intent-aware
    recency = math.exp(-delta_t / tau)

    # Same section boost
    current_section = 'default'  # TODO: get from current session
    same_section = 1.2 if result['section'] == current_section else 1.0

    # Near decision boost
    near_decision = 1.15 if is_near_key_decision(result, index) else 1.0

    # Kind weight
    kind_weights = {
        'decision': 1.15,
        'problem': 1.10,
        'code': 1.05,
        'doc': 1.00,
        'log': 0.95
    }
    kind_weight = kind_weights.get(result['kind'], 1.0)

    # Confidence threshold (drop if cosine < 0.25 unless BM25 strong)
    if result.get('cosine_score', 0) < 0.25 and result.get('bm25_score', 0) < 5.0:
        return 0

    return base_score * recency * same_section * near_decision * kind_weight
```

**Subtasks**:
- [ ] Implement `dense_search()` (cosine similarity)
- [ ] Implement `bm25_search()` (sparse keyword matching)
- [ ] Implement `reciprocal_rank_fusion()`
- [ ] Implement temporal filters (`lastHours`, `between`, `around`)
- [ ] Implement scope filters (session_id, section, episode_id)
- [ ] Implement boost scoring with intent-aware τ
- [ ] Add confidence threshold (drop if cosine < 0.25)
- [ ] Compute badges (recent, same-section, near-decision)

---

### Task 6: Temporal Scopes API

**File**: `reccli/search.py`

```python
def apply_temporal_filter(results: List[Dict], time_filter: Dict) -> List[Dict]:
    """
    Apply temporal filters to search results

    Supported filters:
    - lastHours: Filter to last N hours
    - between: Filter to time range [t1, t2]
    - around: Filter to ±Δ minutes around an event
    """
    if 'lastHours' in time_filter:
        cutoff = datetime.now() - timedelta(hours=time_filter['lastHours'])
        cutoff_iso = cutoff.isoformat()
        return [r for r in results if r['timestamp'] >= cutoff_iso]

    elif 'between' in time_filter:
        t1, t2 = time_filter['between']
        return [r for r in results if t1 <= r['timestamp'] <= t2]

    elif 'around' in time_filter:
        event_id = time_filter['around']['event']
        window_min = time_filter['around']['window_min']

        # Find event timestamp
        # TODO: Look up event in index
        event_time = None  # Get from index

        if event_time:
            t1 = event_time - timedelta(minutes=window_min)
            t2 = event_time + timedelta(minutes=window_min)
            return [r for r in results if t1.isoformat() <= r['timestamp'] <= t2.isoformat()]

    return results
```

**Subtasks**:
- [ ] Implement `lastHours` filter
- [ ] Implement `between` filter
- [ ] Implement `around` event filter (±Δ minutes)
- [ ] Add O(log n) time range search using sorted `t_day`/`t_hour`

---

### Task 7: CLI Commands

**File**: `reccli/cli.py` (extend)

```bash
# Build/rebuild index
reccli index build

# Search across all sessions
reccli search "webhook signature verification" --top-k 10

# Search with temporal filter
reccli search "error" --last-hours 48

# Search with scope filter
reccli search "retry logic" --section "billing-retry"

# Expand result
reccli expand span_7a1e

# Validate index
reccli index validate

# Show index stats
reccli index stats
```

**Subtasks**:
- [ ] Add `index build` command
- [ ] Add `search` command with filters
- [ ] Add `expand` command to retrieve full context
- [ ] Add `index validate` command
- [ ] Add `index stats` command
- [ ] Pretty-print results with badges

---

## Acceptance Tests

From PROJECT_PLAN.md + unified_vector_index.md:

- [ ] **Query "why modal"** returns decision item first; expanding yields exact discussion
- [ ] **"yesterday's crash"** favors last day's logs over older similar text
- [ ] **Results show badges**: `[RECENT]`, `[SAME-SECTION]`, `[NEAR-DECISION]`
- [ ] **`search("error", time={'around': {'event': 'dec_7a1e', 'window_min': 30}})`** returns logs ±30min from decision
- [ ] **Confidence threshold**: Drop results with cosine <0.25 unless BM25 strong
- [ ] **Cross-session search**: Query in session 10 finds relevant context from session 3
- [ ] **Index validation**: Detects missing session files, vector count mismatches
- [ ] **Incremental updates**: New session added without rebuilding entire index
- [ ] **Embedding caching**: Don't re-embed if text_hash unchanged

---

## File Structure

```
packages/reccli-core/
├── reccli/
│   ├── embeddings.py           # NEW - Embedding providers
│   ├── vector_index.py         # NEW - Index building & updates
│   ├── search.py               # NEW - Hybrid search & scoring
│   ├── devsession.py           # EXTEND - Add generate_embeddings()
│   └── cli.py                  # EXTEND - Add search/index commands
├── tests/
│   ├── test_embeddings.py      # NEW - Test embedding providers
│   ├── test_vector_index.py    # NEW - Test index building
│   └── test_search.py          # NEW - Test hybrid search
└── docs/progress/phases/PHASE_5_IMPLEMENTATION.md  # This file
```

---

## Configuration

**File**: `.reccli/config.yml` (new)

```yaml
embedding:
  provider: openai  # openai | local
  model: text-embedding-3-small
  api_key: ${OPENAI_API_KEY}
  batch_size: 512
  cache_embeddings: true

retrieval:
  dense_k: 200
  bm25_k: 200
  rrf_k0: 60
  weights:
    dense: 0.6
    bm25: 0.4
    same_section: 1.2
    near_decision: 1.15
    kind:
      decision: 1.15
      problem: 1.10
      code: 1.05
      doc: 1.00
      log: 0.95
  decay:
    default_days: 3
    error_hours: 8
    decision_days: 30
  confidence_threshold: 0.25
```

---

## Migration from v1.0.0 to v1.1.0

**Steps**:
1. Bump `version` to `"1.1.0"` in index schema
2. Backfill new fields:
   - `section`: Extract from file path/topic or set to "default"
   - `t_start`/`t_end`: Copy from `timestamp` if unknown
   - `t_day`/`t_hour`: Derive from `timestamp`
   - `embed_*`: Add embedding metadata
   - `text_hash`: Compute blake3 of content
3. **Do NOT re-embed** unless changing provider/model
4. Keep a `migrations[]` note in the file

---

## Dependencies

**New dependencies** (add to `requirements.txt`):

```txt
openai>=1.0.0           # For text-embedding-3-small
sentence-transformers   # Optional: for local embeddings
numpy                   # For vector operations
rank-bm25              # For BM25 sparse search
blake3                 # For text hashing
pyyaml                 # For config file
```

---

## Definition of Done

✅ `reccli search "why modal"` returns decision item with badges; "expand 42-50" works

**Checklist**:
- [ ] Embedding providers working (OpenAI + local fallback)
- [ ] Unified index builds from sessions
- [ ] Incremental index updates on new session
- [ ] Hybrid search (Dense + BM25 + RRF) working
- [ ] Temporal filters working (lastHours, between, around)
- [ ] Temporal boosts applied (exp(-Δt/τ))
- [ ] Badges computed (recent, same-section, near-decision)
- [ ] CLI commands working (search, expand, index)
- [ ] All acceptance tests passing
- [ ] Documentation updated

---

## Next Steps After Phase 5

Phase 5 completes the search infrastructure. Next phases build on this:

- **Phase 6**: Memory Middleware uses Phase 5 search for context hydration
- **Phase 7**: Preemptive Compaction uses Phase 5 to find key spans
- **Phase 10**: .devproject uses Phase 5 for cross-session reasoning

---

**Ready to implement!** 🚀
