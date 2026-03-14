# Unified Vector Index for Cross-Session Context

## Overview

RecCli uses a **unified vector index** to enable intelligent cross-session context retrieval. Each coding session is stored as an individual `.devsession` file, but all sessions share a unified vector index that allows searching across the entire project history.

**Architecture Decision:** Individual files + unified index (Option B)

---

## Why This Architecture?

### The Problem
If sessions are completely isolated, context is lost over time:
```
Session 3: "We solved webhook signatures by using req.rawBody"
Session 10: "How did we solve webhooks?" → AI: "I don't know, I can't see session 3"
```

### The Solution
Unified vector index lets AI search ALL past sessions:
```
Session 10: "How did we solve webhooks?"
AI searches unified index → Finds session-003
AI loads relevant messages from session-003.devsession
AI: "In session 3, we solved it by using req.rawBody. Here's the full context..."
```

### Why Not One Giant File?
Single .devsession file for entire project would:
- ❌ Grow forever (100 sessions = 50MB+)
- ❌ Create massive git diffs
- ❌ Risk total data loss on corruption
- ❌ Be slow to load

### Why Not Separate Indexes Per Session?
Would lose cross-session intelligence:
- ❌ Can't search "all sessions about authentication"
- ❌ Can't find "when we first discussed webhooks"
- ❌ Context fragmented across files

---

## File Structure

```
project-root/
├── .devproject                              # Project overview (always loaded)
├── .devsessions/
│   ├── session-20241027-143045.devsession   # Session 1: Full conversation + vectors
│   ├── session-20241028-091230.devsession   # Session 2: Full conversation + vectors
│   ├── session-20241029-153420.devsession   # Session 3: Full conversation + vectors
│   ├── session-20241101-104512.devsession   # Session 10: Current session
│   └── index.json                           # Unified vector index (all sessions)
└── .gitignore                               # Excludes .devproject and .devsessions/
```

**Key Points:**
- Each session = separate `.devsession` file with full conversation + embeddings
- One `index.json` = unified vector index spanning ALL sessions
- `.devproject` = project-level context (separate from sessions)

---

## Unified Index Format

### Schema

**File:** `.devsessions/index.json`

