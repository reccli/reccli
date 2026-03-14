# RecCli + .devsession Architecture

**Status:** Mixed architecture document.

The implemented core in the current repo is strongest at the `.devsession`, summary, indexing, retrieval, compaction, checkpoint, and episode layers. Sections that describe `.devproject` as a live project-layer dependency should be read as intended architecture, not fully wired mainline behavior today.

## Complete System Design

### Core Principle
**RecCli = Simple recording layer (UI)**
**+ .devsession = Intelligent three-layer format (data)**
**+ Vector embeddings = Smart context retrieval (intelligence)**

**Three-Layer Context:**
- **Layer 1:** Project Overview (macro - what is this project?)
- **Layer 2:** Session Summary (this session - what happened today?)
- **Layer 3:** Full Conversation (micro - how did we do it?)

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

## .devsession: The Three-Layer Format

### Layer 1: Project Overview (Macro Context)

**Self-writing documentation - updated each session:**
```json
{
  "project_overview": {
    "project": {
      "name": "RecCli",
      "description": "CLI terminal recorder with AI-powered session management",
      "purpose": "Enable developers to record, summarize, and continue sessions intelligently"
    },
    "architecture": {
      "overview": "RecCli (recording UI) + .devsession (intelligent format)",
      "components": [
        {"name": "RecCli", "purpose": "2-button UI for terminal recording"},
        {"name": ".devsession", "purpose": "Three-layer context management"}
      ]
    },
    "key_decisions": [
      {
        "decision": "Open source (MIT license)",
        "reasoning": "Build credibility, monetize other projects",
        "session": "session-001"
      },
      {
        "decision": "Three-layer format with preemptive compaction",
        "reasoning": "Better than compaction algorithms, maintain full control",
        "session": "session-002"
      }
    ],
    "project_phases": {
      "current_phase": "Architecture & Documentation",
      "next_milestone": "MVP - Export Dialog"
    },
    "sessions": [
      {"id": "session-001", "focus": "Open source conversion"},
      {"id": "session-002", "focus": ".devsession format design"},
      {"id": "session-003", "focus": "Context loading strategy"}
    ]
  }
}
```

**Project overview is compact (~300-500 tokens)** - Provides macro context always

### Layer 2: Session Summary (This Session)

**What happened today - generated at session end:**
```json
{
  "summary": {
    "session_goal": "Build Stripe webhook integration",
    "decisions": [
      {
        "id": "dec_001",
        "decision": "Use req.rawBody for signature verification",
        "reasoning": "Body-parser modifies req.body",
        "references": ["msg_045", "msg_046"]
      }
    ],
    "code_changes": [
      {
        "id": "code_001",
        "files": ["api/webhooks.js"],
        "description": "Added signature verification"
      }
    ],
    "problems_solved": [...],
    "open_issues": [...],
    "next_steps": [...]
  }
}
```

**Summary is compact (500-1000 tokens)** - Current session context

### Layer 3: Full Context with Vectors

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

#### Multi-Session Semantic Search Performance

**Critical Infrastructure** (Implemented November 20, 2025)

The .devsession format supports **blazing-fast multi-session semantic search** using binary storage and vectorized operations:

**File Structure on Disk:**
```
sessions/
  ├── index.json                  # Unified index metadata (~100KB/1000 msgs)
  ├── .index_embeddings.npy       # Binary embedding matrix (~6MB/1000 msgs)
  ├── session1.devsession         # Full conversation + metadata
  ├── session2.devsession
  └── ...
```

**Binary Storage Benefits:**
- **200x faster loading** than JSON (memory-mapped `.npy` files vs text parsing)
- **50% smaller storage** (4 bytes per float32 vs 8 bytes per JSON number)
- **Industry standard** (same format used by TensorFlow, PyTorch, scikit-learn)
- **Zero RAM overhead** (memory-mapped files reference disk, no copy to RAM)

**Search Performance** (Production Benchmarks):
```
Dataset Size    Search Time    Queries/Second
100 messages    0.13ms         7,409 QPS
500 messages    0.23ms         4,347 QPS
1,000 messages  0.34ms         2,941 QPS
5,000 messages  1.88ms         531 QPS
10,000 messages 3.67ms         272 QPS
```

**Real-World Multi-Session Performance:**
- **20 sessions** (4,000 vectors): 1.5ms per query (was 200ms with JSON)
- **50 sessions** (10,000 vectors): 3.7ms per query (was 500ms)
- **100+ sessions** (100,000 vectors): ~50ms per query (was 5 seconds)

**How It Works:**

1. **Index Building** (`reccli index build`):
   ```python
   # Extract all embeddings from sessions
   embeddings_matrix = np.array([v['embedding'] for v in unified_vectors])

   # Save as binary file (memory-mapped loading)
   np.save(sessions_dir / '.index_embeddings.npy', embeddings_matrix)

   # Reference in index.json (don't duplicate embeddings)
   index['embeddings_file'] = '.index_embeddings.npy'
   ```

2. **Search Execution** (vectorized operations):
   ```python
   # Load embeddings (instant with memory mapping)
   embeddings = np.load('.index_embeddings.npy', mmap_mode='r')

   # Compute ALL similarities at once (vectorized!)
   similarities = np.dot(embeddings, query_vector)  # 0.3ms for 1000 vectors

   # Top-K selection
   top_indices = np.argpartition(-similarities, k)[:k]
   ```

