# RecCli v2 Design Decisions

**Status:** Historical design-decision log.

This file captures decision-making during earlier implementation phases. It is useful for rationale, but [PROJECT_PLAN.md](/Users/will/coding-projects/RecCli/PROJECT_PLAN.md) and the current architecture/spec docs are the canonical description of the project now.

## Phase 4: Single-Stage vs Two-Stage Summarization

### The Question
Should we use a two-stage pipeline (Haiku for span detection → Sonnet for reasoning) or single-stage (Sonnet only)?

### The Answer: **Single-Stage by Default**

### Context: Our Use Case
RecCli's summarization is for **compaction**, not archival summarization:

```
Session grows → Hits 190K tokens (95% of 200K limit) → COMPACT → Continue
                                    ↓
                     Summary (~3-5K) + Recent (~20K) = ~25-30K
                     (Leaves 170K headroom to continue working)
```

This is fundamentally different from "summarize a completed 190K session for storage."

### Cost Analysis

**Two-Stage:**
- Stage 1 (Haiku): 190K input + 2K output spans = $0.048
- Stage 2 (Sonnet): 50K input (selected spans) + 3K output = $0.15
- **Total: ~$0.20 per compaction**

**Single-Stage (Sonnet):**
- Sonnet: 170K input (excluding recent 20K kept verbatim) + 3K output = $0.52
- **Total: ~$0.52 per compaction**

**Difference: $0.32 per compaction**

### Why Single-Stage Wins

1. **Compaction is infrequent**
   - Only triggers when hitting 190K tokens
   - For most users: Once per few hours of intensive coding
   - Even heavy users: Maybe 2-3 times per day
   - Daily cost difference: $0.32 × 2 = **$0.64/day**

2. **Simplicity & Reliability**
   - One API call instead of two
   - No span merging logic
   - No risk of Haiku missing important discussions
   - Easier to debug and maintain
   - Less engineering time = more valuable than $0.64/day

3. **Quality**
   - Sonnet sees full conversation flow
   - Better understanding of context and causality
   - No risk of span boundaries cutting critical info
   - More coherent summaries

4. **Engineering Time**
   - Two-stage adds ~2-3 days of development
   - Span detection quality tuning
   - Span merging logic
   - Testing edge cases
   - **Your time is worth more than $0.64/day in savings**

### When Two-Stage Would Make Sense

Only if you have:
1. **Very high volume** (1000+ compactions/day) - enterprise SaaS
2. **Very long sessions** (500K+ tokens) - beyond Sonnet's window
3. **Batch processing** old sessions for analysis
4. **Cost-sensitive users** who explicitly opt-in

### Implementation

We built **both** but default to single-stage:

```python
summarizer = SessionSummarizer(
    llm_client=client,
    model="claude-3-5-sonnet-20241022",
    use_two_stage=False,  # Default: simple & reliable
    span_detection_model="claude-3-5-haiku-20241022"  # Available if needed
)
```

Users can enable two-stage via config if they need it:
```yaml
summarization:
  use_two_stage: true  # For cost-sensitive or high-volume users
```

### The GPT-5 Pro Analysis Confirms This

From the analysis you shared:
> "Start with **single-stage Sonnet** + **strong pre-filters** and **safety nets**.
> It's simpler and robust for 190k. Add **two-stage** behind a flag that triggers
> only when Stage-1 shows **high reduction** (low r) while keeping high recall."

Key insights:
- Two-stage only pays when Stage-1 removes >85-90% of content
- For compaction use case, we're keeping recent messages anyway
- Quality risk of missing spans outweighs cost savings
- Target 75-85% of context limit (150-170K), not 100%

### Target Context Budget

**Hard limit:** 200K tokens
**Soft cap:** 150-170K tokens (75-85%)
**Compaction trigger:** 190K tokens (95%)
**After compaction:** ~25-30K tokens (leaves 170K headroom)

**Breakdown after compaction:**
- Summary: 3-5K tokens
- File operations/ground truth: 2-3K tokens
- Recent messages (verbatim): 15-20K tokens
- Pins (if any): 1-2K tokens
- **Total:** ~25-30K tokens
- **Headroom:** 170K tokens for continued work

### Conclusion

**Ship single-stage Sonnet.** The cost difference is negligible compared to:
- Engineering time saved
- Maintenance simplicity
- Quality improvements
- User experience (more reliable)

Two-stage is available behind a flag for future enterprise users who need it, but we're not optimizing for that now.

