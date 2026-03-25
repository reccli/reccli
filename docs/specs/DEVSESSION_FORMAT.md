# .devsession File Format Specification

**Version:** 1.1.0
**Status:** Current design target
**Last Updated:** March 14, 2026

## Overview

The `.devsession` format is an open standard for storing AI-assisted development sessions with a **linked two-layer memory model**:

- **Full conversation** as the durable source of truth
- **Compacted session summary** as bounded working memory

The layers are linked by explicit provenance:

- **message IDs** for raw chronological events
- **span IDs** for semantic regions in the full conversation
- **summary item IDs** for compacted memory objects
- **message ranges** for exact reconstruction
- **temporal bounds** for time-based recall

It enables:

- **Lossless preservation** of full conversation history
- **Compact working memory** without losing recoverability
- **Temporal and semantic linking** between compacted items and source discussion
- **Vector retrieval** over messages, spans, and summary items
- **Optional project-level augmentation** via `.devproject`, without making it a session prerequisite
- **Tool-agnostic design** that works with any AI coding assistant

## File Extension

**`.devsession`**

Chosen for:
- ✅ Self-documenting (clear purpose)
- ✅ Unique (no naming conflicts)
- ✅ Tool-agnostic (not locked to specific software)
- ✅ Search-friendly (`*.devsession` patterns)

## MIME Type

`application/x-devsession+json`

## File Structure

`.devsession` files are JSON documents with required memory layers, a terminal recording layer, and explicit linking structures:

```json
{
  "format": "devsession",
  "version": "1.1.0",
  "metadata": { },
  "terminal_recording": { },  // PTY-based terminal capture (source for conversation parsing)
  "summary": { },             // Compacted working memory
  "conversation": [ ],        // Full chronological source of truth
  "spans": [ ],               // Semantic spans over the full conversation
  "vector_index": { },        // Vector search index
  "summary_sync": { },        // Rolling compaction frontier
  "embedding_storage": { },   // Inline vs external embedding storage
  "artifacts": { }
}
```

## Required Layers And Linking Structures

### Layer 1: Session Summary
- Compact representation (~500-1000 tokens)
- Represents the current session as decisions, code changes, problems, issues, and next steps
- Every item links back to the full conversation via `span_ids`, `references`, and `message_range`
- Best thought of as compacted working memory, not a replacement for source history

### Layer 2: Full Conversation
- Complete chronological conversation
- Every message gets a stable `msg_*` identifier
- This is the canonical, lossless record of what happened

### Linking Structure: Spans
- Semantic regions laid over the full conversation
- Each span has a stable `spn_*` identifier
- Spans link summary items to exact message ranges without making the summary own conversation identity

### Optional Project Layer
- Cross-session project context belongs in `.devproject`
- `.devsession` must remain useful without it

### Architectural Rationale

#### Why spans exist as a first-class layer

Raw messages are chronological events, not stable semantic units. Summary items are
compacted interpretations of what mattered. A first-class `spans` layer separates
those concerns:

- `msg_*` identifies what literally happened in order
- `spn_*` identifies a semantic discussion region over that raw timeline
- summary items identify compacted memory objects derived from one or more spans

Without spans, summary items point directly at raw offsets and become too tightly
coupled to positional storage.

#### Why summary IDs must be distinct from span IDs

Summary items and spans are different objects:

- one span can support multiple summary items
- one summary item can draw from multiple spans
- summary wording can change without the underlying span changing
- spans can remain stable while summaries are revised, merged, pinned, or superseded

For that reason:

- `msg_*` is the raw event identifier
- `spn_*` is the semantic span identifier
- `dec_*`, `chg_*`, `prb_*`, `iss_*`, `nxt_*` are summary item identifiers

Summary IDs should link to spans, not alias them.

#### Why vectors are accelerators, not the compacted memory itself

Vectors are excellent for:

- recall
- clustering
- candidate retrieval
- cross-session similarity

Vectors are not sufficient as the compacted memory representation because they are:

- approximate
- not human-auditable
- not lossless
- weak at preserving exact reasoning chains on their own

The lossless system is:

1. full conversation as source of truth
2. spans as semantic segmentation
3. summary items as compact working memory
4. embeddings over messages, spans, summary items, and optionally atomic facts as retrieval accelerators

For large sessions, those embeddings should usually live in sidecar storage rather
than inline JSON.