**Three-Path Loading Strategy** (backward compatible):
- **PATH 1**: Binary `.npy` file (production - fastest)
- **PATH 2**: In-memory numpy array (testing/benchmarks)
- **PATH 3**: Extract from message embeddings (legacy - slower but works)

**Scalability:**
- Handles **dozens of sessions** (10,000+ messages) in <5ms
- Ready for **Phase 10** multi-project search across 50+ sessions
- Can scale to **millions of vectors** with optional FAISS integration

This infrastructure enables the .devsession vision of **cross-session intelligence** - search your entire development history across all projects instantly.

**Additional Documentation:**
- LLM retrieval tools implementation: `docs/archive/progress/phases/PHASE_8_IMPLEMENTATION.md`
- Performance optimization details: `docs/implementation/indexing/README.md`

---

## Context Loading: How LLM Uses It

### When Loading a .devsession:

```python
def load_devsession_context(session_file, recent_messages):
    """
    Load three-layer context for LLM
    """
    session = load_devsession(session_file)

    # 1. Always load project overview (macro context)
    project_overview = session.project_overview  # ~300 tokens

    # 2. Always load session summary (this session context)
    summary = session.summary  # ~500 tokens

    # 3. Vector search using recent messages as implicit goal
    query_embedding = embed_messages(recent_messages)
    similar_messages = vector_search(
        query=query_embedding,
        top_k=10,  # Small radius
        session=session.conversation
    )  # Blazing fast: ~0.3ms for 1000 messages with binary .npy files

    # 4. Include recent context (continuity)
    recent_context = recent_messages  # ~500 tokens

    # 5. Combine into LLM context
    context = {
        "project_overview": project_overview,  # Macro: What is this project?
        "summary": summary,  # This session: What happened today?
        "relevant_history": similar_messages,  # Micro: Related earlier work
        "recent": recent_context  # Current: What we're doing now
    }

    return context  # Total: ~2000 tokens instead of 50,000+
```

### Example Usage:

**Scenario: Return to project after 3 months**

```
User: "Continue working on RecCli"

LLM receives:
1. Project Overview layer (~300 tokens):
   - Name: RecCli - CLI terminal recorder with AI session management
   - Architecture: RecCli (UI) + .devsession (format)
   - Key decisions:
     * Open source (MIT) - build credibility
     * Three-layer format - better than compaction
     * Preemptive compaction at 190K tokens
   - Current phase: Architecture & Documentation
   - Next milestone: MVP - Export Dialog
   - 3 sessions completed

2. Session Summary layer (~500 tokens):
   - Last session goal: Define compaction strategy
   - Decisions: Preemptive compaction, implicit goal from recent messages
   - Code changes: Updated CONTEXT_LOADING.md, ARCHITECTURE.md
   - Open issues: None
   - Next steps: Implement export dialog

3. Vector search results (~700 tokens):
   (Based on recent conversation about export dialog)
   - msg_045: "Export dialog shows format options"
   - msg_067: ".devsession format with three layers"
   - msg_089: "Generate embeddings on export"

4. Recent context (~500 tokens):
   - Last 20 messages discussing next steps

Total context: ~2000 tokens
Full project history: 50,000+ tokens saved with embeddings

LLM: "RecCli is your open-source CLI recorder with .devsession format.
      You've completed the architecture design phase across 3 sessions.
      Next milestone is implementing the MVP export dialog. The design
      is complete - you need to wire the UI into the CLI/runtime path
      with format selection (.txt, .md, .devsession) and optional embedding generation.

      Should we start implementing the export dialog now?"
```

**Key Benefit:** LLM has full context from macro (project goals) to micro (implementation details) in just ~2000 tokens.

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
- Three-layer context = macro + micro perspective
- Project overview = keeps work aligned with goals
- Session summary = current work context
- Vector search = precise retrieval from full history
- Automatic documentation = project docs write themselves

### ✅ Scalable
- Incremental embedding (build as you go)
- Small context layers (300 + 500 + 1000 tokens)
- Large history (50K+ tokens) searchable
- Cross-session intelligence (project evolution tracked)

### ✅ Better Than Alternatives
- **vs Raw recording**: Has structure + intelligence
- **vs Compaction**: Nothing lost, everything searchable
- **vs Big context windows**: More focused, cheaper, macro awareness
- **vs Manual docs**: Documentation updates itself

---

## Cost Analysis

### Storage:
- Project overview: ~2 KB (updated each session)
- Session summary: ~2 KB
- Full conversation: ~500 KB
- Embeddings (1536-dim × 200 messages): ~1.2 MB
**Total per session: ~1.7 MB** (acceptable)

### API Costs (per session):
- Embedding generation: 50K tokens × $0.0001 = **$0.005**
- Session summary generation: 1 call × $0.001 = **$0.001**
- Project overview update: 1 call × $0.0005 = **$0.0005**
**Total per session: ~$0.0065** (negligible)

### Context Loading:
- Project overview: 300 tokens (always - macro context)
- Session summary: 500 tokens (always - this session)
- Vector results: 700 tokens (on demand - relevant history)
- Recent context: 500 tokens (continuity)
**Total: ~2000 tokens vs 50,000 raw** (96% reduction)

**Key advantage:** Macro + micro perspective in same token budget

---

## Next Steps

1. **Now**: Add export dialog to RecCli UI
2. **Q1 2025**: Add embedding generation (sentence-transformers)
3. **Q2 2025**: Build vector search + context loading
4. **Q3 2025**: Integrate with Claude Code API

**Start simple. Build incrementally. Stay frictionless.**