```json
{
  "format": "devsession-index",
  "version": "1.0.0",
  "created_at": "2024-10-27T14:30:00Z",
  "last_updated": "2024-11-01T10:45:00Z",
  "total_sessions": 10,
  "total_messages": 1847,
  "total_vectors": 1847,

  "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
  "dimensions": 384,
  "distance_metric": "cosine",

  "unified_vectors": [
    {
      "id": "s001_msg_001",
      "session": "session-20241027-143045",
      "message_id": "msg_001",
      "message_index": 1,
      "timestamp": "2024-10-27T14:30:45Z",
      "role": "user",
      "content_preview": "Let's build the Stripe integration",
      "embedding": [0.123, -0.456, 0.789, ...],  // 384-dimensional vector
      "metadata": {
        "type": "discussion",
        "files_mentioned": [],
        "tokens": 8
      }
    },
    {
      "id": "s001_msg_045",
      "session": "session-20241027-143045",
      "message_id": "msg_045",
      "message_index": 45,
      "timestamp": "2024-10-27T14:45:23Z",
      "role": "assistant",
      "content_preview": "I recommend Stripe Connect because it eliminates manual reconciliation...",
      "embedding": [0.234, -0.567, 0.890, ...],
      "metadata": {
        "type": "decision",
        "summary_ref": "dec_001",  // Links to summary item in session file
        "tokens": 234
      }
    },
    {
      "id": "s003_msg_134",
      "session": "session-20241029-153420",
      "message_id": "msg_134",
      "message_index": 134,
      "timestamp": "2024-10-29T15:47:22Z",
      "role": "user",
      "content_preview": "The webhook signature verification is failing",
      "embedding": [0.345, -0.678, 0.901, ...],
      "metadata": {
        "type": "problem",
        "summary_ref": "prob_001",
        "tokens": 12
      }
    }
    // ... all messages from all sessions
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
    },
    {
      "session_id": "session-20241028-091230",
      "file": "session-20241028-091230.devsession",
      "date": "2024-10-28",
      "created_at": "2024-10-28T09:12:30Z",
      "duration_seconds": 5420,
      "message_count": 234,
      "vector_range": {
        "start": 187,
        "end": 420
      },
      "summary": "Debugged webhook signature verification issues",
      "tags": ["webhooks", "debugging", "stripe"],
      "has_decisions": false,
      "has_problems": true
    }
    // ... all sessions
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

### Field Descriptions

**Root Level:**
- `format`: Always "devsession-index"
- `version`: Semantic version for schema evolution
- `total_sessions`: Count of sessions indexed
- `total_messages`: Count of messages across all sessions
- `total_vectors`: Count of embeddings in unified_vectors
- `embedding_model`: Model used for all vectors
- `dimensions`: Vector dimensionality (384 for all-MiniLM-L6-v2)

**unified_vectors Array:**
- `id`: Unique identifier (format: `s{session_num}_msg_{msg_id}`)
- `session`: Session ID this message belongs to
- `message_id`: Message ID within that session
- `message_index`: Sequential position in that session (1-based)
- `timestamp`: When message was created
- `role`: "user", "assistant", "tool", "system"
- `content_preview`: First 200 chars (for quick scanning)
- `embedding`: Full vector (384-dim for all-MiniLM-L6-v2)
- `metadata.type`: "discussion", "decision", "code_change", "problem", "solution"
- `metadata.summary_ref`: Links to summary item in session file (if applicable)

**session_manifest Array:**
- `session_id`: Unique session identifier
- `file`: Filename of .devsession file
- `date`: ISO date (for filtering)
- `message_count`: Number of messages in this session
- `vector_range`: Indices in unified_vectors array for this session
- `summary`: One-sentence session summary
- `tags`: Keywords for filtering
- `has_decisions/has_problems`: Quick filters

---

## Building the Unified Index

### Initial Index Creation with Binary Export

When first session completes or when rebuilding index:

```python
def build_unified_index(sessions_dir):
    """
    Build unified vector index from all .devsession files

    Now exports embeddings as binary .npy file for 200x faster loading
    """
    print("🔍 Building unified vector index...")

    index = {
        'format': 'devsession-index',
        'version': '1.1.0',  # Updated for binary storage
        'created_at': datetime.now().isoformat(),
        'last_updated': datetime.now().isoformat(),
        'total_sessions': 0,
        'total_messages': 0,
        'total_vectors': 0,
        'embedding_model': 'text-embedding-3-small',  # OpenAI default
        'dimensions': 1536,
        'distance_metric': 'cosine',
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
        sessions_dir.glob('session-*.devsession'),
        key=lambda f: f.name
    )

    vector_offset = 0

    for session_file in session_files:
        print(f"  Processing {session_file.name}...")

        # Load session
        session = json.loads(session_file.read_text())
        session_id = session['metadata']['session_id']

        # Extract vectors from conversation
        for msg in session['conversation']:
            if 'embedding' not in msg:
                continue  # Skip messages without embeddings

            # Determine message type from metadata or summary
            msg_type = classify_message_type(msg, session.get('summary'))

            # Add to unified index
            index['unified_vectors'].append({
                'id': f"{session_id}_{msg['id']}",
                'session': session_id,
                'message_id': msg['id'],
                'message_index': msg['index'],
                'timestamp': msg['timestamp'],
                'role': msg['role'],
                'content_preview': msg['content'][:200],
                'embedding': msg['embedding'],
                'metadata': {
                    'type': msg_type,
                    'summary_ref': find_summary_ref(msg, session.get('summary')),
                    'tokens': msg.get('metadata', {}).get('tokens', 0)
                }
            })

        # Add to session manifest
        message_count = len([m for m in session['conversation'] if 'embedding' in m])

        index['session_manifest'].append({
            'session_id': session_id,
            'file': session_file.name,
            'date': session['metadata']['created_at'][:10],  # YYYY-MM-DD
            'created_at': session['metadata']['created_at'],
            'duration_seconds': session['metadata'].get('duration_seconds', 0),
            'message_count': message_count,
            'vector_range': {
                'start': vector_offset,
                'end': vector_offset + message_count - 1
            },
            'summary': session.get('summary', {}).get('overview', 'No summary'),
            'tags': extract_tags(session),
            'has_decisions': len(session.get('summary', {}).get('decisions', [])) > 0,
            'has_problems': len(session.get('summary', {}).get('problems_solved', [])) > 0
        })

        # Update statistics
        index['statistics']['total_duration_hours'] += session['metadata'].get('duration_seconds', 0) / 3600
        index['statistics']['total_decisions'] += len(session.get('summary', {}).get('decisions', []))
        index['statistics']['total_problems_solved'] += len(session.get('summary', {}).get('problems_solved', []))
        index['statistics']['total_code_changes'] += len(session.get('summary', {}).get('code_changes', []))

        vector_offset += message_count
        index['total_sessions'] += 1

    index['total_messages'] = vector_offset
    index['total_vectors'] = vector_offset

    # Export embeddings as binary .npy file (PRODUCTION OPTIMIZATION)
    if index['unified_vectors']:
        print("  Exporting embeddings as binary file...")

        embeddings_list = [
            v['embedding'] for v in index['unified_vectors']
            if 'embedding' in v
        ]

        if embeddings_list:
            embeddings_matrix = np.array(embeddings_list, dtype=np.float32)

            # Save as binary file (memory-mapped loading)
            embeddings_path = sessions_dir / '.index_embeddings.npy'
            np.save(embeddings_path, embeddings_matrix)

            # Reference in index (don't duplicate embeddings in JSON!)
            index['embeddings_file'] = '.index_embeddings.npy'

            matrix_size_mb = embeddings_matrix.nbytes / (1024 * 1024)
            print(f"    Binary file: {matrix_size_mb:.1f} MB ({embeddings_matrix.shape[0]} vectors)")

    # Save index (without embedding duplicates)
    index_path = sessions_dir / 'index.json'
    with open(index_path, 'w') as f:
        json.dump(index, f, indent=2)

    print(f"✓ Index built: {index['total_sessions']} sessions, {index['total_vectors']} vectors")
    print(f"  Search performance: ~{0.34 * (index['total_vectors'] / 1000):.2f}ms for {index['total_vectors']} vectors")
    return index


def classify_message_type(msg, summary):
    """
    Classify message type based on content and summary links
    """
    if summary:
        # Check if message is referenced in decisions
        for decision in summary.get('decisions', []):
            if msg['id'] in decision.get('references', []):
                return 'decision'

        # Check if in problems_solved
        for problem in summary.get('problems_solved', []):
            if msg['id'] in problem.get('references', []):
                return 'problem' if msg['role'] == 'user' else 'solution'

        # Check if in code_changes
        for change in summary.get('code_changes', []):
            if msg['id'] in change.get('references', []):
                return 'code_change'

    # Default
    return 'discussion'