#### Recommended implementation order

To minimize drift and keep the architecture stable, implement the system in this order:

1. add first-class `spans` to `.devsession`
2. generate spans from conversation analysis before or during summarization
3. make summary items carry `span_ids` in addition to `references` and `message_range`
4. keep `message_range` as the exact reconstruction primitive
5. update retrieval to resolve `summary item -> span_ids -> message range -> conversation slice`
6. update verification to check span existence, span/range consistency, and reference containment
7. extend embeddings and indexing to cover spans and summary items
8. track the summary frontier so compaction can run incrementally instead of rebuilding from scratch

This ordering keeps the canonical source of truth in the full conversation while
adding higher-level semantic stability above it.

## Schema Definition

### Root Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `format` | string | Yes | Must be `"devsession"` |
| `version` | string | Yes | Semantic version (e.g., `"1.1.0"`) |
| `metadata` | object | Yes | Session metadata |
| `terminal_recording` | object | Yes | PTY-based terminal capture with events array |
| `conversation` | array | Yes | Full message history in chronological order |
| `summary` | object | No | AI-generated compacted session memory |
| `spans` | array | No | Semantic spans over the conversation |
| `vector_index` | object | No | Vector search index metadata |
| `summary_sync` | object | No | Rolling compaction frontier metadata |
| `embedding_storage` | object | No | Embedding storage mode and sidecar metadata |
| `artifacts` | object | No | Additional files/resources |

### Metadata Object

