# .devsession File Format Specification

**Version:** 1.0.0
**Status:** Draft
**Last Updated:** October 2024

## Overview

The `.devsession` format is an open standard for storing AI-assisted development sessions with **three-layer intelligent context management**. It enables:

- **Three-layer architecture** - Project overview + session summary + full context with vectors
- **Lossless preservation** of full conversation history
- **Automatic project documentation** that evolves with each session
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

`.devsession` files are JSON documents with a **three-layer architecture**:

```json
{
  "format": "devsession",
  "version": "1.0.0",
  "metadata": { },
  "project_overview": { },  // Layer 1: Project-level context (macro)
  "summary": { },           // Layer 2: Session summary (this session)
  "conversation": [ ],      // Layer 3: Full context with embeddings (micro)
  "vector_index": { },      // Vector search index
  "artifacts": { }
}
```

## Three-Layer Architecture

### Layer 1: Project Overview (Macro Context)
- High-level project information (~300-500 tokens)
- "What is this project?" - Purpose, architecture, tech stack
- Key project-level decisions across all sessions
- Session history and evolution over time
- Updated incrementally with each session
- **Provides macro perspective** - keeps LLM grounded in project goals

### Layer 2: Session Summary (This Session)
- Compact representation (~500-1000 tokens)
- "What happened today?" - Current session's work
- Decisions, code changes, problems solved
- Always provided to LLM as base context
- Generated at session end or during compaction

### Layer 3: Full Context (Micro Details)
- Complete conversation with embeddings
- "How did we do it?" - Every message, every detail
- Semantic search enabled via vector embeddings
- Only loaded when LLM needs specific details
- Small radius retrieval around current problem

## Schema Definition

### Root Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `format` | string | Yes | Must be `"devsession"` |
| `version` | string | Yes | Semantic version (e.g., `"1.0.0"`) |
| `metadata` | object | Yes | Session metadata |
| `project_overview` | object | No | Project-level context (Layer 1) - automatically maintained |
| `summary` | object | No | AI-generated session summary (Layer 2) |
| `conversation` | array | Yes | Full message history with embeddings (Layer 3) |
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

### Project Overview Object (Layer 1)

The project overview provides **macro-level context** that persists and evolves across all sessions. This is **automatically maintained** - the AI updates it incrementally with each session.

**Key Benefits:**
- 🎯 **Grounding** - Keeps AI focused on project goals, not just current task
- 📚 **Automatic Documentation** - Project documentation that writes itself
- 🔄 **Evolution Tracking** - See how project architecture and decisions evolved
- 🚀 **Onboarding** - New contributors (or you after 3 months) get instant context