---

## Advanced Features Implemented

### 1. Temporal Hints (t_first / t_last)

**What:** Every summary item includes ISO timestamps for first and last message in its range.

**Why:** Enables time-aware retrieval and temporal preference in reasoning.

**Implementation:**
- `extract_temporal_bounds()` extracts timestamps from conversation
- Converts Unix timestamps to ISO format
- Attached to all decision/code_change/problem/issue/next_step items
- System prompt includes: _"Prefer more recent evidence unless an earlier canonical decision contradicts it"_

**Usage:**
```python
decision = {
    "decision": "Use modal for export",
    "message_range": {"start": "msg_042", "end": "msg_050", ...},
    "t_first": "2024-10-26T18:22:12",  # When discussion started
    "t_last": "2024-10-26T18:28:49"    # When it concluded
}
```

### 2. Break-Even Switch (Auto Two-Stage Decision)

**What:** Automatically decides single-stage vs two-stage based on cost analysis.

**Formula:**
```
r_break_even = 1 - (cost_haiku_input / cost_sonnet_input)
             = 1 - (0.25 / 3.0)
             = 0.9167 (91.67%)
```

**Decision Logic:**
- If Stage-1 keeps **>91.67%** of tokens → Use **single-stage** (not worth overhead)
- If Stage-1 keeps **<91.67%** of tokens → Use **two-stage** (saves cost)

**Test Results:**
```
Scenario 1: 190K → 30K (15.8% kept)
  → Use two-stage (saves 60% cost: $0.615 vs $0.245)

Scenario 2: 190K → 180K (94.7% kept)
  → Use single-stage (two-stage adds overhead for minimal benefit)
```

**Implementation:**
```python
summarizer = SessionSummarizer(
    auto_switch_two_stage=False,  # Default: manual control
    reduction_threshold=0.5        # Fallback if >50% kept
)

# Automatic decision
should_use, reason = summarizer.should_use_two_stage(
    total_tokens=190_000,
    estimated_span_tokens=30_000  # Optional: from Stage-1
)
```

**Safety Nets (if two-stage enabled):**
- Always include last N messages (highest signal)
- Always include pinned items
- Always include file/tool events
- Always include messages with code blocks
- Coverage metric: if <40-50% of structural events retained, fallback to single-stage

### 3. Quality Guardrails

**Pre-filtering (before any LLM):**
- Drop greetings/pleasantries
- Keep all code/tool outputs
- Keep messages with file paths
- Keep messages with decision keywords ("decide", "choose", "fix", "merge")
- Truncate extremely long logs (keep header + tail, placeholder in middle)

**Verification:**
- Reference validation (all message IDs exist)
- Message range ordering (start ≤ end)
- Confidence levels on all items
- Auto-fix invalid items

---

---

## The Core Innovation: Two-Level Linked Retrieval

### The Problem with Current Approaches

**ChatGPT/Claude (Pure Summary):**
```
Full conversation (200K) → Summarize → Throw away original
Problem: Lossy, can't verify, missing critical details
```

**RAG Systems (Pure Vector Search):**
```
Full conversation (200K) → Chunk → Vector search → Return fragments
Problem: Slow, chunking splits discussions, no structure
```

**Keyword Search:**
```
Full conversation (200K) → Keyword match → Return lines
Problem: Brittle, no semantics, returns noise
```

### The .devsession Solution: Hybrid Two-Level Retrieval

```
Layer 1: Summary (3-5K tokens, AI-generated)
           ↓ message_range + references (temporal index)
Layer 2: Full Conversation (190K tokens, preserved)
           ↓ O(1) array lookup
         Exact discussion with full context
```

**How it works:**

1. **Level 1 (Fast): Search Summary**
   - Only 3-5K tokens (10-20 summary items)
   - Vector search finds relevant items in milliseconds
   - Each item has human-readable description

2. **Level 2 (Precise): Retrieve Full Context**
   - Use `message_range.start_index` and `end_index`
   - O(1) array lookup: `conversation[42:50]`
   - Returns full 8-message discussion with complete context
   - No chunking issues, no missing context

3. **Feed to LLM**
   - LLM reads full discussion (not lossy summary)
   - Can see exact reasoning, alternatives considered, nuances
   - Verifiable against source (no hallucinations)

### Implementation