def find_summary_ref(msg, summary):
    """
    Find which summary item references this message
    """
    if not summary:
        return None

    for decision in summary.get('decisions', []):
        if msg['id'] in decision.get('references', []):
            return decision['id']

    for problem in summary.get('problems_solved', []):
        if msg['id'] in problem.get('references', []):
            return problem['id']

    for change in summary.get('code_changes', []):
        if msg['id'] in change.get('references', []):
            return change['id']

    return None


def extract_tags(session):
    """
    Extract relevant tags from session
    """
    tags = set()

    # From summary
    summary = session.get('summary', {})
    overview = summary.get('overview', '').lower()

    # Common tech keywords
    keywords = ['stripe', 'payment', 'webhook', 'auth', 'database',
                'api', 'frontend', 'backend', 'testing', 'deployment']

    for keyword in keywords:
        if keyword in overview:
            tags.add(keyword)

    # From decisions
    for decision in summary.get('decisions', []):
        decision_text = decision.get('decision', '').lower()
        for keyword in keywords:
            if keyword in decision_text:
                tags.add(keyword)

    return sorted(list(tags))
```

---

## Incremental Index Updates

When new session completes:

```python
def update_index_with_new_session(sessions_dir, new_session):
    """
    Add new session to unified index (incremental update)
    """
    print(f"📝 Updating index with {new_session['metadata']['session_id']}...")

    index_path = sessions_dir / 'index.json'

    # Load existing index
    if index_path.exists():
        index = json.loads(index_path.read_text())
    else:
        # First session - build from scratch
        return build_unified_index(sessions_dir)

    session_id = new_session['metadata']['session_id']
    vector_offset = len(index['unified_vectors'])

    # Extract vectors from new session
    new_vectors = []
    for msg in new_session['conversation']:
        if 'embedding' not in msg:
            continue

        msg_type = classify_message_type(msg, new_session.get('summary'))

        new_vectors.append({
            'id': f"{session_id}_{msg['id']}",
            'session': session_id,
            'message_id': msg['id'],
            'message_index': msg['index'],
            'timestamp': msg['timestamp'],
            'role': msg['role'],
            'content_preview': msg['content'][:200],
            'embedding': msg['embedding'],
            'metadata': {
                'type': msg_type,
                'summary_ref': find_summary_ref(msg, new_session.get('summary')),
                'tokens': msg.get('metadata', {}).get('tokens', 0)
            }
        })

    # Append to unified vectors
    index['unified_vectors'].extend(new_vectors)

    # Add to manifest
    message_count = len(new_vectors)
    index['session_manifest'].append({
        'session_id': session_id,
        'file': f"{session_id}.devsession",
        'date': new_session['metadata']['created_at'][:10],
        'created_at': new_session['metadata']['created_at'],
        'duration_seconds': new_session['metadata'].get('duration_seconds', 0),
        'message_count': message_count,
        'vector_range': {
            'start': vector_offset,
            'end': vector_offset + message_count - 1
        },
        'summary': new_session.get('summary', {}).get('overview', 'No summary'),
        'tags': extract_tags(new_session),
        'has_decisions': len(new_session.get('summary', {}).get('decisions', [])) > 0,
        'has_problems': len(new_session.get('summary', {}).get('problems_solved', [])) > 0
    })

    # Update metadata
    index['last_updated'] = datetime.now().isoformat()
    index['total_sessions'] += 1
    index['total_messages'] += message_count
    index['total_vectors'] += message_count

    # Update statistics
    index['statistics']['total_duration_hours'] += new_session['metadata'].get('duration_seconds', 0) / 3600
    index['statistics']['total_decisions'] += len(new_session.get('summary', {}).get('decisions', []))
    index['statistics']['total_problems_solved'] += len(new_session.get('summary', {}).get('problems_solved', []))
    index['statistics']['total_code_changes'] += len(new_session.get('summary', {}).get('code_changes', []))

    # Save updated index
    with open(index_path, 'w') as f:
        json.dump(index, f, indent=2)

    print(f"✓ Index updated: {index['total_sessions']} sessions, {index['total_vectors']} vectors")
    return index
