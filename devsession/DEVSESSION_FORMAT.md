# .devsession File Format Specification

**Version:** 1.0.0
**Status:** Draft
**Last Updated:** October 2024

## Overview

The `.devsession` format is an open standard for storing AI-assisted development sessions with **dual-layer intelligent context management**. It enables:

- **Dual-layer architecture** - Lightweight summary + full context with vectors
- **Lossless preservation** of full conversation history
- **Intelligent summarization** for efficient context loading
- **Vector embeddings** for semantic search and precise retrieval
- **Multi-session synthesis** for compound context
- **Tool-agnostic design** works with any AI coding assistant

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

`.devsession` files are JSON documents with a **dual-layer architecture**:

```json
{
  "format": "devsession",
  "version": "1.0.0",
  "metadata": { },
  "summary": { },           // Layer 1: Always loaded (lightweight)
  "conversation": [ ],      // Layer 2: Full context with embeddings
  "vector_index": { },      // Vector search index
  "artifacts": { }
}
```

## Dual-Layer Architecture

### Layer 1: Summary (Always Loaded)
- Compact representation (~500-1000 tokens)
- Current goals, decisions, key changes
- Always provided to LLM as base context
- Updated/appended during compaction

### Layer 2: Full Context (On-Demand via Vector Search)
- Complete conversation with embeddings
- Semantic search enabled
- Only loaded when LLM needs specific details
- Small radius retrieval around current problem

## Schema Definition

### Root Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `format` | string | Yes | Must be `"devsession"` |
| `version` | string | Yes | Semantic version (e.g., `"1.0.0"`) |
| `metadata` | object | Yes | Session metadata |
| `summary` | object | No | AI-generated summary layer (Layer 1) |
| `conversation` | array | Yes | Full message history with embeddings (Layer 2) |
| `vector_index` | object | No | Vector search index metadata |
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

### Summary Object

The summary is AI-generated and provides efficient context for loading.

```json
{
  "summary": {
    "generated_at": "2024-10-27T16:45:12Z",
    "model": "claude-sonnet-4.5",
    "token_count": 487,
    "overview": "Built Stripe Connect integration for automated payouts...",
    "decisions": [ ],
    "code_changes": [ ],
    "problems_solved": [ ],
    "open_issues": [ ],
    "next_steps": [ ]
  }
}
```

#### Summary.decisions

Key technical decisions made during the session.

```json
{
  "decisions": [
    {
      "id": "dec_001",
      "timestamp": "2024-10-27T14:45:23Z",
      "decision": "Use Stripe Connect instead of manual splits",
      "reasoning": "Eliminates manual reconciliation and reduces errors",
      "impact": "high",
      "references": ["msg_045", "msg_046", "msg_047"]
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier for reference |
| `timestamp` | string | When decision was made |
| `decision` | string | What was decided |
| `reasoning` | string | Why this decision was made |
| `impact` | enum | `"low"`, `"medium"`, `"high"` |
| `references` | array[string] | Message IDs with full context |

#### Summary.code_changes

Code modifications made during the session.

```json
{
  "code_changes": [
    {
      "id": "code_001",
      "timestamp": "2024-10-27T15:12:34Z",
      "files": ["api/orders.js", "api/stripe.js"],
      "description": "Added Stripe Connect transfer logic",
      "type": "feature",
      "lines_added": 45,
      "lines_removed": 12,
      "references": ["msg_089", "msg_090", "msg_091"]
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier |
| `timestamp` | string | When change was made |
| `files` | array[string] | Files modified |
| `description` | string | What was changed |
| `type` | enum | `"feature"`, `"bugfix"`, `"refactor"`, `"test"`, `"docs"` |
| `lines_added` | number | Lines of code added |
| `lines_removed` | number | Lines of code removed |
| `references` | array[string] | Message IDs with full context |

#### Summary.problems_solved

Issues resolved during the session.

```json
{
  "problems_solved": [
    {
      "id": "prob_001",
      "timestamp": "2024-10-27T15:47:22Z",
      "problem": "Webhook signature verification failing",
      "solution": "Use req.rawBody instead of req.body for signature check",
      "references": ["msg_134", "msg_135", "msg_136", "msg_137"]
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
      "id": "issue_001",
      "created_at": "2024-10-27T16:30:15Z",
      "issue": "Need error handling for failed Stripe transfers",
      "severity": "high",
      "references": ["msg_201"]
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
      "id": "next_001",
      "priority": 1,
      "action": "Test end-to-end order flow with test mode",
      "estimated_time": "30 minutes",
      "references": ["msg_215"]
    }
  ]
}
```

### Conversation Array (Layer 2)

Full message history with complete context **and vector embeddings** for semantic search.

```json
{
  "conversation": [
    {
      "id": "msg_001",
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
| `id` | string | Yes | Unique message identifier |
| `timestamp` | string | Yes | ISO 8601 timestamp |
| `role` | enum | Yes | `"user"`, `"assistant"`, `"system"`, `"tool"` |
| `content` | string | Yes | Message content |
| `embedding` | array[number] | No | Vector embedding (typically 1536-dim) for semantic search |
| `tool_name` | string | Conditional | Required if `role` is `"tool"` |
| `metadata` | object | No | Additional context |

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
  version: "1.0.0",
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
  d => d.id === 'dec_001'
);

// Load full messages
const messages = decision.references.map(ref =>
  session.conversation.find(msg => msg.id === ref)
);

// Claude now has full context for that decision
```

## Implementation Guidelines

### For Tool Developers

**Creating .devsession files:**
1. Start conversation → initialize session
2. Each message → append to conversation array
3. Session end → generate summary (AI)
4. Save complete .devsession file

**Loading .devsession files:**
1. Parse JSON and validate schema
2. Load summary into AI context
3. Expand sections as needed via references
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
1. Identify relevant section ID
2. Load referenced messages from conversation
3. Provide full context to user
4. Continue with complete information

## Version History

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
