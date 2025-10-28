# RecCli + .devsession Architecture

## Complete System Design

### Core Principle
**RecCli = Simple recording layer (UI)**
**+ .devsession = Intelligent dual-layer format (data)**
**+ Vector embeddings = Smart context retrieval (intelligence)**

---

## RecCli: The Recording Layer

### UI: 2 Buttons Only

```
┌─────────────────┐
│  ●  REC  ⚙️     │  ← Floating overlay
└─────────────────┘

● = Record/Stop toggle
⚙️ = Settings gear
```

**Click-based only** - No commands (35% adoption drop if command-based)

### Recording Flow

1. **Click Record**
   - Starts capturing terminal activity
   - Button turns red (square)
   - Duration timer starts
   - Auto-saves conversation incrementally

2. **Click Stop**
   - Stops recording
   - **Opens Export Dialog** (key UX moment)
   - Presents save options

3. **Export Dialog**
```
┌────────────────────────────────────┐
│  Save Recording                    │
├────────────────────────────────────┤
│  Session: session_20241027_143045  │
│  Duration: 2h 14m                  │
│  Messages: 187                     │
│                                    │
│  Format:                           │
│  ○ Plain Text (.txt)              │
│  ○ Markdown (.md)                 │
│  ● DevSession (.devsession)       │
│     └─ ✓ Include AI summary       │
│     └─ ✓ Generate embeddings      │
│                                    │
│  Location: ~/Documents/sessions/   │
│                                    │
│  [ Cancel ]  [ Save ]              │
└────────────────────────────────────┘
```

### Settings Gear (⚙️)
```
┌────────────────────────────────────┐
│  RecCli Settings                   │
├────────────────────────────────────┤
│  Recording:                        │
│  ☑ Auto-save every 20 messages     │
│  ☑ Capture file changes           │
│  ☑ Capture screenshots            │
│                                    │
│  .devsession:                      │
│  ☑ Generate summaries             │
│  ☑ Build vector embeddings        │
│  Model: [claude-sonnet-4.5  ▼]    │
│                                    │
│  Storage:                          │
│  Default location: [Browse...]     │
│  ☑ Compress old sessions          │
│                                    │
│  [ Close ]                         │
└────────────────────────────────────┘
```

---

## .devsession: The Dual-Layer Format

### Layer 1: Summary (Always Loaded)

**Lightweight, always in context:**
```json
{
  "summary": {
    "current_goal": "Build Stripe webhook integration",
    "decisions": [
      {
        "id": "dec_001",
        "decision": "Use req.rawBody for signature verification",
        "reasoning": "Body-parser modifies req.body",
        "vector_id": "vec_dec_001"
      }
    ],
    "code_changes": [
      {
        "id": "code_001",
        "files": ["api/webhooks.js"],
        "description": "Added signature verification",
        "vector_id": "vec_code_001"
      }
    ],
    "problems_solved": [...],
    "open_issues": [...],
    "next_steps": [...]
  }
}
```

**Summary is compact (500-1000 tokens)** - Always loaded into LLM context

### Layer 2: Full Context with Vectors

**Full conversation + vector embeddings:**
```json
{
  "conversation": [
    {
      "id": "msg_001",
      "timestamp": "2024-10-27T14:30:45Z",
      "role": "user",
      "content": "The webhook signature verification is failing",
      "embedding": [0.123, -0.456, 0.789, ...],  // 1536-dim vector
      "metadata": {
        "tokens": 12,
        "related_files": ["api/webhooks.js"]
      }
    },
    {
      "id": "msg_002",
      "role": "assistant",
      "content": "This is because body-parser consumes req.body...",
      "embedding": [0.234, -0.567, 0.890, ...],
      "metadata": {
        "tokens": 156,
        "code_block": true
      }
    }
    // ... 200+ messages with embeddings
  ],

  "vector_index": {
    "dimensions": 1536,
    "total_vectors": 187,
    "index_type": "flat_l2",  // or "hnsw" for large sessions
    "segments": {
      "decisions": ["vec_dec_001", "vec_dec_002", ...],
      "code": ["vec_code_001", "vec_code_002", ...],
      "problems": ["vec_prob_001", ...],
      "debugging": ["vec_debug_001", ...]
    }
  }
}
```