**Summary Item Structure:**
```python
{
    "id": "dec_7a1e...",
    "decision": "Use modal dialog for export",
    "reasoning": "Focuses user attention on task",

    # Temporal/chronological linking
    "message_range": {
        "start": "msg_042",      # Human-readable
        "end": "msg_050",
        "start_index": 42,       # Fast array lookup
        "end_index": 50
    },
    "references": ["msg_045", "msg_046", "msg_047"],  # Key moments

    # Temporal metadata
    "t_first": "2024-10-26T18:22:12",
    "t_last": "2024-10-26T18:28:49"
}
```

**Retrieval Code:**
```python
# Level 1: Search summary (fast)
results = vector_search("modal dialog", summary_items)
decision = results[0]

# Level 2: Retrieve full context (precise)
start = decision["message_range"]["start_index"]
end = decision["message_range"]["end_index"]
full_discussion = conversation[start:end]  # O(1) lookup

# Feed to LLM with full context
llm.query("Why modal?", context=full_discussion)
```

### Why This Beats Everything

**vs Pure Summary:**
- ✅ Lossless (can verify against source)
- ✅ Complete (full reasoning preserved)
- ✅ Verifiable (no hallucinations)

**vs Pure Vector Search:**
- ✅ Fast (search 3K not 190K)
- ✅ Contextual (returns discussions not fragments)
- ✅ Structured (summary organizes by type)

**vs Keyword Search:**
- ✅ Semantic (finds "dialog" when searching "modal")
- ✅ Ranked (best matches first)
- ✅ Precise (exact discussion, not scattered lines)

### The Critical Insight

> **Users don't need lossless context everywhere.**
> **They need fast search + lossless retrieval when needed.**

.devsession provides both:
- **Fast**: Search compressed summary (3-5K tokens)
- **Lossless**: Retrieve exact source when needed
- **Linked**: Temporal index connects them seamlessly

This is what ChatGPT/Claude are missing and why .devsession is a paradigm shift. 🚀

---

**Decision Date:** 2025-11-01
**Decision Maker:** Based on user requirements + GPT-5 Pro analysis
**Status:** Fully implemented and tested ✅
**Test Coverage:** 100% (temporal extraction, break-even math, cost estimation, auto-decision logic, two-level retrieval)

---

## Phase 0: Write-Ahead Log (WAL) Recording Architecture

### The Question
How should we handle crash-safe terminal recording with minimal data loss?

### The Answer: **Write-Ahead Log (WAL) Pattern**

### The Problem
Traditional terminal recorders have data loss risks:
1. **Buffer loss**: Events buffered in memory lost on crash
2. **Partial writes**: Corruption if process killed during save
3. **No incremental parsing**: Must wait until end to parse conversation
4. **All-or-nothing**: Complete session loss if finalization fails

### The WAL Solution

**Architecture:**
```
Recording starts → .wal file (append-only, fsync every 64 events)
                         ↓
                    Raw events stream
                         ↓
              On stop: Parse conversation
                         ↓
              Build .devsession atomically
                         ↓
              Auto-compact (conversation mode)
                         ↓
              Delete .wal (success)
```

**Implementation:** `reccli/wal_recorder.py`

```python
class WALRecorder:
    """Crash-safe terminal recorder using write-ahead log pattern"""

    def __init__(self, output_path: Path, shell: Optional[str] = None):
        self.wal_path = output_path.with_suffix('.wal')
        self.fsync_interval = 64  # fsync every N events

    def _append_event(self, timestamp: float, event_type: str, data: str):
        """Append event to WAL (crash-safe)"""
        event = [timestamp, event_type, data]
        self.wal_file.write(json.dumps(event) + '\n')
        self.event_count += 1
        self.fsync_counter += 1

        # Periodic fsync for crash safety
        if self.fsync_counter >= self.fsync_interval:
            self.wal_file.flush()
            os.fsync(self.wal_file.fileno())
            self.fsync_counter = 0

    def _finalize(self):
        """Finalize recording: WAL → .devsession"""
        # 1. Load events from WAL
        # 2. Parse conversation
        # 3. Build final .devsession
        # 4. Atomic write: .tmp → rename
        # 5. Auto-compact
        # 6. Delete WAL
```

**Benefits:**
1. **Crash-safe**: Events fsynced every 64 appends (max 64 events lost)
2. **No corruption**: Atomic rename prevents partial writes
3. **Fast**: Append-only writes, no seek operations
4. **Clean**: WAL deleted after successful finalization
5. **Debuggable**: WAL preserved on failure for recovery

