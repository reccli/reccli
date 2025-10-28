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
Ready to Record:
┌─────────────────┐
│  ● REC   ⚙️     │  ← Green circle with red dot + "REC" text
└─────────────────┘

Recording:
┌─────────────────┐
│  ■ STOP  ⚙️     │  ← Red square + "STOP" text
└─────────────────┘
```

**2 buttons total:**
- **Left button:** Dynamic toggle (● REC → ■ STOP → ● REC)
  - Click when showing "REC" = Start recording
  - Click when showing "STOP" = Stop recording & show export dialog
- **Right button:** Settings gear (⚙️)

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

## Preemptive Compaction Strategy

### The Problem: Claude Code's 200K Token Limit

When using RecCli with Claude Code (or any LLM with context limits):
- Claude Code automatically compacts at ~200K tokens
- **We can't customize that compaction** (Anthropic controls it)
- Would lose our .devsession structure and vector search approach

### The Solution: Beat Them to It

**RecCli triggers compaction at 190K tokens** - before Claude Code does it automatically.

```
Token Usage Over Time:

0K ────────────────────────────────── 190K ─ 200K
                                       ↑      ↑
                                    COMPACT  CLAUDE CODE
                                    (ours)   (theirs - never reached)
```

### How It Works:

```python
class SessionMonitor:
    """Monitor token usage and trigger preemptive compaction"""

    def __init__(self):
        self.token_threshold = 190_000  # Before Claude Code's 200K
        self.current_tokens = 0
        self.compaction_triggered = False

    def on_message(self, message):
        """Called for each message in the conversation"""
        # Update token count
        self.current_tokens += count_tokens(message)

        # Check if we need to compact
        if self.current_tokens >= self.token_threshold:
            if not self.compaction_triggered:
                self.trigger_compaction()
                self.compaction_triggered = True

    def trigger_compaction(self):
        """Compact session using our custom strategy"""
        print("🔄 Session approaching context limit (190K tokens)")
        print("📦 Compacting with .devsession strategy...")

        # 1. Get full conversation
        conversation = self.get_conversation()

        # 2. Generate summary with custom prompt
        summary = generate_summary_with_custom_prompt(conversation)

        # 3. Generate embeddings for vector search
        embeddings = generate_embeddings_incrementally(conversation)

        # 4. Save complete .devsession file
        devsession_file = save_devsession(
            summary=summary,
            conversation=conversation,
            embeddings=embeddings
        )

        # 5. Compact context intelligently
        compacted_context = compact_intelligently(
            session=devsession_file,
            num_recent_messages=20
        )

        # 6. Load compacted context back into Claude Code
        self.load_compacted_context(compacted_context)

        print(f"✅ Compacted: 190K → 2K tokens")
        print(f"📄 Full session saved: {devsession_file}")
        print("💬 Continuing conversation with focused context...")


def compact_intelligently(session, num_recent_messages=20):
    """
    Compact 190K tokens → 2K tokens using .devsession strategy
    """
    # Extract recent messages as implicit goal
    recent = session.conversation[-num_recent_messages:]
    query_embedding = embed_messages(recent)

    # Search earlier conversation for relevant context
    earlier = session.conversation[:-num_recent_messages]
    relevant = vector_search(
        vectors=earlier,
        query=query_embedding,
        top_k=10
    )

    # Build compacted context
    compacted = {
        'summary': session.summary,      # ~500 tokens
        'relevant': relevant,            # ~1000 tokens
        'recent': recent                 # ~500 tokens
    }

    return format_for_llm(compacted)  # ~2000 tokens total


def load_compacted_context(compacted_context):
    """
    Load compacted context back into Claude Code

    Options:
    A. Auto-paste (if API access available)
    B. Copy to clipboard + notify user
    C. Save as "continuation prompt" file
    """

    # Option A: Direct API call (if available)
    if has_api_access():
        claude_code_api.send_message(
            "Continue from this compacted context:",
            context=compacted_context
        )

    # Option B: Copy to clipboard
    else:
        copy_to_clipboard(compacted_context)
        notify_user(
            "Context compacted! Paste into Claude Code to continue.\n"
            "Copied to clipboard - just Cmd+V"
        )
```

### The Flow in Practice:

**User's Experience:**
```
1. Recording session with Claude Code
   [Working... 50K tokens]
   [Working... 100K tokens]
   [Working... 150K tokens]
   [Working... 180K tokens]

2. RecCli notification appears:
   ┌─────────────────────────────────────┐
   │  🔄 Session Compacting              │
   ├─────────────────────────────────────┤
   │  Context approaching limit (190K)   │
   │  Compacting with .devsession...     │
   │                                     │
   │  [Progress: ████████░░] 80%        │
   │                                     │
   │  ✓ Summary generated                │
   │  ✓ Embeddings created               │
   │  ⏳ Compacting context...           │
   └─────────────────────────────────────┘

3. Compaction complete:
   ┌─────────────────────────────────────┐
   │  ✅ Session Compacted               │
   ├─────────────────────────────────────┤
   │  190K tokens → 2K tokens            │
   │                                     │
   │  Full session saved:                │
   │  ~/sessions/session_20241027.devsession │
   │                                     │
   │  Compacted context copied to        │
   │  clipboard. Paste to continue.      │
   │                                     │
   │  [ Open Session ] [ Continue ]      │
   └─────────────────────────────────────┘

4. User pastes into Claude Code:
   User: [Paste compacted context]

   Claude Code: "I see from the summary we built Stripe
   integration with req.rawBody for signatures. The recent
   messages show you're debugging a 400 error. Let me help..."

   [Session continues from ~2K tokens instead of 190K]
```

### Benefits:

**✅ Full Control**
- Our custom summarization prompt (not Anthropic's)
- Our vector search strategy (not generic compaction)
- Our .devsession format preserved

**✅ Zero Data Loss**
- Full conversation saved with embeddings
- Can always load more context from .devsession if needed
- Summary captures all key decisions/code changes

**✅ Seamless Continuation**
- Claude Code never hits its 200K limit
- Compacted context is focused and relevant
- Can compact multiple times in a long session

**✅ Cost Efficient**
- 2K tokens vs 190K tokens = 98.9% reduction
- Vector search finds only relevant context
- Pay for embeddings once, use forever

### Implementation Timeline:

**Phase 1 (MVP):** Token counting + notification
```python
# Just warn user when approaching limit
if tokens > 190_000:
    show_notification("Approaching context limit - consider saving")
```

**Phase 2:** Manual compaction trigger
```python
# User clicks "Compact Session" button
# Generates .devsession and compacted context
# Copies to clipboard
```

**Phase 3:** Automatic compaction
```python
# Automatically triggers at 190K
# Generates compacted context
# Auto-pastes if API available, otherwise copies to clipboard
```

**Phase 4:** Continuous monitoring
```python
# Real-time token display
# Multiple compaction rounds (190K → 2K → 190K → 2K)
# Session "chapters" in .devsession
```

### Key Design Decisions:

**Why 190K not 195K or 185K?**
- 190K gives 10K token buffer (safety margin)
- Compaction takes 5-10 seconds (need buffer)
- Embedding generation is async (need time)

**Why copy to clipboard vs auto-paste?**
- Claude Code API may not support programmatic input
- Clipboard is universal, always works
- User maintains control

**Can we compact multiple times?**
- Yes! Each compaction creates a new "chapter"
- Previous chapters saved in .devsession
- Can have: Chapter 1 (190K) → Chapter 2 (190K) → Chapter 3...

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