```

---

## Cross-Session Search

### Production-Grade Vector Search with Binary Storage

**Critical Performance Update (November 20, 2025):** The unified index now uses **binary `.npy` files** for blazing-fast multi-session search.

**File Structure:**
```
.devsessions/
├── index.json                  # Metadata only (~100KB/1000 msgs)
├── .index_embeddings.npy       # Binary embedding matrix (~6MB/1000 msgs)
├── session-001.devsession
└── session-002.devsession
```

**Performance (Production Benchmarks):**
- 100 messages: 0.13ms (7,409 QPS)
- 1,000 messages: 0.34ms (2,941 QPS)
- 10,000 messages: 3.67ms (272 QPS)
- 100,000 messages: ~50ms (still interactive!)

**Real-World Multi-Session Search:**
- 20 sessions (4,000 vectors): 1.5ms (was 200ms - 133x faster)
- 50 sessions (10,000 vectors): 3.7ms (was 500ms - 135x faster)

### Vectorized Search Implementation

```python
def search_all_sessions(project_dir, query, top_k=10):
    """
    Search across all sessions using unified index with binary embeddings

    Performance: <5ms for 10,000 messages
    """
    sessions_dir = project_dir / '.devsessions'
    index_path = sessions_dir / 'index.json'

    # Load index metadata
    index = json.loads(index_path.read_text())

    # Load embeddings from binary file (FAST - memory-mapped)
    embeddings_matrix = None
    if 'embeddings_file' in index:
        embeddings_path = sessions_dir / index['embeddings_file']
        if embeddings_path.exists():
            # Memory-mapped loading - instant, no RAM copy
            embeddings_matrix = np.load(embeddings_path, mmap_mode='r')

    # Fallback: extract from vectors (legacy compatibility)
    if embeddings_matrix is None:
        embeddings_matrix = np.array(
            [v['embedding'] for v in index['unified_vectors']],
            dtype=np.float32
        )

    # Embed query
    query_embedding = embed_text(query)  # sentence-transformers or OpenAI
    query_vector = np.array(query_embedding, dtype=np.float32)

    # Compute ALL similarities at once (vectorized - FAST!)
    similarities = np.dot(embeddings_matrix, query_vector)

    # Top-K selection (O(n) not O(n log n))
    if top_k < len(similarities):
        top_indices = np.argpartition(-similarities, top_k)[:top_k]
        top_indices = top_indices[np.argsort(-similarities[top_indices])]
    else:
        top_indices = np.argsort(-similarities)[:top_k]

    # Build results
    results = []
    for idx in top_indices:
        vector_item = index['unified_vectors'][idx]
        results.append({
            'similarity': float(similarities[idx]),
            'session': vector_item['session'],
            'message_id': vector_item['message_id'],
            'message_index': vector_item['message_index'],
            'timestamp': vector_item['timestamp'],
            'content_preview': vector_item['content_preview'],
            'type': vector_item['metadata']['type']
        })

    return results
```

**Key Optimizations:**
1. **Binary Storage**: 200x faster loading than JSON
2. **Memory Mapping**: Instant loading, no RAM overhead
3. **Vectorized Operations**: Compute all similarities at once
4. **Efficient Top-K**: Use `argpartition` instead of full sort
5. **L2-Normalized Vectors**: Direct dot product for cosine similarity

**Additional Documentation:**
- Complete technical analysis: `../implementation/indexing/VECTOR_SEARCH_FINAL.md`
- Optimization status: `../implementation/indexing/VECTOR_OPTIMIZATION_STATUS.md`
- Binary storage solution: `../implementation/indexing/VECTOR_SEARCH_COMPLETION.md`

### Filtered Search

```python
def search_with_filters(project_dir, query, filters=None):
    """
    Search with time, session, or type filters
    """
    sessions_dir = project_dir / '.devsessions'
    index = json.loads((sessions_dir / 'index.json').read_text())

    query_embedding = embed_text(query)

    # Apply filters
    filtered_vectors = index['unified_vectors']

    if filters:
        # Filter by date range
        if 'start_date' in filters:
            filtered_vectors = [
                v for v in filtered_vectors
                if v['timestamp'] >= filters['start_date']
            ]

        if 'end_date' in filters:
            filtered_vectors = [
                v for v in filtered_vectors
                if v['timestamp'] <= filters['end_date']
            ]

        # Filter by session
        if 'sessions' in filters:
            filtered_vectors = [
                v for v in filtered_vectors
                if v['session'] in filters['sessions']
            ]

        # Filter by type
        if 'types' in filters:
            filtered_vectors = [
                v for v in filtered_vectors
                if v['metadata']['type'] in filters['types']
            ]

        # Filter by tags
        if 'tags' in filters:
            # Get sessions with matching tags
            matching_sessions = [
                s['session_id'] for s in index['session_manifest']
                if any(tag in s['tags'] for tag in filters['tags'])
            ]
            filtered_vectors = [
                v for v in filtered_vectors
                if v['session'] in matching_sessions
            ]

    # Search filtered vectors
    results = []
    for vector_item in filtered_vectors:
        similarity = cosine_similarity(query_embedding, vector_item['embedding'])
        results.append({
            'similarity': similarity,
            'session': vector_item['session'],
            'message_id': vector_item['message_id'],
            'content_preview': vector_item['content_preview'],
            'type': vector_item['metadata']['type'],
            'timestamp': vector_item['timestamp']
        })

    results = sorted(results, key=lambda x: x['similarity'], reverse=True)
    return results
```

### Example Searches

```python
# Search all sessions
results = search_all_sessions(project_dir, "webhook signature verification")

# Search recent sessions only (last 7 days)
results = search_with_filters(
    project_dir,
    "webhook signature",
    filters={
        'start_date': (datetime.now() - timedelta(days=7)).isoformat()
    }
)

# Search only decisions
results = search_with_filters(
    project_dir,
    "authentication strategy",
    filters={'types': ['decision']}
)

# Search by tags
results = search_with_filters(
    project_dir,
    "payment processing",
    filters={'tags': ['stripe', 'payments']}
)