---

## Context Loading: How LLM Uses It

### When Loading a .devsession:

```python
def load_devsession_context(session_file, current_problem):
    """
    Load dual-layer context for LLM
    """
    # 1. Always load summary layer (cheap)
    summary = session.summary  # ~500 tokens

    # 2. Vector search around current problem
    problem_embedding = embed(current_problem)
    similar_messages = vector_search(
        query=problem_embedding,
        top_k=10,  # Small radius
        session=session.conversation
    )

    # 3. Get immediate context (recent messages)
    recent_context = session.conversation[-20:]  # Last 20 messages

    # 4. Combine into LLM context
    context = {
        "summary": summary,  # Full summary
        "relevant_history": similar_messages,  # Vector matches
        "recent": recent_context  # Immediate context
    }

    return context  # Total: ~2000 tokens instead of 50,000
```

### Example Usage:

```
User: "The webhook is failing again with a 400 error"

LLM receives:
1. Summary layer (500 tokens):
   - Current goal: Webhook integration
   - Decision: Use req.rawBody
   - Problem solved: Signature verification

2. Vector search results (10 messages, ~1000 tokens):
   - msg_167: "Webhook signature verification failing"
   - msg_168: "Body-parser is consuming req.body"
   - msg_169: "Use req.rawBody for verification"
   - msg_178: "Created middleware/rawBody.js"
   - (6 more related messages)

3. Recent context (20 messages, ~500 tokens):
   - Last 20 messages from current session

Total context: ~2000 tokens
Full session: 50,000 tokens saved in vector index

LLM: "I see we already solved this signature issue.
      The solution was to use req.rawBody. Let me check
      if you're applying it correctly..."
```

---

## Incremental Vector Building

### During Recording:

```python
class DevSessionRecorder:
    def __init__(self):
        self.messages = []
        self.embeddings = []
        self.summary_state = {
            "decisions": [],
            "code_changes": [],
            "problems": []
        }

    def add_message(self, role, content):
        """Add message and build vector incrementally"""
        msg = {
            "id": f"msg_{len(self.messages):03d}",
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }

        # Generate embedding (async, doesn't block UI)
        embedding = self.embed_async(content)
        msg["embedding"] = embedding

        # Detect important moments
        if self.is_decision(content):
            self.summary_state["decisions"].append({
                "id": f"dec_{len(self.summary_state['decisions']):03d}",
                "decision": self.extract_decision(content),
                "vector_id": msg["id"]
            })

        if self.is_code_change(msg):
            self.summary_state["code_changes"].append({
                "id": f"code_{len(self.summary_state['code_changes']):03d}",
                "files": self.extract_files(msg),
                "vector_id": msg["id"]
            })

        self.messages.append(msg)
        self.auto_save()  # Save incrementally

    def embed_async(self, text):
        """Generate embedding without blocking"""
        # Use local model (fast) or queue for API (slower but better)
        # Options:
        # - sentence-transformers (local, fast, free)
        # - OpenAI embeddings (API, better quality)
        # - Voyage AI embeddings (specialized for code)

        return generate_embedding(text)

    def export_devsession(self):
        """Generate final .devsession file with both layers"""
        # Generate comprehensive summary from full session
        full_summary = self.generate_final_summary(self.messages)

        devsession = {
            "format": "devsession",
            "version": "1.0.0",
            "metadata": {...},
            "summary": full_summary,  # Layer 1
            "conversation": self.messages,  # Layer 2 with embeddings
            "vector_index": self.build_vector_index()
        }

        return devsession
```

---

## File Format: Complete .devsession Structure

