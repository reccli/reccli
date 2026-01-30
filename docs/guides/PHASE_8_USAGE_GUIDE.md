# Phase 8 Retrieval - Usage Guide

## Overview

Phase 8 adds intelligent context retrieval to RecCli's chat interface. The LLM can now search and retrieve relevant context from conversation history automatically.

---

## Quick Start

### 1. Start a Chat Session

```bash
cd /Users/will/coding-projects/reccli/v2-devsession-recorder
python3 -m reccli chat --model claude-sonnet
```

### 2. Have a Conversation

The LLM will automatically use retrieval tools when needed:

```
> What did we decide about authentication?

Claude: [Uses search_history tool to find auth decisions]
        [Uses retrieve_context to get full details]

Found authentication decision from earlier:
- Decision: Use JWT tokens with refresh mechanism
- Reasoning: Better security than sessions
- Implementation in msg_042 to msg_050
```

---

## How Retrieval Works

### Automatic Tool Selection

The LLM decides when to use retrieval based on:
1. **Query type** - Questions about past events trigger search
2. **Context gaps** - Missing information prompts retrieval
3. **Message references** - Summary mentions trigger detail retrieval

### Two Retrieval Tools

#### 1. search_history
**When used**: Broad queries about topics across the session

**Example queries that trigger it**:
- "What bugs did we fix?"
- "Show me all authentication decisions"
- "What happened with the database migration?"

**What it does**:
- Semantic search using Phase 5 vector embeddings
- Returns top-k most relevant items
- Shows category, relevance score, message range

**Output format**:
```
Found 5 results:

1. [problems_solved] Database connection timeout
   Range: msg_023 to msg_028
   Relevance: 0.87
   Preview: Fixed by adding connection pooling...

2. [decisions] Use PostgreSQL instead of MySQL
   Range: msg_042 to msg_050
   Relevance: 0.82
   Preview: Better JSON support and performance...
```

#### 2. retrieve_context
**When used**: Need full details of a specific discussion

**Example triggers**:
- Summary mentions `message_range` fields
- LLM needs exact code or conversation details
- User asks for specific message ranges

**What it does**:
- Fetches exact message ranges from conversation
- Includes context expansion (±5 messages)
- Returns full conversation text

**Output format**:
```
Retrieved 1 context ranges with 8 messages:

## Retrieved Context: Authentication decision details

Messages msg_042-msg_050:

>>> msg_042 (user): Should we use JWT or sessions?
>>> msg_043 (assistant): JWT is better for...
    msg_041 (user): [context before]
    msg_051 (assistant): [context after]
```

---

## Usage Patterns

### Pattern 1: Finding Past Decisions

**User Query**:
```
> What did we decide about error handling?
```

**LLM Behavior**:
1. Calls `search_history({"query": "error handling decision", "category": "decisions"})`
2. Gets results with `message_range` links
3. Calls `retrieve_context` to get full discussion
4. Synthesizes answer with full context

### Pattern 2: Debugging Recurring Issues

**User Query**:
```
> We had this bug before, how did we fix it?
```

**LLM Behavior**:
1. Calls `search_history({"query": "similar bug fix", "category": "problems_solved"})`
2. Retrieves relevant past fixes
3. Compares with current issue
4. Suggests solution based on past success

### Pattern 3: Code Change History

**User Query**:
```
> Show me all changes to the authentication module
```

**LLM Behavior**:
1. Calls `search_history({"query": "authentication module changes", "category": "code_changes"})`
2. Lists all relevant code changes
3. Can retrieve specific change details on request

---

## Manual Tool Invocation

While the LLM uses tools automatically, you can guide it:

### Request Specific Search

```
> Search our conversation for "database migration"
```

The LLM will recognize this as a search request and use `search_history`.

### Request Specific Retrieval

```
> Show me the full conversation from messages 42 to 50
```

The LLM will use `retrieve_context` with those specific bounds.

---

## Configuration

### Enable/Disable Retrieval

In `chat_loop()`:
```python
session.chat_loop(
    enable_compaction=True,   # Auto-compact at 190K tokens
    enable_retrieval=True      # Enable retrieval tools (Phase 8)
)
```

To disable retrieval:
```python
session.chat_loop(enable_retrieval=False)
```

### Retrieval Threshold

Retrieval is only enabled when session has >10 messages:
```python
# In _call_anthropic, _call_openai, etc.
if len(self.session.conversation) > 10:
    system_message = RETRIEVAL_SYSTEM_PROMPT + "\n\n" + base_system_message
```

This prevents tool overhead in short conversations.

---

## Advanced Features

### Context Expansion

When retrieving message ranges, the system automatically includes surrounding context:

```python
# Default: ±5 messages around the core range
retrieve_context({
    "ranges": [{"start": "msg_042", "end": "msg_050"}],
    "expand_context": 5  # Can be adjusted
})
```

**Core range** (marked with `>>>`):
- Messages explicitly in the range

**Context** (marked with spaces):
- 5 messages before and after for continuity

### Category Filtering

Search can be filtered by type:

```python
search_history({
    "query": "authentication",
    "category": "decisions"  # Only search decisions
})
```

**Available categories**:
- `decisions` - Key architectural decisions
- `code_changes` - File modifications and implementations
- `problems_solved` - Bug fixes and solutions
- `open_issues` - Known problems not yet solved
- `all` - Search everything (default)

### Max Results Control

Limit search results to control token usage:

```python
search_history({
    "query": "database",
    "max_results": 3  # Top 3 results only (default: 5)
})
```

---

## Token Usage

### Retrieval is Token-Efficient

**Without Retrieval**:
- Load entire conversation: 50K-190K tokens
- Every query pays full cost

**With Retrieval**:
- Summary: ~1K tokens (always loaded)
- Recent messages: ~1K tokens (always loaded)
- Retrieved context: ~2-5K tokens (only when needed)
- **Total: ~3-7K tokens** (94-98% savings)

### Retrieval Costs

**Per search_history call**: ~50-100 tokens
- Query processing + result formatting

**Per retrieve_context call**: ~500-3K tokens
- Depends on range size and expansion

**Typical conversation**:
- 2-3 retrieval calls per complex query
- ~1-5K tokens total for retrieval
- Still 90%+ cheaper than loading full history

---

## Troubleshooting

### "Search failed" Error

**Cause**: No vector index generated for session

**Solution**:
```bash
# Generate embeddings for session
reccli embed ~/sessions/my-session.devsession

# Or rebuild entire index
reccli index build
```

### LLM Not Using Retrieval

**Possible causes**:
1. Session has <10 messages (threshold not met)
2. Query is simple and doesn't need history
3. `enable_retrieval=False` in chat_loop

**Check**:
```python
# Verify retrieval is enabled
print(f"Conversation length: {len(session.conversation)}")
print(f"Retrieval threshold met: {len(session.conversation) > 10}")
```

### Retrieved Context is Truncated

**Cause**: Messages are limited to 500 chars each

**Location**: `_format_retrieved_context()` line 502
```python
content = msg.get("content", "")[:500]  # Truncated to 500 chars
```

**To increase**: Modify the limit (but watch token usage)

---

## Examples

### Example 1: Decision Recall

```
User: Why did we choose PostgreSQL over MySQL?