```json
{
  "project_overview": {
    "last_updated": "session-003",
    "updated_at": "2024-10-27T18:30:00Z",
    "project": {
      "name": "RecCli",
      "description": "CLI terminal recorder with AI-powered session management using .devsession format",
      "purpose": "Enable developers to record, summarize, and intelligently continue terminal sessions",
      "value_proposition": "Solves AI context loss by automatically building living project documentation that eliminates the need to re-explain project context",
      "repository": "https://github.com/willluecke/RecCli",
      "license": "MIT",
      "status": "active_development"
    },
    "tech_stack": {
      "languages": ["Python"],
      "frameworks": ["tkinter"],
      "key_dependencies": ["asciinema", "anthropic"],
      "embedding_model": "text-embedding-3-small",
      "llm_model": "claude-sonnet-4.5"
    },
    "architecture": {
      "overview": "Two-component system: RecCli (recording UI) + .devsession (intelligent format)",
      "components": [
        {
          "name": "RecCli",
          "purpose": "Terminal recording with 2-button UI (REC/STOP + Settings)",
          "tech": "Python, tkinter, asciinema"
        },
        {
          "name": ".devsession Format",
          "purpose": "Three-layer context: project overview + session summary + full conversation with vectors",
          "tech": "JSON, vector embeddings, semantic search"
        }
      ],
      "key_patterns": [
        "Dual-layer UI (simple recorder + intelligent export)",
        "Preemptive compaction (190K threshold before Claude Code's 200K limit)",
        "Vector search for context retrieval (cosine similarity)",
        "Incremental embedding generation (build as you go)"
      ]
    },
    "key_decisions": [
      {
        "id": "decision_001",
        "date": "2024-10-27",
        "session": "session-001",
        "decision": "Make RecCli open source (MIT license)",
        "reasoning": "Build developer credibility, monetize other projects instead",
        "impact": "high",
        "alternatives_considered": ["Freemium model", "One-time purchase"],
        "current_status": "implemented"
      },
      {
        "id": "decision_002",
        "date": "2024-10-27",
        "session": "session-002",
        "decision": "Use three-layer .devsession format (project + summary + conversation)",
        "reasoning": "Better than compaction algorithms, maintains all context with semantic search",
        "impact": "high",
        "alternatives_considered": ["Simple markdown logs", "Use Claude Code's built-in compaction"],
        "current_status": "implemented"
      },
      {
        "id": "decision_003",
        "date": "2024-10-27",
        "session": "session-003",
        "decision": "Preemptive compaction at 190K tokens (before Claude Code's 200K limit)",
        "reasoning": "Maintain control over compaction strategy, preserve .devsession format",
        "impact": "high",
        "alternatives_considered": ["Let Claude Code handle compaction", "Manual save prompts"],
        "current_status": "designed"
      }
    ],
    "project_phases": {
      "current_phase": "Architecture & Documentation",
      "completed_phases": [
        {
          "phase": "Open Source Conversion",
          "completed": "2024-10-27",
          "sessions": ["session-001"],
          "summary": "Removed payment infrastructure, added MIT license, updated README"
        },
        {
          "phase": "Format Design",
          "completed": "2024-10-27",
          "sessions": ["session-002"],
          "summary": "Designed .devsession format with dual-layer architecture and vector embeddings"
        }
      ],
      "next_milestones": [
        {
          "milestone": "MVP - Export Dialog",
          "target": "Q4 2024",
          "description": "Add export dialog to RecCli with .devsession format support",
          "priority": "high"
        },
        {
          "milestone": "Vector Embeddings",
          "target": "Q1 2025",
          "description": "Generate embeddings during recording for semantic search",
          "priority": "medium"
        },
        {
          "milestone": "Smart Context Loading",
          "target": "Q2 2025",
          "description": "Implement vector search and intelligent context loading",
          "priority": "medium"
        }
      ]
    },
    "sessions": [
      {
        "id": "session-001",
        "date": "2024-10-27",
        "duration_hours": 1.5,
        "focus": "Open source conversion",
        "key_outcomes": ["Removed payment code", "Added MIT license", "Updated documentation"]
      },
      {
        "id": "session-002",
        "date": "2024-10-27",
        "duration_hours": 2.0,
        "focus": ".devsession format design",
        "key_outcomes": ["Designed three-layer architecture", "Created format specification", "Built example files"]
      },
      {
        "id": "session-003",
        "date": "2024-10-27",
        "duration_hours": 1.5,
        "focus": "Context loading & compaction strategy",
        "key_outcomes": ["Defined implicit goal approach", "Designed preemptive compaction", "Updated documentation"]
      }
    ],
    "statistics": {
      "total_sessions": 3,
      "total_duration_hours": 5.0,
      "files_created": 12,
      "files_modified": 8,
      "lines_of_code": 2400,
      "documentation_pages": 6
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `last_updated` | string | Session ID that last updated this overview |
| `updated_at` | string | Timestamp of last update |
| `project` | object | Basic project information |
| `tech_stack` | object | Technologies, frameworks, dependencies |
| `architecture` | object | System design, components, patterns |
| `key_decisions` | array | Project-level decisions across all sessions |
| `project_phases` | object | Current phase, completed work, next milestones |
| `sessions` | array | History of all sessions with key outcomes |
| `statistics` | object | Aggregate statistics across all sessions |

#### How Project Overview Updates:

```python
def update_project_overview(previous_session, current_session):
    """
    Automatically update project overview based on current session
    Called at the end of each session
    """
    overview = previous_session.project_overview.copy()

    # Add current session to history
    overview['sessions'].append({
        'id': current_session.id,
        'date': current_session.date,
        'duration_hours': current_session.duration / 3600,
        'focus': infer_session_focus(current_session.summary),
        'key_outcomes': extract_key_outcomes(current_session.summary)
    })

    # Extract project-level decisions (AI classifies importance)
    for decision in current_session.summary.decisions:
        if is_project_level(decision):  # High impact, affects architecture
            overview['key_decisions'].append({
                'id': f"decision_{len(overview['key_decisions']) + 1:03d}",
                'date': current_session.date,
                'session': current_session.id,
                'decision': decision.decision,
                'reasoning': decision.reasoning,
                'impact': decision.impact,
                'alternatives_considered': decision.get('alternatives', []),
                'current_status': 'implemented'
            })

    # Update tech stack if new technologies added
    new_tech = extract_new_technologies(current_session)
    if new_tech:
        overview['tech_stack']['key_dependencies'].extend(new_tech)

    # Update architecture if structural changes made
    architecture_changes = extract_architecture_changes(current_session)
    if architecture_changes:
        overview['architecture']['key_patterns'].extend(architecture_changes)

    # Update project phase based on work done
    current_phase = infer_current_phase(
        overview['sessions'],
        current_session.summary.next_steps
    )
    if current_phase != overview['project_phases']['current_phase']:
        # Phase completed, move to next
        overview['project_phases']['completed_phases'].append({
            'phase': overview['project_phases']['current_phase'],
            'completed': current_session.date,
            'sessions': get_sessions_in_phase(overview['sessions']),
            'summary': summarize_phase_work(overview['sessions'])
        })
        overview['project_phases']['current_phase'] = current_phase

    # Update statistics
    overview['statistics']['total_sessions'] += 1
    overview['statistics']['total_duration_hours'] += current_session.duration / 3600
    overview['statistics']['files_modified'] += count_files_modified(current_session)

    # Update metadata
    overview['last_updated'] = current_session.id
    overview['updated_at'] = current_session.created_at

    return overview