```json
{
  "format": "devsession",
  "version": "1.0.0",

  "metadata": {
    "session_id": "session-20241027-143045",
    "created_at": "2024-10-27T14:30:45Z",
    "duration_seconds": 8067,
    "messages_count": 187,
    "tokens_total": 52341,
    "embedding_model": "text-embedding-3-small",
    "summary_model": "claude-sonnet-4.5"
  },

  "summary": {
    "overview": "Built Stripe webhook integration...",
    "current_goal": "Complete end-to-end testing",
    "decisions": [...],
    "code_changes": [...],
    "problems_solved": [...],
    "open_issues": [...],
    "next_steps": [...]
  },

  "conversation": [
    {
      "id": "msg_001",
      "timestamp": "2024-10-27T14:30:45Z",
      "role": "user",
      "content": "Let's build the webhook integration",
      "embedding": [0.123, -0.456, ...],  // 1536 dimensions
      "metadata": {
        "tokens": 8
      }
    }
    // ... 186 more messages with embeddings
  ],

  "vector_index": {
    "embedding_model": "text-embedding-3-small",
    "dimensions": 1536,
    "total_vectors": 187,
    "index_metadata": {
      "created_at": "2024-10-27T16:45:12Z",
      "build_time_seconds": 12.4
    }
  }
}
```

---

## Implementation Phases

### Phase 1: Basic Export (Now)
```python
# Add export dialog to RecCli
def stop_recording(self):
    success, result, duration = self.recorder.stop()
    if success:
        self.show_export_dialog(result, duration)

def show_export_dialog(self, recording_path, duration):
    dialog = tk.Toplevel(self.root)
    dialog.title("Save Recording")

    # Format selection
    format_var = tk.StringVar(value="devsession")
    tk.Radiobutton(dialog, text="Plain Text (.txt)",
                   variable=format_var, value="txt")
    tk.Radiobutton(dialog, text="Markdown (.md)",
                   variable=format_var, value="md")
    tk.Radiobutton(dialog, text="DevSession (.devsession)",
                   variable=format_var, value="devsession")

    # Save button
    tk.Button(dialog, text="Save",
              command=lambda: self.export(format_var.get()))
```

### Phase 2: Vector Embeddings (Q1 2025)
```python
# Add embedding generation
def add_message_with_embedding(self, msg):
    # Generate embedding
    embedding = self.embedding_client.embed(msg.content)
    msg.embedding = embedding

    # Update vector index
    self.vector_index.add(msg.id, embedding)
```

### Phase 3: Smart Context Loading (Q2 2025)
```python
# LLM loads dual-layer context
def load_context(session_file, current_query):
    session = load_devsession(session_file)

    # Always load summary
    summary = session.summary

    # Vector search for relevant history
    query_embedding = embed(current_query)
    relevant = vector_search(session, query_embedding, top_k=10)

    return {
        "summary": summary,
        "relevant": relevant
    }
```

---

## Why This Architecture Works

### ✅ Frictionless
- 2 buttons (record/stop)
- Click-based (no commands)
- Export dialog on stop
- Zero friction = high adoption

### ✅ Intelligent
- Summary layer = always in context
- Vector search = precise retrieval
- Dual-layer = best of both worlds

### ✅ Scalable
- Incremental embedding (build as you go)
- Small summary (500 tokens)
- Large history (50K+ tokens) searchable

### ✅ Better Than Alternatives
- **vs Raw recording**: Has structure
- **vs Compaction**: Nothing lost
- **vs Big context windows**: More focused, cheaper

---

## Cost Analysis

### Storage:
- Summary: ~2 KB
- Full conversation: ~500 KB
- Embeddings (1536-dim × 200 messages): ~1.2 MB
**Total per session: ~1.7 MB** (acceptable)

### API Costs (per session):
- Embedding generation: 50K tokens × $0.0001 = **$0.005**
- Summary generation: 1 call × $0.001 = **$0.001**
**Total per session: ~$0.006** (negligible)

### Context Loading:
- Summary: 500 tokens (always)
- Vector results: 1000 tokens (on demand)
- Recent context: 500 tokens
**Total: ~2000 tokens vs 50,000 raw** (96% reduction)

---

## Next Steps

1. **Now**: Add export dialog to RecCli UI
2. **Q1 2025**: Add embedding generation (sentence-transformers)
3. **Q2 2025**: Build vector search + context loading
4. **Q3 2025**: Integrate with Claude Code API

**Start simple. Build incrementally. Stay frictionless.**