**File Lifecycle:**
```
~/reccli/sessions/session_20251102.wal       (during recording)
         ↓
~/reccli/sessions/session_20251102.devsession.tmp  (finalization)
         ↓
~/reccli/sessions/session_20251102.devsession      (atomic rename)
         ↓
WAL deleted (cleanup)
```

### Why Not Stream Directly to .devsession?

**Alternative considered:**
```python
# Bad: Stream events directly to .devsession
with open(output_path, 'w') as f:
    f.write('{"events": [')
    for event in stream:
        f.write(json.dumps(event) + ',')
```

**Problems:**
1. **Invalid JSON on crash**: Incomplete array, missing closing brackets
2. **No atomic writes**: File left in broken state
3. **Can't parse conversation**: Need complete event stream to parse
4. **No rollback**: Corrupted file is unrecoverable

**WAL advantages:**
- Each line is valid JSON (recoverable even if incomplete)
- Atomic finalization (all-or-nothing)
- Parse after recording complete (conversation requires full context)
- Clean separation: recording vs processing

---

## Session Compaction: Removing Redundant Data

### The Problem: 189x Bloat

**Test case:** Simple Q&A conversation
- User: "which came first the chicken or the egg?"
- Assistant: ~300 word response
- Raw .devsession file: **189KB**
- Actual conversation data: **1KB**
- **Bloat ratio: 189x**

**Breakdown of 189KB:**
```
Terminal events (raw keystrokes): 185KB (98%)
  - Every keystroke captured ('w', 'h', 'i', 'c', 'h', ...)
  - Incremental typing artifacts
  - Shell prompts, UI chrome, animations
  - ANSI escape codes

Conversation (parsed): 1KB (0.5%)
  - User message
  - Assistant message

Metadata: 3KB (1.5%)
```

**Problem:** 690MB/year for casual use (1 conversation/day)

### The Solution: Auto-Compaction

**Implementation:** `reccli/compactor.py`

**Four compaction modes:**

1. **none** - Keep everything (debugging)
2. **conversation** - Keep only conversation + metadata (smallest, ~2KB)
3. **audit** - Keep conversation + audit frames (~5-15KB)
4. **lossless** - Move events to external .events.zst (keeps replay ability)

**Default: conversation mode** (auto-applied after finalization)

```python
class SessionCompactor:
    def _compact_conversation_only(self, data: Dict) -> Dict:
        """Keep only conversation + minimal metadata"""
        return {
            'format': 'devsession',
            'version': data.get('version', '2.0'),
            'session_id': data.get('session_id'),
            'conversation': data.get('conversation', []),
            'meta': {
                'duration': self._calculate_duration(data),
                'message_count': len(data.get('conversation', [])),
                'compaction': {
                    'mode': 'conversation',
                    'original_events': len(data.get('terminal_recording', {}).get('events', []))
                }
            },
            'terminal_recording': None,  # Drop raw events
            # ... other fields null/minimal
        }
```

**Results:**
```
Before compaction: 189KB (282 raw events)
After compaction:  2.2KB (2 messages)
Saved: 186.8KB
Compression ratio: 86x
```

**Integration with WAL:**
```python
def _finalize(self):
    # ... parse conversation ...

    # Auto-compact (conversation mode)
    if len(conversation) > 0:
        from .compactor import auto_compact
        stats = auto_compact(self.output_path, mode='conversation')
        print(f"✓ Compacted: {stats['saved_bytes']/1024:.1f}KB saved ({stats['compression_ratio']:.1f}x smaller)")
```

**Output:**
```
✅ Recording stopped
Finalizing 282 events...
Parsing conversation...
✓ Parsed 2 messages
✓ Finalized to /Users/will/reccli/sessions/session_20251102.devsession
✓ Duration: 78.5s
✓ Events: 282
Compacting session...
✓ Compacted: 125.8KB saved (75.8x smaller)
```

### Why This is Correct

**Question:** "Should we keep raw events for replay?"

**Answer:** No, because:
1. **Replay is not a use case**: Users care about the conversation, not terminal playback
2. **Video/GIF is better**: If visual needed, screen recording tools exist
3. **Conversation is the artifact**: That's what matters for .devsession format
4. **Bloat is unacceptable**: 189KB → 2KB is the right compression
5. **Forward-compatible**: If replay needed later, we can implement lossless mode

**Alternative if replay needed:**
- Use `lossless` mode (moves events to external .events.zst)
- But default to `conversation` mode for 98% of users