# Search specific sessions
results = search_with_filters(
    project_dir,
    "error handling",
    filters={'sessions': ['session-001', 'session-003']}
)
```

---

## Loading Full Context from Old Sessions

```python
def load_full_context_from_result(project_dir, search_result):
    """
    Given a search result, load full message context from that session
    """
    sessions_dir = project_dir / '.devsessions'

    # Find session file
    session_file = sessions_dir / f"{search_result['session']}.devsession"

    if not session_file.exists():
        raise FileNotFoundError(f"Session file not found: {session_file}")

    # Load session
    session = json.loads(session_file.read_text())

    # Find the specific message
    target_message = None
    for msg in session['conversation']:
        if msg['id'] == search_result['message_id']:
            target_message = msg
            break

    if not target_message:
        raise ValueError(f"Message {search_result['message_id']} not found in session")

    # Get surrounding context (chronological range)
    message_index = target_message['index']

    # Get messages in range [index - 5, index + 5]
    context_start = max(1, message_index - 5)
    context_end = min(len(session['conversation']), message_index + 5)

    context_messages = [
        msg for msg in session['conversation']
        if context_start <= msg['index'] <= context_end
    ]

    # Check if this message links to a summary item
    summary_context = None
    if target_message.get('metadata', {}).get('summary_ref'):
        summary_ref = target_message['metadata']['summary_ref']

        # Find summary item
        summary = session.get('summary', {})
        for decision in summary.get('decisions', []):
            if decision['id'] == summary_ref:
                summary_context = {
                    'type': 'decision',
                    'summary': decision['decision'],
                    'message_range': decision.get('message_range')
                }
                break

        for problem in summary.get('problems_solved', []):
            if problem['id'] == summary_ref:
                summary_context = {
                    'type': 'problem_solved',
                    'problem': problem['problem'],
                    'solution': problem['solution'],
                    'message_range': problem.get('message_range')
                }
                break

    return {
        'message': target_message,
        'context_messages': context_messages,
        'summary_context': summary_context,
        'session_metadata': session['metadata'],
        'session_summary': session.get('summary', {}).get('overview')
    }
```

---

## Performance Optimizations

### 1. Lazy Loading

```python
def load_index_lazy(index_path):
    """
    Load index with lazy vector loading for large indexes
    """
    with open(index_path, 'r') as f:
        index_data = json.load(f)

    # Load metadata immediately
    index = {
        'format': index_data['format'],
        'version': index_data['version'],
        'total_sessions': index_data['total_sessions'],
        'total_vectors': index_data['total_vectors'],
        'session_manifest': index_data['session_manifest'],
        '_vectors_file': index_path,
        '_vectors_loaded': False,
        '_vectors': None
    }

    return index


def get_vectors(index):
    """
    Load vectors on-demand
    """
    if not index['_vectors_loaded']:
        with open(index['_vectors_file'], 'r') as f:
            full_data = json.load(f)
        index['_vectors'] = full_data['unified_vectors']
        index['_vectors_loaded'] = True

    return index['_vectors']
```

### 2. Index Compression

```python
def compress_index(index_path):
    """
    Compress index by removing content_preview from vectors
    Store previews separately for space savings
    """
    with open(index_path, 'r') as f:
        index = json.load(f)

    # Extract previews
    previews = {}
    for vector_item in index['unified_vectors']:
        vector_id = vector_item['id']
        previews[vector_id] = vector_item['content_preview']
        del vector_item['content_preview']  # Remove from index

    # Save compressed index
    compressed_path = index_path.parent / 'index.compressed.json'
    with open(compressed_path, 'w') as f:
        json.dump(index, f)

    # Save previews separately
    previews_path = index_path.parent / 'index.previews.json'
    with open(previews_path, 'w') as f:
        json.dump(previews, f)

    print(f"✓ Index compressed: {os.path.getsize(index_path)} → {os.path.getsize(compressed_path)} bytes")
```

### 3. Session-Scoped Search (Fast Path)

```python
def search_recent_sessions_only(project_dir, query, num_sessions=3, top_k=10):
    """
    Search only recent N sessions (fast path)
    """
    sessions_dir = project_dir / '.devsessions'
    index = json.loads((sessions_dir / 'index.json').read_text())

    # Get recent sessions
    recent_sessions = sorted(
        index['session_manifest'],
        key=lambda s: s['created_at'],
        reverse=True
    )[:num_sessions]

    recent_session_ids = [s['session_id'] for s in recent_sessions]

    # Get vector ranges for recent sessions
    vector_ranges = []
    for session in recent_sessions:
        vector_ranges.extend(range(
            session['vector_range']['start'],
            session['vector_range']['end'] + 1
        ))

    # Search only recent vectors
    query_embedding = embed_text(query)
    results = []

    for idx in vector_ranges:
        vector_item = index['unified_vectors'][idx]
        similarity = cosine_similarity(query_embedding, vector_item['embedding'])
        results.append({
            'similarity': similarity,
            'session': vector_item['session'],
            'message_id': vector_item['message_id'],
            'content_preview': vector_item['content_preview']
        })

    results = sorted(results, key=lambda x: x['similarity'], reverse=True)[:top_k]
    return results
```

### 4. Approximate Nearest Neighbor (Future)

For very large indexes (1000+ sessions):

```python
# Use FAISS or Annoy for approximate nearest neighbor search
# Trade slight accuracy for massive speed improvement

import faiss

def build_faiss_index(vectors, dimensions=384):
    """
    Build FAISS index for fast approximate search
    """
    # Convert to numpy array
    vectors_array = np.array([v['embedding'] for v in vectors], dtype='float32')

    # Build index
    index = faiss.IndexFlatIP(dimensions)  # Inner product (cosine with normalized vectors)

    # Normalize vectors
    faiss.normalize_L2(vectors_array)

    # Add to index
    index.add(vectors_array)

    return index