```

#### Benefits of Automatic Project Overview:

**📚 Self-Writing Documentation**
- No manual README updates needed
- Architecture documentation stays current
- Tech stack tracked automatically
- Decision log maintained

**🎯 Macro Perspective**
- LLM understands project purpose, not just current task
- Keeps work aligned with project goals
- Prevents scope creep

**🔄 Evolution Tracking**
- See how architecture evolved
- Understand why decisions were made
- Track project phases over time

**🚀 Onboarding**
- Return after 3 months? Full context instantly
- New team member? Complete project understanding
- Handoff? Zero knowledge loss

### Summary Object (Layer 2)

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
      "references": ["msg_045", "msg_046", "msg_047"],
      "message_range": {
        "start": "msg_042",
        "end": "msg_050",
        "start_index": 42,
        "end_index": 50
      }
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
| `references` | array[string] | Message IDs with full context (key messages) |
| `message_range` | object | **Chronological range** in full conversation where this was discussed |

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
      "references": ["msg_089", "msg_090", "msg_091"],
      "message_range": {
        "start": "msg_085",
        "end": "msg_095",
        "start_index": 85,
        "end_index": 95
      }
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
| `references` | array[string] | Message IDs with full context (key messages) |
| `message_range` | object | **Chronological range** in full conversation where this was implemented |

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
      "references": ["msg_134", "msg_135", "msg_136", "msg_137"],
      "message_range": {
        "start": "msg_130",
        "end": "msg_142",
        "start_index": 130,
        "end_index": 142
      }
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
      "references": ["msg_201"],
      "message_range": {
        "start": "msg_198",
        "end": "msg_205",
        "start_index": 198,
        "end_index": 205
      }
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
      "references": ["msg_215"],
      "message_range": {
        "start": "msg_212",
        "end": "msg_218",
        "start_index": 212,
        "end_index": 218
      }
    }
  ]
}
```

### Conversation Array (Layer 3)

Full message history with complete context **and vector embeddings** for semantic search.

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
| `index` | number | Yes | **Sequential position** in conversation (1-based) for chronological range queries |
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

### Chronological Indexing

**Why chronology matters:**

Summaries are naturally **chronological** - they tell the story of what happened in order. When the AI needs deeper context about a specific summary item, it should be able to jump to that **chronological location** in the full conversation and read the surrounding messages.

**Key principle:** Every summary item links back to its chronological position via `message_range`.

#### Chronological Linking Structure