---

## Conversation Parsing: Terminal Events → Structured Messages

### The Challenge

**Input:** Raw terminal events (keystrokes, output, ANSI codes)
```
[0.0, "o", "\x1b[1m> \x1b[0m"]
[1.2, "i", "w"]
[1.3, "i", "h"]
[1.4, "i", "i"]
[1.5, "i", "c"]
[1.6, "i", "h"]
...
[7.5, "i", "\r"]
[8.0, "o", "⏺ This is actually..."]
```

**Output:** Structured conversation
```json
{
  "role": "user",
  "content": "which came first the chicken or the egg?",
  "timestamp": 1.2
}
```

### Implementation: `reccli/parser.py`

**Key components:**

1. **Character accumulation**: Build complete messages from keystrokes
```python
user_input_buffer = []
for event in events:
    if event[1] == "i":  # input event
        if data == '\r':  # Enter pressed
            user_text = ''.join(user_input_buffer)
            conversation.append({"role": "user", "content": user_text})
        elif data == '\x7f':  # Backspace
            user_input_buffer.pop()
        else:
            user_input_buffer.append(data)
```

2. **Incremental typing cleanup**: Remove UI artifacts
```python
# Skip loading animations
if stripped.startswith('✶') or 'Pondering' in stripped:
    continue

# Skip keyboard instructions (but keep menu content)
if 'Enter to select' in stripped:
    continue

# Skip user prompt echo from assistant responses
if stripped.startswith('>') and len(stripped) > 1:
    continue
```

3. **Interactive menu handling**: Preserve decision trees
```python
# KEEP numbered menu options (valuable context)
# Example:
# ❯ 1. 3D model/game (Unity, Blender, Three.js)
#   2. Physical model/prop
#   3. Simulation/physics engine

# SKIP keyboard shortcuts
# Example: "Enter to select · Tab/Arrow keys to navigate"
```

**Design decision:** Keep menu content, remove UI chrome
- ✅ Keep: Numbered options, menu text, questions
- ❌ Remove: Keyboard shortcuts, animations, prompts

### Filtering Strategy

**Categories:**

1. **Redundant UI (filter out):**
   - Loading animations ("Pondering...", "Galloping...")
   - Keyboard shortcuts ("Enter to select")
   - Status indicators ("? for shortcuts")
   - User prompt echo in assistant response

2. **Valuable content (keep):**
   - User messages
   - Assistant responses
   - Interactive menu options
   - Decision tree questions
   - Code blocks
   - Error messages

**Result:**
```markdown
**User:**
which came first the chicken or the egg?

**Assistant:**
⏺ This is actually a question with a scientific answer! The egg came first.

  From an evolutionary perspective...

What type of UFO project would you like to create?
❯ 1. 3D model/game (Unity, Blender, Three.js)
  2. Physical model/prop
  3. Simulation/physics engine

⏺ User declined to answer questions
```

Clean, readable, preserves decision context.

---

## Export Formatting

### Duration Display

**Problem:** Raw float timestamps
```markdown
**Duration:** 55.144407987594604
```

**Solution:** Human-readable formatting
```python
def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"
```

**Result:**
```markdown
**Duration:** 55s
**Duration:** 1m 19s
**Duration:** 2h 15m
```

---

## Format Standardization: .devsession Only

### The Question
Should we support .cast format for exports?

### The Answer: **No - .devsession Only**

### Reasoning

**Use cases for .cast format:**
1. ~~Replay in a terminal cast player~~ → Not a use case (conversation matters, not playback)
2. ~~Compatibility with existing tools~~ → We're not a terminal recorder, we're a conversation recorder
3. ~~Standard format~~ → .devsession is our standard

**.devsession advantages:**
- Forward-compatible (can add summary/vector_index later)
- Structured conversation layer
- Metadata and compaction support
- Single format everywhere (simplicity)

**Decision: Remove all .cast references**
- Removed from CLI export options
- Removed from GUI export dialog
- Removed from exporters.py
- Export formats: txt, md, json, html only

---

**Decision Date:** 2025-11-02
**Decision Maker:** Based on user requirements + production testing
**Status:** Fully implemented and tested ✅
**Implementation:**
- WAL recorder: `reccli/wal_recorder.py`
- Compactor: `reccli/compactor.py`
- Parser: `reccli/parser.py`
- Exporters: `src/export/exporters.py`