def search_with_faiss(faiss_index, query, vectors, top_k=10):
    """
    Fast search using FAISS
    """
    query_embedding = embed_text(query)
    query_array = np.array([query_embedding], dtype='float32')
    faiss.normalize_L2(query_array)

    # Search
    similarities, indices = faiss_index.search(query_array, top_k)

    # Convert to results
    results = []
    for sim, idx in zip(similarities[0], indices[0]):
        vector_item = vectors[idx]
        results.append({
            'similarity': float(sim),
            'session': vector_item['session'],
            'message_id': vector_item['message_id'],
            'content_preview': vector_item['content_preview']
        })

    return results
```

---

## Index Maintenance

### Rebuild Index

```python
def rebuild_index(sessions_dir):
    """
    Rebuild index from scratch (use if corrupted or after schema change)
    """
    print("🔧 Rebuilding index from scratch...")

    # Backup old index
    old_index = sessions_dir / 'index.json'
    if old_index.exists():
        backup_path = sessions_dir / f'index.backup.{int(time.time())}.json'
        shutil.copy(old_index, backup_path)
        print(f"  Backed up old index to {backup_path.name}")

    # Build new index
    new_index = build_unified_index(sessions_dir)

    print("✓ Index rebuilt successfully")
    return new_index
```

### Validate Index

```python
def validate_index(sessions_dir):
    """
    Validate index integrity
    """
    index_path = sessions_dir / 'index.json'

    if not index_path.exists():
        return {'valid': False, 'error': 'Index file not found'}

    try:
        index = json.loads(index_path.read_text())
    except json.JSONDecodeError as e:
        return {'valid': False, 'error': f'Invalid JSON: {e}'}

    errors = []
    warnings = []

    # Check format
    if index.get('format') != 'devsession-index':
        errors.append('Invalid format field')

    # Check session files exist
    for session_info in index['session_manifest']:
        session_file = sessions_dir / session_info['file']
        if not session_file.exists():
            warnings.append(f"Session file missing: {session_info['file']}")

    # Check vector counts match
    expected_vectors = sum(s['message_count'] for s in index['session_manifest'])
    actual_vectors = len(index['unified_vectors'])
    if expected_vectors != actual_vectors:
        errors.append(f'Vector count mismatch: expected {expected_vectors}, got {actual_vectors}')

    # Check vector ranges are contiguous
    for i, session_info in enumerate(index['session_manifest']):
        vr = session_info['vector_range']
        expected_start = sum(s['message_count'] for s in index['session_manifest'][:i])
        if vr['start'] != expected_start:
            errors.append(f"Session {session_info['session_id']}: vector range start mismatch")

    return {
        'valid': len(errors) == 0,
        'errors': errors,
        'warnings': warnings,
        'total_sessions': index['total_sessions'],
        'total_vectors': index['total_vectors']
    }
```

### Prune Old Sessions

```python
def prune_old_sessions(sessions_dir, keep_days=90):
    """
    Archive sessions older than keep_days, rebuild index
    """
    cutoff_date = datetime.now() - timedelta(days=keep_days)
    cutoff_iso = cutoff_date.isoformat()

    # Get session files
    session_files = list(sessions_dir.glob('session-*.devsession'))

    archived = []
    for session_file in session_files:
        session = json.loads(session_file.read_text())
        if session['metadata']['created_at'] < cutoff_iso:
            # Archive
            archive_dir = sessions_dir / 'archive'
            archive_dir.mkdir(exist_ok=True)

            shutil.move(session_file, archive_dir / session_file.name)
            archived.append(session_file.name)

    if archived:
        print(f"📦 Archived {len(archived)} old sessions")
        rebuild_index(sessions_dir)
    else:
        print("No sessions to archive")
```

---

## Error Handling

### Index Not Found

```python
def load_index_safe(sessions_dir):
    """
    Load index, rebuild if not found or corrupted
    """
    index_path = sessions_dir / 'index.json'

    if not index_path.exists():
        print("⚠️  Index not found, building...")
        return build_unified_index(sessions_dir)

    try:
        with open(index_path, 'r') as f:
            index = json.load(f)

        # Validate
        if index.get('format') != 'devsession-index':
            raise ValueError('Invalid index format')

        return index

    except (json.JSONDecodeError, ValueError) as e:
        print(f"⚠️  Index corrupted ({e}), rebuilding...")
        return build_unified_index(sessions_dir)
```

### Session File Missing

```python
def load_message_safe(sessions_dir, session_id, message_id):
    """
    Load message from session file, handle missing files
    """
    session_file = sessions_dir / f"{session_id}.devsession"

    if not session_file.exists():
        return {
            'error': 'session_not_found',
            'message': f'Session file {session_id}.devsession not found (may have been archived)',
            'suggestion': 'Check .devsessions/archive/ directory'
        }

    try:
        session = json.loads(session_file.read_text())

        for msg in session['conversation']:
            if msg['id'] == message_id:
                return {'message': msg}

        return {
            'error': 'message_not_found',
            'message': f'Message {message_id} not found in session {session_id}'
        }

    except Exception as e:
        return {
            'error': 'load_failed',
            'message': f'Failed to load session: {e}'
        }
```

---

## Usage Examples

### Example 1: Search All Sessions

```python
# User in session 10 asks about earlier work
query = "How did we handle webhook signatures?"

# Search unified index
results = search_all_sessions(project_dir, query, top_k=5)