```json
{
  "metadata": {
    "session_id": "session-20241027-143045",
    "created_at": "2024-10-27T14:30:45Z",
    "updated_at": "2024-10-27T16:45:12Z",
    "duration_seconds": 8067,
    "tool": {
      "name": "claude-code",
      "version": "1.2.0"
    },
    "project": {
      "name": "RecCli",
      "path": "/Users/will/projects/RecCli",
      "git_repo": "https://github.com/willluecke/RecCli",
      "git_branch": "main",
      "git_commit": "f2303a7"
    },
    "user": {
      "id": "user-123",
      "name": "Will Luecke"
    },
    "tags": ["stripe-integration", "debugging", "api"],
    "related_sessions": ["session-001.devsession", "session-002.devsession"]
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | string | Yes | Unique session identifier |
| `created_at` | string (ISO 8601) | Yes | Session start timestamp |
| `updated_at` | string (ISO 8601) | No | Last modification timestamp |
| `duration_seconds` | number | No | Total session duration |
| `tool` | object | No | AI tool information |
| `project` | object | No | Project context |
| `user` | object | No | User information |
| `tags` | array[string] | No | Searchable tags |
| `related_sessions` | array[string] | No | Links to other sessions |

### Project Context

Project-level memory is intentionally **not required** inside `.devsession`.

If a product wants cross-session project context, it should store that in an optional
`.devproject` companion file and load it alongside one or more `.devsession` files.

The session file remains focused on:

- the terminal recording as the raw capture source,
- the full conversation parsed from that recording,
- the compacted session summary,
- and the linking structures required to move between them safely.

### Terminal Recording Object

The terminal recording is the raw PTY capture layer. It is the source from which conversation messages are parsed and is required for every `.devsession` file. Without it, the session cannot be replayed, re-parsed, or used to update the `.devproject` feature map.

```json
{
  "terminal_recording": {
    "version": 2,
    "width": 80,
    "height": 24,
    "shell": "/bin/zsh",
    "events": [
      [0.0, "o", "$ "],
      [0.5, "i", "git status\r"],
      [0.8, "o", "On branch main\r\n"],
      [1.2, "r", "120x40"]
    ]
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `version` | number | Yes | Recording format version (currently `2`) |
| `width` | number | Yes | Initial terminal width in columns |
| `height` | number | Yes | Initial terminal height in rows |
| `shell` | string | No | Shell used for the recording |
| `events` | array | Yes | Chronological event tuples |

Each event is a 3-element array: `[timestamp, type, data]`

| Element | Type | Description |
|---------|------|-------------|
| `timestamp` | number | Seconds since recording started |
| `type` | enum | `"o"` (output), `"i"` (input), `"r"` (resize) |
| `data` | string | Terminal text or resize dimensions |

The events array is append-only during recording. Events may be empty for sessions created via `save_session_notes` where no PTY capture occurred, but the `terminal_recording` object and its `events` array must still be present.

### Summary Object

The summary is AI-generated and provides efficient context for loading.

```json
{
  "summary": {
    "schema_version": "1.1",
    "generated_at": "2024-10-27T16:45:12Z",
    "model": "claude-sonnet-4.5",
    "token_count": 487,
    "overview": "Built Stripe Connect integration for automated payouts...",
    "decisions": [],
    "code_changes": [],
    "problems_solved": [],
    "open_issues": [],
    "next_steps": [],
    "causal_edges": [],
    "audit_trail": []
  }
}
```

#### Summary.decisions

Key technical decisions made during the session.

```json
{
  "decisions": [
    {
      "id": "dec_7a1e3f4c",
      "decision": "Use Stripe Connect instead of manual splits",
      "reasoning": "Eliminates manual reconciliation and reduces errors",
      "impact": "high",
      "span_ids": ["spn_014"],
      "references": ["msg_045", "msg_046", "msg_047"],
      "message_range": {
        "start": "msg_042",
        "end": "msg_050",
        "start_index": 41,
        "end_index": 50
      },
      "t_first": "2024-10-27T14:43:01Z",
      "t_last": "2024-10-27T14:45:23Z",
      "confidence": "high"
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Stable summary item identifier, distinct from message and span IDs |
| `decision` | string | What was decided |
| `reasoning` | string | Why this decision was made |
| `impact` | enum | `"low"`, `"medium"`, `"high"` |
| `span_ids` | array[string] | Semantic source spans in the full conversation |
| `references` | array[string] | Message IDs with full context (key messages) |
| `message_range` | object | Exact chronological span in full conversation |
| `t_first` / `t_last` | string | Temporal bounds derived from the source range |
| `confidence` | enum | `"low"`, `"medium"`, `"high"` |

#### Summary.code_changes

Code modifications made during the session.

```json
{
  "code_changes": [
    {
      "id": "chg_c4a31b20",
      "files": ["api/orders.js", "api/stripe.js"],
      "description": "Added Stripe Connect transfer logic",
      "type": "feature",
      "lines_added": 45,
      "lines_removed": 12,
      "source_of_truth": "file_events",
      "span_ids": ["spn_021", "spn_022"],
      "references": ["msg_089", "msg_090", "msg_091"],
      "message_range": {
        "start": "msg_085",
        "end": "msg_095",
        "start_index": 84,
        "end_index": 95
      },
      "t_first": "2024-10-27T15:10:00Z",
      "t_last": "2024-10-27T15:12:34Z",
      "confidence": "high"
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Stable summary item identifier |
| `files` | array[string] | Files modified |
| `description` | string | What was changed |
| `type` | enum | `"feature"`, `"bugfix"`, `"refactor"`, `"test"`, `"docs"` |
| `lines_added` | number | Lines of code added |
| `lines_removed` | number | Lines of code removed |
| `source_of_truth` | enum | `"git"`, `"file_events"`, `"llm_inferred"` |
| `span_ids` | array[string] | Semantic source spans |
| `references` | array[string] | Message IDs with full context (key messages) |
| `message_range` | object | Exact chronological range in full conversation |

#### Summary.problems_solved

Issues resolved during the session.

```json
{
  "problems_solved": [
    {
      "id": "prb_52a8e0af",
      "problem": "Webhook signature verification failing",
      "solution": "Use req.rawBody instead of req.body for signature check",
      "span_ids": ["spn_035"],
      "references": ["msg_134", "msg_135", "msg_136", "msg_137"],
      "message_range": {
        "start": "msg_130",
        "end": "msg_142",
        "start_index": 129,
        "end_index": 142
      },
      "t_first": "2024-10-27T15:41:18Z",
      "t_last": "2024-10-27T15:47:22Z"
    }
  ]
}
```

#### Summary.open_issues

Unresolved problems or future work.

```json
{
  "open_issues": [
    {
      "id": "iss_e10d4a21",
      "issue": "Need error handling for failed Stripe transfers",
      "severity": "high",
      "span_ids": ["spn_041"],
      "references": ["msg_201"],
      "message_range": {
        "start": "msg_198",
        "end": "msg_205",
        "start_index": 197,
        "end_index": 205
      },
      "t_first": "2024-10-27T16:28:03Z",
      "t_last": "2024-10-27T16:30:15Z"
    }
  ]
}
```

#### Summary.next_steps

Planned next actions.

```json
{
  "next_steps": [
    {
      "id": "nxt_4d16d4bf",
      "priority": 1,
      "action": "Test end-to-end order flow with test mode",
      "estimated_time": "30 minutes",
      "span_ids": ["spn_044"],
      "references": ["msg_215"],
      "message_range": {
        "start": "msg_212",
        "end": "msg_218",
        "start_index": 211,
        "end_index": 218
      },
      "t_first": "2024-10-27T16:41:10Z",
      "t_last": "2024-10-27T16:43:00Z"
    }
  ]
}
```

### Spans Array

Spans are first-class semantic regions laid over the full conversation. They are the
missing middle layer between raw messages and compacted summary items.

```json
{
  "spans": [
    {
      "id": "spn_014",
      "kind": "decision_discussion",
      "status": "closed",
      "topic": "Choosing Stripe Connect for payouts",
      "start_message_id": "msg_042",
      "end_message_id": "msg_050",
      "start_index": 41,
      "end_index": 50,
      "message_ids": ["msg_042", "msg_043", "msg_044", "msg_045", "msg_046", "msg_047", "msg_048", "msg_049", "msg_050"],
      "t_first": "2024-10-27T14:43:01Z",
      "t_last": "2024-10-27T14:45:23Z",
      "references": ["msg_045", "msg_046", "msg_047"],
      "episode_id": "ep_003",
      "parent_span_ids": []
    },
    {
      "id": "spn_active_tail",
      "kind": "active_context",
      "status": "open",
      "topic": "Continuing transfer retry debugging",
      "start_message_id": "msg_206",
      "end_message_id": null,
      "start_index": 205,
      "end_index": null,
      "message_ids": ["msg_206", "msg_207", "msg_208", "msg_209", "msg_210", "msg_211", "msg_212", "msg_213", "msg_214"],
      "latest_message_id": "msg_214",
      "latest_index": 213
    }
  ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Stable semantic span identifier (`spn_*`) |
| `kind` | string | Yes | Span category such as `decision_discussion`, `code_change_discussion`, `problem_solving` |
| `status` | string | No | `"closed"` for fully compacted spans, `"open"` for active tails still being worked |
| `topic` | string | No | Short semantic label |
| `start_message_id` | string | Yes | First message in the span |
| `end_message_id` | string | No | Last message in a closed span; omitted or null for open spans |
| `start_index` | number | Yes | Inclusive start index |
| `end_index` | number | No | Exclusive end index for closed spans |
| `message_ids` | array[string] | No | Explicit span membership for precise overlap-aware semantics |
| `latest_message_id` / `latest_index` | string / number | No | Current observed tail for an open span |
| `t_first` / `t_last` | string | No | Temporal bounds |
| `references` | array[string] | No | Key evidence messages inside the span |
| `episode_id` | string | No | Higher-order grouping if episodes are used |
| `parent_span_ids` | array[string] | No | Optional lineage for nested or merged spans |

Open spans are useful for ongoing conversations that should not yet be compacted
into final summary items. The rolling compactor can leave a live tail as an open
span while advancing `summary_sync` only through the last closed span.

`message_ids` is important once spans are allowed to overlap. The bounding range
answers "roughly where is this span?", while `message_ids` answers "which messages
actually belong to it?" Tooling should treat `message_ids` as the precise
membership list and `start_index` / `end_index` as the fast locator.

### Conversation Array

Full message history with complete context. This layer is chronological, not semantic:
messages get `msg_*` identifiers, but they do not themselves carry section identity.
Semantic grouping is represented in `spans`.

```json
{
  "conversation": [
    {
      "id": "msg_001",
      "index": 1,
      "timestamp": "2024-10-27T14:30:45Z",
      "role": "user",
      "content": "Let's build the Stripe integration",
      "embedding": [0.123, -0.456, 0.789, ...],  // 1536-dim vector
      "metadata": {
        "terminal_visible": true,
        "files_open": ["api/orders.js"],
        "tokens": 8
      }
    },
    {
      "id": "msg_002",
      "index": 2,
      "timestamp": "2024-10-27T14:30:52Z",
      "role": "assistant",
      "content": "I'll help you set up Stripe Connect...",
      "embedding": [0.234, -0.567, 0.890, ...],  // 1536-dim vector
      "metadata": {
        "model": "claude-sonnet-4.5",
        "tokens": 234
      }
    },
    {
      "id": "msg_003",
      "index": 3,
      "timestamp": "2024-10-27T14:31:15Z",
      "role": "tool",
      "tool_name": "write_file",
      "content": "Created file: api/stripe.js",
      "embedding": [0.345, -0.678, 0.901, ...],  // 1536-dim vector
      "metadata": {
        "file_path": "api/stripe.js",
        "operation": "create",
        "tokens": 12
      }
    }
  ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique message identifier (e.g., `msg_001`) |
| `index` | number | Yes | Sequential position in conversation (1-based) |
| `timestamp` | string | Yes | ISO 8601 timestamp |
| `role` | enum | Yes | `"user"`, `"assistant"`, `"system"`, `"tool"` |
| `content` | string | Yes | Message content |
| `embedding` | array[number] | No | Vector embedding (typically 1536-dim) for semantic search |
| `tool_name` | string | Conditional | Required if `role` is `"tool"` |
| `metadata` | object | No | Additional context |

#### Conversation Mutation Rules

The conversation array is append-only once persisted.

- New messages append at the end.
- Message IDs remain stable forever.
- Hard deletion is discouraged because it invalidates span and summary ranges.
- Sensitive content should be handled by redaction-in-place or tombstoning, not by removing an array element.

Recommended tombstone shape:

```json
{
  "id": "msg_043",
  "content": "[TOMBSTONED]",
  "deleted": true,
  "deleted_at": "2026-03-14T11:32:00Z",
  "delete_reason": "api_key_redaction",
  "content_hash_before_delete": "7d38..."
}
```

This preserves array length, message identity, and the integrity of downstream
`span_ids`, `references`, and `message_range` links.

### Summary Sync Object

`summary_sync` tracks the compaction frontier for rolling summary updates.

```json
{
  "summary_sync": {
    "status": "pending",
    "last_synced_msg_id": "msg_142",
    "last_synced_msg_index": 141,
    "updated_at": "2026-03-14T11:41:22Z",
    "pending_messages": 8,
    "pending_tokens": 1342
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `status` | string | No | `"not_started"`, `"pending"`, or `"synced"` |
| `last_synced_msg_id` | string | No | Last message fully represented in summary/span form |
| `last_synced_msg_index` | number | No | 0-based index of that message |
| `updated_at` | string | No | Last time the frontier was refreshed |
| `pending_messages` | number | No | Count of non-deleted messages beyond the frontier |
| `pending_tokens` | number | No | Approximate token count of uncompacted, non-deleted messages beyond the frontier |

This enables incremental compaction. Long sessions should update the existing
summary against newly-added spans rather than regenerate the entire summary from
scratch on every pass. Production implementations should generally trigger rolling
compaction from both:

- total conversation pressure against the model context limit
- uncompacted frontier growth (`pending_messages` / `pending_tokens`)

### Embedding Storage Object

Embeddings are optional and may be stored either inline or in a sidecar file.

```json
{
  "embedding_storage": {
    "mode": "external",
    "messages_file": "session.embeddings.npy",
    "format": "npy",
    "external_message_count": 187,
    "loaded": false
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `mode` | string | No | `"inline"` or `"external"` |
| `messages_file` | string | No | Relative or absolute sidecar path for message embeddings |
| `format` | string | No | Storage format such as `npy` |
| `external_message_count` | number | No | Number of messages externalized |
| `loaded` | boolean | No | Whether external embeddings are currently hydrated in memory |

Production guidance:

- Inline embeddings are acceptable for prototyping and small sessions.
- External sidecars are preferred for large sessions to avoid JSON bloat and slow parses.
- Vector data remains an acceleration structure, not the canonical memory representation.

### Vector Index Object

Metadata for vector search capabilities. Enables semantic retrieval of relevant messages.

```json
{
  "vector_index": {
    "embedding_model": "text-embedding-3-small",
    "dimensions": 1536,
    "total_vectors": 187,
    "distance_metric": "cosine",
    "index_metadata": {
      "created_at": "2024-10-27T16:45:12Z",
      "build_time_seconds": 12.4,
      "index_type": "flat"
    },
    "segments": {
      "decisions": ["msg_045", "msg_046", "msg_167"],
      "code_changes": ["msg_063", "msg_098", "msg_178"],
      "problems": ["msg_167", "msg_122"],
      "debugging": ["msg_168", "msg_169", "msg_170"]
    }
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `embedding_model` | string | Yes | Model used for generating embeddings |
| `dimensions` | number | Yes | Vector dimensionality (typically 1536) |
| `total_vectors` | number | Yes | Number of embedded messages |
| `distance_metric` | enum | No | `"cosine"`, `"euclidean"`, `"dot_product"` (default: cosine) |
| `index_metadata` | object | No | Build information |
| `segments` | object | No | Categorized message IDs for faster filtering |

**Usage for Context Loading:**
```python
# Load summary + vector search around current problem
def load_context(devsession, current_query):
    # Always load summary (Layer 1)
    summary = devsession.summary

    # Vector search for relevant history (Layer 2)
    query_embedding = embed(current_query)
    relevant_messages = vector_search(
        devsession.conversation,
        query_embedding,
        top_k=10  # Small radius
    )

    return {
        "summary": summary,  # ~500 tokens
        "relevant": relevant_messages  # ~1000 tokens
    }
```

### Temporal And Semantic Linking

**Why both are needed:**

- chronology preserves the exact order of what happened
- spans preserve semantic grouping
- summary items preserve compact meaning

**Key principle:** Summary items should carry three kinds of links:

1. `span_ids` for semantic identity
2. `references` for key evidence messages
3. `message_range` for exact reconstruction

#### Linking Structure

```json
{
  "summary": {
    "decisions": [
      {
        "id": "dec_7a1e3f4c",
        "decision": "Use Stripe Connect instead of manual splits",
        "span_ids": ["spn_014"],
        "references": ["msg_045", "msg_046", "msg_047"],
        "message_range": {
          "start": "msg_042",
          "end": "msg_050",
          "start_index": 41,
          "end_index": 50
        }
      }
    ]
  },
  "spans": [
    {
      "id": "spn_014",
      "kind": "decision_discussion",
      "start_message_id": "msg_042",
      "end_message_id": "msg_050",
      "start_index": 41,
      "end_index": 50
    }
  ],
  "conversation": [
    {"id": "msg_001", "index": 1, "content": "...", "timestamp": "..."},
    {"id": "msg_002", "index": 2, "content": "...", "timestamp": "..."},
    // ...
    {"id": "msg_042", "index": 42, "content": "Should we use Stripe Connect?", "timestamp": "..."},
    {"id": "msg_043", "index": 43, "content": "Let's evaluate the options...", "timestamp": "..."},
    {"id": "msg_045", "index": 45, "content": "I recommend Stripe Connect because...", "timestamp": "..."},
    {"id": "msg_046", "index": 46, "content": "That makes sense, let's do it", "timestamp": "..."},
    {"id": "msg_050", "index": 50, "content": "Great, moving on to implementation", "timestamp": "..."}
    // ...
  ]
}
```

**How it works:**
1. **Summary item** says what matters in compact form
2. **span_ids** point to the semantic discussion unit
3. **references** point to the strongest evidence inside that unit
4. **message_range** provides exact source reconstruction from the raw conversation
5. **AI can expand** by reading `conversation[start_index:end_index]`

#### Chronological Search Algorithms

##### 1. Expand Summary Item (by ID)

```python
def expand_summary_item(devsession, summary_item_id):
    """
    Given a summary item ID, load the full chronological context
    """
    # Find summary item
    summary_item = find_summary_item(devsession.summary, summary_item_id)

    # Prefer semantic span lookup first
    span = find_span(devsession.spans, summary_item.span_ids[0])
    start_idx = span.start_index
    end_idx = span.end_index

    # Extract messages in chronological order
    messages = devsession.conversation[start_idx:end_idx]

    return {
        "summary": summary_item,
        "full_context": messages,
        "chronological_position": f"Messages {start_idx}-{end_idx} of {len(devsession.conversation)}"
    }
```

**Example usage:**
```python
# AI reads summary: "Decision dec_7a1e3f4c: Use Stripe Connect"
# AI needs more context, asks for expansion
context = expand_summary_item(session, "dec_7a1e3f4c")

# Returns:
# - summary: The decision summary
# - full_context: All 9 messages (42-50) in chronological order
# - position: "Messages 42-50 of 187"
```

##### 2. Search With Summary Context

```python
def search_with_chronology(devsession, keyword):
    """
    Search for keyword and return results with chronological context
    """
    results = []

    for msg in devsession.conversation:
        if keyword.lower() in msg.content.lower():
            # Find which span(s) and summary item(s) this message belongs to
            summary_items = find_summary_items_for_message(
                devsession.summary,
                msg.index
            )

            results.append({
                "message": msg,
                "index": msg.index,
                "timestamp": msg.timestamp,
                "summary_context": summary_items,  # What was happening here?
                "chronological_position": f"{msg.index}/{len(devsession.conversation)}"
            })

    return results
```

**Example usage:**
```python
# Search for "webhook"
results = search_with_chronology(session, "webhook")

# Returns:
# [
#   {
#     "message": {"id": "msg_134", "content": "The webhook signature is failing..."},
#     "index": 134,
#     "summary_context": ["prb_52a8e0af: Webhook signature verification failing"],
#     "chronological_position": "134/187"
#   },
#   ...
# ]
```

##### 3. Time-Based Range Query

```python
def query_time_range(devsession, start_time, end_time):
    """
    Get all messages and summary items in a time range
    """
    # Filter messages by timestamp
    messages = [
        msg for msg in devsession.conversation
        if start_time <= parse_iso(msg.timestamp) <= end_time
    ]

    # Find summary items that overlap this range
    summary_items = []
    for item in all_summary_items(devsession.summary):
        if start_time <= item.t_last and end_time >= item.t_first:
            summary_items.append(item)

    return {
        "messages": messages,
        "summary_items": summary_items,
        "message_indices": [msg.index for msg in messages]
    }
```

**Example usage:**
```python
# "What happened between 3pm and 4pm?"
results = query_time_range(
    session,
    "2024-10-27T15:00:00Z",
    "2024-10-27T16:00:00Z"
)

# Returns:
# - messages: All messages in that hour
# - summary_items: Decisions, code changes, problems in that hour
# - message_indices: [85, 86, 87, ..., 142]
```

##### 4. Hybrid Vector + Span Search

```python
def hybrid_search(devsession, query, summary_item_id=None):
    """
    Combine vector similarity with chronological context
    """
    # If summary item specified, focus search in that chronological range
    if summary_item_id:
        summary_item = find_summary_item(devsession.summary, summary_item_id)
        span = find_span(devsession.spans, summary_item.span_ids[0])
        search_space = devsession.conversation[span.start_index:span.end_index]
    else:
        search_space = devsession.conversation

    # Vector search within chronological range
    query_embedding = embed(query)
    results = vector_search(search_space, query_embedding, top_k=10)

    # Sort by chronological order (preserve timeline)
    results_chronological = sorted(results, key=lambda m: m.index)

    return results_chronological
```

**Example usage:**
```python
# "Show me where we discussed webhook signatures"
# AI first finds summary item: prb_52a8e0af (messages 130-142)
# Then does vector search within that range
results = hybrid_search(session, "webhook signature", "prb_52a8e0af")

# Returns messages 130-142, ranked by relevance, sorted chronologically
```

#### Benefits Of The True Linking Model

**✅ Natural narrative flow**
- Summaries are chronological stories
- Reading full context preserves timeline
- Easier to understand what happened and why

**✅ Exact reconstruction**
- `message_range.start_index` and `end_index` enable O(1) array slicing
- No need to scan the entire conversation
- Efficient expansion of summary items

**✅ Stable semantic identity**
- span IDs can stay stable even if summary wording changes
- one message can belong to multiple spans
- one summary item can draw from multiple spans

**✅ Better retrieval composition**
- vector search can run over messages, spans, or summary items
- exact recovery still comes from the raw conversation
- semantic grouping does not require chunking the canonical source

**✅ Clear layer separation**
- summary items do not own conversation identity
- spans do not replace raw messages
- the source of truth remains the full conversation

#### Implementation Guidelines

**When generating summaries:**
1. Process conversation chronologically
2. Detect or synthesize semantic spans
3. For each summary item, record:
   - `span_ids`: Semantic source spans
   - `references`: Key messages (most important)
   - `message_range`: Full span of discussion (complete context)
4. Use `message_range` as the exact fallback locator

**When loading context:**
1. **Start with summary** (chronologically organized)
2. **Resolve span IDs** to locate semantic source regions
3. **Expand specific items** using `message_range`
4. **Combine with vector search** for semantic relevance
5. **Preserve chronological order** in results

**Storage optimization:**
- `index` is redundant (can derive from array position) but included for clarity
- `message_range` uses both ID and index for flexibility
- `span_ids` provide semantic identity above raw ranges
- Most tools will use `start_index`/`end_index` for performance

### Artifacts Object

Optional additional files or resources referenced in the session.

```json
{
  "artifacts": {
    "files": {
      "api/stripe.js": {
        "content": "const stripe = require('stripe')...",
        "encoding": "utf-8"
      }
    },
    "screenshots": {
      "error_screenshot.png": {
        "data": "base64_encoded_data",
        "encoding": "base64",
        "mime_type": "image/png"
      }
    }
  }
}
```

## Usage Examples

### Creating a Session

```javascript
const session = {
  format: "devsession",
  version: "1.1.0",
  metadata: {
    session_id: "session-" + Date.now(),
    created_at: new Date().toISOString(),
    tool: {
      name: "claude-code",
      version: "1.2.0"
    }
  },
  conversation: []
};

// Save to file
fs.writeFileSync(
  'session-001.devsession',
  JSON.stringify(session, null, 2)
);
```

### Loading a Session

```javascript
const session = JSON.parse(
  fs.readFileSync('session-001.devsession', 'utf-8')
);

// Validate format
if (session.format !== 'devsession') {
  throw new Error('Invalid devsession file');
}

// Load summary for context
const summary = session.summary;
console.log(`Loading session: ${summary.overview}`);
```

### Expanding a Section

```javascript
// Find references from summary
const decision = session.summary.decisions.find(
  d => d.id === 'dec_7a1e3f4c'
);

// Resolve the linked span, then expand the exact range
const span = session.spans.find(
  s => decision.span_ids.includes(s.id)
);
const messages = session.conversation.slice(span.start_index, span.end_index);

// Claude now has full context for that decision
```

## Implementation Guidelines

### For Tool Developers

**Creating .devsession files:**
1. Start conversation → initialize session
2. Each message → append to conversation array
3. Detect spans over the conversation
4. Session end or compaction → generate summary items linked to spans
5. Save complete `.devsession` file

**Loading .devsession files:**
1. Parse JSON and validate schema
2. Load summary into AI context
3. Resolve `span_ids` / `message_range` to expand sections as needed
4. Keep full conversation for search/reference

### For AI Models

**Summary generation prompt:**
```
Analyze this coding session and extract:
1. Key technical decisions with reasoning
2. Code changes with file names and descriptions
3. Problems solved with solutions
4. Open issues that need attention
5. Next steps with priorities

Be concise but complete. Future sessions will use this summary as context.
```

**Section expansion:**
When summary doesn't have enough detail:
1. Identify the relevant summary item
2. Resolve `span_ids` to a source span
3. Expand the linked `message_range` from conversation
4. Provide full context to user
5. Continue with complete information

## Version History

### 1.1.0 (Current design target - March 2026)
- Two required layers: summary + full conversation
- Added first-class `spans` as semantic linking objects
- Clarified that `.devproject` is optional and external to `.devsession`
- Summary items now carry `span_ids` plus `references` and `message_range`
- Canonicalized exact range semantics around `[start_index, end_index)`

### 1.0.0 (Draft - October 2024)
- Initial specification
- Core structure defined
- Summary layer design
- Reference system

## Future Considerations

### Version 2.0 Ideas
- Binary format for large sessions
- Compression options
- Streaming support
- Encryption for sensitive sessions
- Differential updates (session continues)
- Cross-session search index

### Potential Extensions
- Video/audio attachments
- Real-time collaboration metadata
- Approval workflows
- Cost tracking (API usage)
- Performance metrics

## License

This specification is released under CC0 1.0 Universal (Public Domain).

Tools may implement this format freely without restriction.

## Contributing

To propose changes to this specification:
1. Open an issue at https://github.com/willluecke/RecCli/issues
2. Discuss the change with community
3. Submit PR with specification updates
4. Version bump according to semantic versioning

## References

- **RecCli**: Reference implementation at https://github.com/willluecke/RecCli
- **JSON Schema**: Validation schema in `/schemas/devsession.schema.json`
- **Examples**: Sample files in `/examples/`

---

**Specification maintained by the RecCli project and community.**