```json
{
  "summary": {
    "decisions": [
      {
        "id": "dec_001",
        "decision": "Use Stripe Connect instead of manual splits",
        "references": ["msg_045", "msg_046", "msg_047"],  // Key messages
        "message_range": {
          "start": "msg_042",    // First message in this discussion
          "end": "msg_050",      // Last message in this discussion
          "start_index": 42,     // Array index for fast range query
          "end_index": 50        // Array index for fast range query
        }
      }
    ],
    "code_changes": [
      {
        "id": "code_001",
        "description": "Added Stripe Connect transfer logic",
        "references": ["msg_089", "msg_090", "msg_091"],
        "message_range": {
          "start": "msg_085",
          "end": "msg_095",
          "start_index": 85,
          "end_index": 95
        }
      }
    ]
  },
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
1. **Summary item** (dec_001) says "We decided to use Stripe Connect"
2. **references** points to key messages that made the decision ([45, 46, 47])
3. **message_range** shows the full chronological span of the discussion (messages 42-50)
4. **AI can expand** by reading messages[42:50] to get complete context

#### Chronological Search Algorithms

##### 1. Expand Summary Item (by ID)

```python
def expand_summary_item(devsession, summary_item_id):
    """
    Given a summary item ID, load the full chronological context
    """
    # Find summary item
    summary_item = find_summary_item(devsession.summary, summary_item_id)

    # Get chronological range
    start_idx = summary_item.message_range.start_index
    end_idx = summary_item.message_range.end_index

    # Extract messages in chronological order
    messages = devsession.conversation[start_idx-1:end_idx]  # 1-based to 0-based

    return {
        "summary": summary_item,
        "full_context": messages,
        "chronological_position": f"Messages {start_idx}-{end_idx} of {len(devsession.conversation)}"
    }
```

**Example usage:**
```python
# AI reads summary: "Decision dec_001: Use Stripe Connect"
# AI needs more context, asks for expansion
context = expand_summary_item(session, "dec_001")

# Returns:
# - summary: The decision summary
# - full_context: All 9 messages (42-50) in chronological order
# - position: "Messages 42-50 of 187"
```

##### 2. Keyword Search with Chronological Position

```python
def search_with_chronology(devsession, keyword):
    """
    Search for keyword and return results with chronological context
    """
    results = []

    for msg in devsession.conversation:
        if keyword.lower() in msg.content.lower():
            # Find which summary item(s) this message belongs to
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
#     "summary_context": ["prob_001: Webhook signature verification failing"],
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
        item_time = parse_iso(item.timestamp)
        if start_time <= item_time <= end_time:
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

##### 4. Hybrid Vector + Chronological Search

```python
def hybrid_search(devsession, query, summary_item_id=None):
    """
    Combine vector similarity with chronological context
    """
    # If summary item specified, focus search in that chronological range
    if summary_item_id:
        summary_item = find_summary_item(devsession.summary, summary_item_id)
        start_idx = summary_item.message_range.start_index
        end_idx = summary_item.message_range.end_index
        search_space = devsession.conversation[start_idx-1:end_idx]
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
# AI first finds summary item: prob_001 (messages 130-142)
# Then does vector search within that range
results = hybrid_search(session, "webhook signature", "prob_001")

# Returns messages 130-142, ranked by relevance, sorted chronologically
```

#### Benefits of Chronological Indexing

**✅ Natural narrative flow**
- Summaries are chronological stories
- Reading full context preserves timeline
- Easier to understand what happened and why

**✅ Fast range queries**
- `message_range.start_index` and `end_index` enable O(1) array slicing
- No need to scan entire conversation
- Efficient expansion of summary items

**✅ Context preservation**
- Each summary item knows its chronological position
- AI can "rewind" to that point in the conversation
- Understand what led up to a decision or problem

**✅ Keyword search with context**
- Find "webhook" → also know *when* in the session and *what* was happening
- Results include summary context for immediate understanding

**✅ Multi-layer linking**
- Layer 1 (project overview) → links to session IDs
- Layer 2 (summary) → links to message ranges
- Layer 3 (full conversation) → indexed chronologically
- All three layers connected via chronology

#### Implementation Guidelines

**When generating summaries:**
1. Process conversation chronologically
2. For each summary item, record:
   - `references`: Key messages (most important)
   - `message_range`: Full span of discussion (complete context)
3. Use `index` field for fast array access

**When loading context:**
1. **Start with summary** (chronologically organized)
2. **Expand specific items** using `message_range`
3. **Combine with vector search** for semantic relevance
4. **Preserve chronological order** in results

**Storage optimization:**
- `index` is redundant (can derive from array position) but included for clarity
- `message_range` uses both ID and index for flexibility
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