# Display results
print(f"Found {len(results)} relevant messages:")
for i, result in enumerate(results):
    print(f"{i+1}. Session {result['session']} (similarity: {result['similarity']:.2f})")
    print(f"   {result['content_preview']}")
    print()

# Load full context for top result
if results:
    full_context = load_full_context_from_result(project_dir, results[0])
    print(f"Full context from {full_context['session_metadata']['session_id']}:")
    print(f"Session summary: {full_context['session_summary']}")
    print(f"\nRelevant message:")
    print(full_context['message']['content'])
```

### Example 2: Search Recent Sessions Only

```python
# Fast path: Search last 3 sessions
query = "authentication implementation"

results = search_recent_sessions_only(
    project_dir,
    query,
    num_sessions=3,
    top_k=10
)

print(f"Searched last 3 sessions, found {len(results)} matches")
```

### Example 3: Filtered Search by Type

```python
# Find all decisions about database
results = search_with_filters(
    project_dir,
    "database choice",
    filters={
        'types': ['decision']
    }
)

print("Database decisions:")
for result in results[:5]:
    full_context = load_full_context_from_result(project_dir, result)
    if full_context['summary_context']:
        print(f"- {full_context['summary_context']['summary']}")
```

### Example 4: Maintenance

```python
# Validate index
validation = validate_index(project_dir / '.devsessions')
if not validation['valid']:
    print(f"Index validation failed: {validation['errors']}")
    rebuild_index(project_dir / '.devsessions')

# Prune old sessions (archive > 90 days)
prune_old_sessions(project_dir / '.devsessions', keep_days=90)

# Rebuild index from scratch
rebuild_index(project_dir / '.devsessions')
```

---

## Integration with RecCli

### On Session Start

```python
def start_new_session(project_dir):
    """
    Start new session with access to all past sessions
    """
    # Load index (for cross-session search)
    sessions_dir = project_dir / '.devsessions'
    index = load_index_safe(sessions_dir)

    # Load project overview
    devproject = load_devproject(project_dir / '.devproject')

    # Create new session
    session = {
        'metadata': {
            'session_id': f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            'created_at': datetime.now().isoformat()
        },
        'conversation': [],
        'vector_index': index  # Available for cross-session search
    }

    return session
```

### During Session (Cross-Session Query)

```python
def handle_cross_session_query(session, query):
    """
    Handle query that might reference past sessions
    """
    # Search current session first
    current_results = search_current_session(session, query)

    # Also search all past sessions
    historical_results = search_all_sessions(
        session['project_dir'],
        query,
        top_k=5
    )

    # Combine results
    all_results = current_results + historical_results

    # Load full context for top results
    contexts = []
    for result in all_results[:3]:
        if result['session'] == session['metadata']['session_id']:
            # Current session - already have context
            contexts.append({'message': result, 'is_current': True})
        else:
            # Past session - load from file
            context = load_full_context_from_result(
                session['project_dir'],
                result
            )
            contexts.append({**context, 'is_current': False})

    return contexts
```

### On Session End

```python
def end_session(project_dir, session):
    """
    Save session and update unified index
    """
    sessions_dir = project_dir / '.devsessions'

    # Save session file
    session_file = sessions_dir / f"{session['metadata']['session_id']}.devsession"
    with open(session_file, 'w') as f:
        json.dump(session, f, indent=2)

    # Update unified index
    update_index_with_new_session(sessions_dir, session)

    print(f"✓ Session saved and indexed: {session['metadata']['session_id']}")
```

---

## Benefits of Unified Index

**1. Complete Project History**
- Search any decision from any session
- Never lose context
- AI has full project memory

**2. Manageable Files**
- Each session = separate file (~100KB-1MB)
- Clean git diffs
- Easy to archive old sessions

**3. Fast Search**
- Index loaded once
- Session files loaded on-demand
- Only load what you need

**4. Flexible Queries**
- Search all sessions
- Search recent only
- Filter by type/date/tags
- Time-range queries

**5. Resilient**
- Index can be rebuilt from sessions
- Corruption affects one session, not all
- Easy to backup and restore

**6. Scalable**
- Works for 10 sessions or 1000 sessions
- Can add FAISS for very large projects
- Can archive old sessions without losing search

---

## Migration Path

**Phase 1:** Individual sessions without index
- Simple implementation
- Get to market fast
- Sessions are isolated

**Phase 2:** Add unified index
- Backward compatible
- Rebuild index from existing sessions
- Enables cross-session intelligence

**Future:** Advanced optimizations
- FAISS for approximate search
- Compressed index format
- Distributed index for teams

---

## Summary

The unified vector index architecture provides:
- ✅ Individual session files (manageable, version-friendly)
- ✅ Cross-session search (complete project intelligence)
- ✅ On-demand loading (fast performance)
- ✅ Flexible filtering (time, type, tags)
- ✅ Easy maintenance (rebuild, validate, prune)
- ✅ Scalability (works from 10 to 1000+ sessions)

This is the foundation for true long-term AI memory across an entire project lifecycle.

Below is a drop-in patch (schema, scoring, APIs, and migration) you can paste into the doc. It extends your unified index with **first-class temporal indexing**, **provider-agnostic embeddings**, and a **tunable RRF+time** scorer while staying compatible with your current layout. 

---

# 🔧 Changes at a glance

* **New fields:** `t_start/t_end`, `t_day/t_hour`, `section`, `episode_id`, `embed_model/provider/dim/embed_ts`, `text_hash`.
* **Temporal queries:** `LAST_48H`, `BETWEEN(t1,t2)`, `AROUND(event,±Δ)` with O(log n) time range search.
* **Scoring:** Dense∪BM25 → RRF → time/section/decision boosts (tunable).
* **Adapters:** Embedding provider abstraction (OpenAI default; easy swap to bge/e5/local).
* **Compression:** optional split previews & vectors.
* **Version bump:** `version: "1.1.0"` + simple migration notes.

---

## 📚 Unified Index (v1.1.0) – schema delta

```json
{
  "format": "devsession-index",
  "version": "1.1.0",
  "created_at": "2025-11-02T00:00:00Z",
  "last_updated": "2025-11-02T00:00:00Z",

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

      "section": "billing-retry",
      "episode_id": 15,

      "t_start": "2024-10-27T14:42:00Z",
      "t_end":   "2024-10-27T14:49:59Z",
      "t_day":   "2024-10-27",
      "t_hour":  "2024-10-27T14",

      "role": "assistant",
      "kind": "decision",             // decision | code | problem | note | log | doc
      "content_preview": "I recommend a modal...",
      "text_hash": "blake3:…",

      "embedding": [ /* 1536-dim */ ],
      "embed_model": "text-embedding-3-small",
      "embed_provider": "openai",
      "embed_dim": 1536,
      "embed_ts": "2025-11-02T00:00:00Z",

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
      "created_at": "2024-10-27T14:30:45Z",
      "date": "2024-10-27",
      "vector_range": {"start": 0, "end": 186},
      "message_count": 187,
      "summary": "Built Stripe Connect integration",
      "tags": ["stripe","payments"],
      "has_decisions": true,
      "has_problems": true
    }
  ]
}
```

> **Notes**
>
> * Keep your existing fields; this adds temporal + provider metadata.
> * `t_day`/`t_hour` enable cheap partitioning; `t_start/t_end` support interval joins.

---

## 🔎 Retrieval & scoring (RRF + temporal + locality)

**Pipeline**

1. **Dense ANN** (cosine, normalized) @ *k=200*
2. **BM25** over `text + kind + filenames + paths` @ *k=200*
3. **RRF fuse**: `rrf = Σ 1/(k0 + rank_i)` (k0≈60)
4. **Boosts** (multiplicative):

```python
base = 0.6*dense + 0.4*bm25  # or use RRF as base if you prefer
recency = exp(-delta_t / tau(kind, query_intent))  # intent-aware τ
same_section = 1.2 if span.section == current_section else 1.0
near_decision = 1.15 if near_key_decision(span) else 1.0
kind_w = {"decision":1.15,"problem":1.1,"code":1.05,"doc":1.0,"log":0.95}[span.kind]
score = base * recency * same_section * near_decision * kind_w
```

**Intent-aware τ (cheap heuristic)**

```python
if intent=="error": τ = hours(8)
elif intent in ("why","decision"): τ = days(30)
else: τ = days(3)
```

---

## ⏱️ Temporal scopes API

```ts
search(q, {
  time?: { lastHours?: number } |
         { between?: [ISODate, ISODate] } |
         { around?: { event: string, window_min: number } },
  scope?: { session_id?: string, section?: string, episode_id?: number },
  k?: number
}) -> Result[]  // each with message_range for O(1) expand
```

**Index helpers**

* Keep a sorted array of `[(t_start,t_end,span_id)]` per session for fast `between/around`.
* Maintain hash `{event_id -> (t_start,t_end)}` for “AROUND(event,±Δ)”.

---

## 🧠 Embedding providers (adapter)

* **Default:** OpenAI `text-embedding-3-small` (quality/$ sweet spot).
* **Config keys:**
  `EMBED_PROVIDER=openai|local|voyage`
  `EMBED_MODEL=text-embedding-3-small|bge-m3|e5-large|…`
* **Cache rule:** re-embed **only** if `text_hash` or `{provider,model}` changes.
* **Cosine, L2-norm vectors** across all providers for consistency.

---

## 🗜️ Optional compression

* Split large index:

  * `index.meta.json` (everything minus vectors)
  * `index.vectors.bin` (float32 array) + `index.previews.json`
* Lazy-load vectors on first query; keep previews separate for UI.

---

## ✅ Acceptance tests (add to doc)

* **AROUND:** `search("error", time={around:{event:"dec_7a1e",window_min:30}})` returns logs ±30m and the decision span.
* **Intent decay:** `search("EADDRINUSE")` w/ `lastHours:48` ranks fresh logs above old ones; without time scope, exact matches still appear.
* **Locality:** `search("retry logic")` inside `billing-retry` section favors that section’s spans.

---

## 🔁 Migration (v1.0.0 → v1.1.0)

1. **Bump** `version` to `"1.1.0"`.
2. **Backfill**:

   * Set `section` from file path/topic if present; else `"default"`.
   * `t_start=t_end=timestamp` if unknown.
   * Add `t_day/t_hour` derived from `timestamp`.
   * Add `embed_*` from your current index globals.
   * Compute and store `text_hash` (e.g., blake3 of `content_preview` or full text).
3. **Do not** re-embed unless you’re changing provider/model.
4. Keep a one-time `migrations[]` note in the file.

---

## 🧪 Config & tuning (YAML)

Add a tiny config so you can tune without code changes:

```yaml
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
```

---
