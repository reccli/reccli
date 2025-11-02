# RecCli Project Plan
## Building the .devsession Protocol for Intelligent AI Context Management

**Status**: ✅ Phase 0 + 0.5 + 1 + 2 + 3 + 4 Complete → Phase 5 Next
**Started**: 2025-11-01
**Current Phase**: Phase 5 - Vector Embeddings & Search
**Completed**: Phase 0 (Terminal Recording), Phase 0.5 (Native LLM + GUI), Phase 1 (Data Integrity), Phase 2 (Conversation Parsing), Phase 3 (Token Counting), Phase 4 (Summary Generation)

---

## 🎯 Project Vision

RecCli is a terminal session recorder that creates `.devsession` files - a revolutionary dual-layer format that solves the AI context management problem through intelligent summarization and lossless memory preservation.

### Core Innovation: Two-Level Linked Retrieval

The .devsession format enables a breakthrough in context management:

**Traditional Approaches (Lossy):**
```
ChatGPT/Claude: Full conversation → Summarize → Discard original
Problem: Can't verify, missing details, no drill-down
```

**The .devsession Breakthrough (Lossless + Fast):**
```
Layer 1 (Summary): AI-generated summary (3-5K tokens)
           ↓ message_range + temporal index
Layer 2 (Full Conversation): Complete preserved conversation (190K tokens)
           ↓ O(1) array lookup
         Exact discussion with full context
```

**How it works:**

**Scenario 1: Post-Compaction (Primary Use Case - 95% of usage)**
```
Session hits 190K tokens → COMPACT
    ↓
Summary generated (3-5K) with message_range links
    ↓
LLM context = Summary (3-5K) + Recent messages (20K) + Links
    ↓
LLM reads summary, follows links when detail needed
    ↓
retrieve conversation[42:50] → Full discussion (O(1) lookup)
```
- **No search needed** - summary already loaded in context
- Links (`message_range`) already computed during compaction
- LLM just follows links to retrieve full context on-demand

**Scenario 2: Prior Session Recall (Multi-Document - Phase 5+)**
```
Working on Day 5, need context from Day 1
    ↓
Vector search summaries across old .devsession files
    ↓
Find relevant items → follow message_range links
    ↓
Retrieve full context from old session
```
- **Vector search needed** - searching across multiple sessions
- Summary NOT in current context (different file)
- Find relevant items first, then retrieve

**Key Insight:**
1. **Post-compaction**: Summary in context, links pre-computed, no search
2. **Prior sessions**: Search needed to find relevant items across files

**The Four Layers:**
- **Layer 1**: Terminal recording (asciinema-compatible events)
- **Layer 2**: Parsed LLM conversation (user/assistant messages)
- **Layer 3**: AI-generated summary with temporal links to Layer 2
- **Layer 4**: Project-level overview (.devproject) across sessions

**Result**: 99% token reduction (200K → 2K) with BETTER reasoning, not worse.

**Key Differentiators:**
- ✅ **Fast**: Search compressed summary, not full 190K conversation
- ✅ **Semantic**: Vector search (Phase 5) finds conceptually similar items
- ✅ **Lossless**: Full conversation preserved, retrieve exact sections via temporal index
- ✅ **Verifiable**: Summary links to source via `message_range` references
- ✅ **Contextual**: Returns complete discussions, not fragments

---

## 📋 Implementation Phases

### Phase 0: Terminal Recording Foundation ✅ COMPLETE
**Goal**: Capture terminal I/O and output to .devsession format

**DECISION MADE (2025-11-01)**: **Pure Python Recorder** - No dependencies, full control, faster to ship.

#### Why Pure Python Won:
- ✅ **No external dependencies** - Uses Python's built-in `pty` module
- ✅ **All in one codebase** - Python only, no Rust/asciinema dependency
- ✅ **Fast to implement** - 1-2 days vs 5-7 days for Rust
- ✅ **Easy to maintain** - You can modify without learning Rust
- ✅ **Full control** - Custom .devsession format from day 1
- ✅ **Performance is fine** - Terminal I/O is not performance-critical (~1-10 KB/sec)

#### Architecture:
```python
DevsessionRecorder (reccli/recorder.py)
  ├─ Uses Python pty module for terminal capture
  ├─ Records events in .devsession format directly
  ├─ Auto-saves incrementally (crash protection)
  └─ Outputs complete .devsession file on stop
```

#### Tasks (Phase 0):
- [x] Analyze options (asciinema Rust vs Python recorder)
- [x] Make decision (Pure Python)
- [x] Create `reccli/` package structure
- [x] Implement `DevsessionRecorder` class
  - [x] PTY capture with event recording
  - [x] Incremental auto-save
  - [x] Terminal resize handling
  - [x] Clean shutdown and finalization
- [x] Implement `DevSession` file manager
  - [x] `load()` / `save()` methods
  - [x] JSON schema validation
  - [x] Event append and flush
- [x] Create CLI commands
  - [x] `reccli record [name]` - Start recording
  - [x] `reccli list` - Show sessions
  - [x] `reccli show` - Session details
  - [x] `reccli export` - Export to .txt/.md/.cast
- [x] Test basic recording end-to-end
- [x] Handle edge cases (window resize, UTF-8, ANSI codes)

**Deliverable**: ✅ `reccli record` command that creates .devsession files with terminal events

**Duration**: 1 day (actual)

---

### Phase 0.5: Native LLM + GUI ✅ COMPLETE
**Goal**: Add native LLM chat interface and floating button GUI

**BONUS FEATURES** - Built in addition to Phase 0:

#### Native LLM CLI:
- [x] Direct API integration (Claude + GPT)
- [x] Interactive chat mode with conversation history
- [x] One-shot query mode
- [x] API key management (secure local storage)
- [x] Multiple model support (Claude Sonnet/Opus/Haiku, GPT-5/4)
- [x] Auto-save conversations to .devsession format
- [x] Clean conversation objects (no terminal parsing needed)

#### Floating Button GUI (Ported from reccli-public):
- [x] Tkinter floating button that follows terminal window
- [x] One-click start/stop recording
- [x] Dark mode support (auto-detects system appearance)
- [x] Terminal window tracking by ID (macOS)
- [x] Auto-hide when terminal minimized (keeps recording)
- [x] Drag to reposition
- [x] Right-click menu (Stats, Sessions, Quit)
- [x] Visual states (idle/recording/stopped)
- [x] BackgroundRecorder using subprocess + AppleScript
- [x] Launcher script (`reccli-gui.py`)
- [x] **Added watcher** - Auto-launches GUI for new terminals, prevents duplicates on Space changes

**Files Created**:
- `reccli/llm.py` (300 lines) - LLMSession class
- `reccli/config.py` (65 lines) - API key management
- `reccli/recorder.py` - Added DevsessionGUI + BackgroundRecorder (500+ lines)
- `reccli-gui.py` - GUI launcher script
- Updated `reccli/cli.py` - chat, ask, config commands
- Updated `README.md` - Full documentation

**Deliverable**: ✅ Native LLM interface + Floating button GUI for easy recording

**Duration**: 1 day (actual)

---

### Phase 1: Core .devsession File Management ✅ COMPLETE
**Goal**: Read/write/manage .devsession files programmatically

#### Tasks:
- [x] Create `DevSession` class
  - [x] `load(path)` - Read .devsession file
  - [x] `save(path)` - Write .devsession file
  - [x] `append_event(event)` - Add terminal event
  - [x] `incremental_save()` - Auto-save for crash protection
  - [x] `finalize()` - Complete session recording

- [x] Implement .devsession JSON schema validation
  - [x] Validate required fields (format, version, terminal_recording)
  - [x] Validate event structure (timestamp, type, data)
  - [x] Validate conversation structure (role, content)
  - [x] Clear error messages for invalid files
- [x] Add checksums (blake2b) for event integrity
  - [x] Calculate checksums for events, conversation, summary, vector_index
  - [x] Verify checksums on load (detect corruption/tampering)
  - [x] Auto-calculate fresh checksums on save
- [x] Test file format with sample sessions
  - [x] Test corruption detection
  - [x] Test schema validation with invalid files

**Deliverable**: ✅ Working DevSession API with data integrity verification

**Features Added**:
- `_calculate_checksums()` - Generate blake2b hashes for all data structures
- `verify_checksums()` - Detect file corruption or tampering
- `_validate_schema()` - Validate .devsession file format
- `load(verify_checksums=True)` - Optional checksum verification on load

**Duration**: 1 hour (actual)

---

### Phase 2: Conversation Parsing ✅ COMPLETE
**Goal**: Extract user/LLM conversation from terminal events

#### Challenge:
Terminal events are raw I/O - we need to parse:
```
[0.123, "o", "$ claude\r\n"]
[1.456, "o", "Claude Code v1.0\r\n"]
[2.789, "i", "help me build auth\r\n"]  ← USER
[3.012, "o", "I'll help...\r\n"]         ← ASSISTANT
```

Into structured conversation:
```json
{
  "role": "user",
  "content": "help me build auth",
  "timestamp": 2.789
}
```

#### Tasks:
- [x] Build conversation parser
  - [x] Detect LLM CLI patterns (claude, chatgpt-cli, etc.)
  - [x] Separate user input (event_type: "i") from output ("o")
  - [x] Handle multi-line responses
  - [x] Preserve message boundaries
  - [x] Incorporate proven cleaning logic from reccli-public
  - [x] Remove ANSI escape codes
  - [x] Clean incremental typing artifacts (keystroke-by-keystroke recording)
  - [x] Filter loading animations and duplicate UI elements

- [x] Add DevSession integration methods
  - [x] `parse_conversation()` - Parse terminal events into conversation
  - [x] `auto_parse_conversation()` - Auto-parse if needed

**Deliverable**: ✅ Working conversation parser with proven cleaning logic from reccli-public

**Features Added**:
- `ConversationParser` class in `reccli/parser.py`
- `clean_text()` - ANSI escape code removal
- `clean_incremental_typing()` - Remove typing artifacts (based on reccli-public logic)
- `detect_llm()` - Identify Claude/ChatGPT sessions
- `parse()` - Extract structured conversation from events
- `parse_conversation()` and `auto_parse_conversation()` methods in DevSession

**Duration**: 2 hours (actual)

---

### Phase 3: Token Counting & Monitoring ✅ COMPLETE
**Goal**: Track session token count and warn before limit

#### Tasks:
- [x] Implement token counter (tiktoken for OpenAI/Claude)
  - [x] Support Claude and GPT models
  - [x] Fallback to character-based estimate if tiktoken not installed
  - [x] Count text, messages, and conversations
  - [x] Count terminal output events
- [x] Add `token_counts` to .devsession file format
  - [x] Store counts for conversation, terminal_output, summary layers
  - [x] Store total and last_updated timestamp
- [x] Create `check_tokens()` function
  - [x] `calculate_tokens()` - Calculate tokens for all layers
  - [x] `check_tokens()` - Check limit and return warning
- [x] Add warning at 180K tokens (90% of limit)
- [x] Add critical warning at 190K tokens (95% of limit)
- [x] Update to_dict() and load() to persist token_counts
- [x] Test token counting with mock conversations

**Deliverable**: ✅ Real-time token monitoring with warning thresholds

**Features Added**:
- `TokenCounter` class in `reccli/tokens.py`
  - `count_text()`, `count_message()`, `count_conversation()`
  - `count_terminal_output()` - Count tokens in raw events
  - `get_limit()` - Get token limit for any model
  - `check_limit()` - Return status (ok/warning/critical) and percentage
  - `format_warning()` - Format user-friendly warning messages
- `calculate_tokens()` and `check_tokens()` methods in DevSession
- `token_counts` field in .devsession file format
- Warning thresholds: 90% (warning), 95% (critical)

**Duration**: 1 hour (actual)

---

### Phase 4: Summary Generation (AI-Powered) ✅ COMPLETE
**Goal**: Generate intelligent summaries from conversation events

#### Tasks:
- [x] Design enhanced summary schema (v1.1)
  - [x] Stable IDs using blake2b hashing
  - [x] Provenance metadata (model, version, session_hash)
  - [x] Reference verification (message ranges + key refs)
  - [x] Confidence levels (low/medium/high)
  - [x] Pin/lock/edit functionality
  - [x] Audit trail for human modifications
  - [x] Causal edges for graph-based retrieval

- [x] Implement secrets/PII redaction
  - [x] Regex patterns for API keys, passwords, JWTs, etc.
  - [x] Entropy-based detection for unknown secret formats
  - [x] Email/phone/credit card/SSN redaction
  - [x] Redaction map for secure local rehydration

- [x] Build ground truth code change detector
  - [x] Detect file operations from tool messages
  - [x] Extract code blocks and estimate lines changed
  - [x] Track file_operations, code_blocks, files_changed
  - [x] Augment LLM summaries with real metrics

- [x] Implement reference verification system
  - [x] Verify message IDs exist in conversation
  - [x] Verify message ranges are valid and ordered
  - [x] Verify references fall within claimed ranges
  - [x] Auto-fix summaries with invalid references
  - [x] Extract quotes from referenced messages

- [x] Create flexible summarization pipeline
  - [x] **Single-stage by default** (Sonnet only - simpler, more reliable)
  - [x] **Two-stage optional** (Haiku span detection → Sonnet reasoning)
  - [x] Design decision: Single-stage for compaction use case
    - Compaction happens at 190K → 25-30K (not summarizing 190K for later use)
    - Cost difference negligible (~$0.52 vs ~$0.20 per compaction)
    - Compaction is infrequent (once per few hours of intensive coding)
    - Single-stage is simpler, more reliable, better quality
  - [x] System prompts with hallucination prevention
  - [x] Temperature 0 for determinism

- [x] Implement `generate_summary()` in DevSession
  - [x] Auto-parse conversation if needed
  - [x] Calculate session hash for provenance
  - [x] Redact secrets before LLM call
  - [x] Merge ground truth with LLM output
  - [x] Verify and auto-fix summary references

- [x] Add pin/lock/edit functionality
  - [x] `pin_summary_item()` - Prevent auto-deletion
  - [x] `lock_summary_item()` - Prevent auto-edits
  - [x] Audit trail tracking with timestamps

- [x] Test all summarization features
  - [x] Schema creation and validation
  - [x] Reference verification
  - [x] Secrets redaction
  - [x] Code change detection
  - [x] DevSession integration
  - [x] Pin/lock/audit trail

**Deliverable**: ✅ Production-ready summarization with safety, verification, and audit trails

**Features Added**:
- `summary_schema.py` - Enhanced schema with provenance, IDs, audit trails
- `summary_verification.py` - Reference verification and auto-fixing
- `redaction.py` - Secrets/PII redaction with 10+ pattern types
- `code_change_detector.py` - Ground truth extraction from events
- `summarizer.py` - Two-stage pipeline with hallucination prevention
- `generate_summary()`, `pin_summary_item()`, `lock_summary_item()` in DevSession

**Key Improvements Over Original Plan** (based on GPT-5 Pro analysis):
- ✅ Determinism via JSON schema + stable IDs
- ✅ Reference verification prevents hallucinations
- ✅ Ground truth code changes (not LLM guesses)
- ✅ Secrets redaction before summarization
- ✅ Pin/lock/audit for human-in-the-loop
- ✅ Confidence levels on all items
- ✅ Two-stage process for cost + accuracy
- ✅ Temporal metadata (t_first, t_last)
- ✅ Causal edges ready for graph retrieval

**🚀 Critical Innovation: Two-Level Linked Retrieval**

The breakthrough feature that makes .devsession unique:

**Temporal Index (`message_range`):**
```python
summary_item = {
    "decision": "Use modal dialog",
    "message_range": {
        "start_index": 42,    # O(1) lookup in conversation array
        "end_index": 50,      # conversation[42:50] = full discussion
    },
    "references": ["msg_045", "msg_046"],  # Key moments
    "t_first": "2024-10-26T18:22:12",      # When it started
    "t_last": "2024-10-26T18:28:49"        # When it concluded
}
```

**Retrieval Pattern:**
```python
# Level 1: Fast search on summary (3-5K tokens)
results = vector_search("modal dialog", summary_items)
decision = results[0]

# Level 2: Precise retrieval from full conversation (190K tokens)
start = decision["message_range"]["start_index"]
end = decision["message_range"]["end_index"]
full_discussion = conversation[start:end]  # O(1) array access

# Level 3: LLM reads full context (not lossy summary)
llm.query("Why modal?", context=full_discussion)
```

**Why This Beats Everything:**
- **vs ChatGPT/Claude**: They discard original → lossy, can't verify
- **vs RAG systems**: They chunk full text → slow, fragments discussions
- **vs Keyword search**: Brittle, no semantics, returns noise

**Our Approach:**
- ✅ Fast (search 3-5K summary, not 190K full conversation)
- ✅ Semantic (vector embeddings in Phase 5)
- ✅ Lossless (full conversation preserved, linked via temporal index)
- ✅ Verifiable (can check summary claims against source)
- ✅ Contextual (returns complete discussions, not fragments)

**Implementation:**
- `reccli/retrieval.py` - ContextRetriever with two-level search
- `test_two_level_retrieval.py` - Comprehensive demonstration
- All summary items include `message_range` + `references` + temporal metadata

**This is what ChatGPT/Claude are missing!** 🎯

**Duration**: 2 hours (actual)

---

### Phase 5: Vector Embeddings & Search
**Goal**: Semantic search over sessions with hybrid recall (dense + sparse) and time-aware boosts

#### Advanced Temporal Indexing
Beyond simple recency boosts, .devsession uses **temporal structure as a first-class index**:

**Must-Have Features**:
- **Temporal scopes & joins**: Query specific time intervals (`LAST_48H`, `BETWEEN(t1,t2)`, `AROUND(event,±Δ)`)
- **Time-aware boosts**: `score *= exp(-Δt/τ)` (τ=3 days default) + `1.2×` for same section
- **Hybrid retrieval**: Dense ANN (cosine) ∪ BM25 (text + kind + filenames) with Reciprocal Rank Fusion

**Should-Have Features** (add if time permits):
- **Episode detection**: Heuristic segmentation (bursts, topic shifts, vocabulary change)
- **Time-aware reranker features**: `Δt`, `same_episode`, `near_checkpoint`, `precedes/follows decision`

#### Index Schema
```json
{
  "id": "span_7a1e",
  "session_id": "sess_042",
  "section": "billing-retry",
  "t_start": "2024-10-26T18:22:12",
  "t_end": "2024-10-26T18:28:49",
  "episode_id": 15,
  "start_idx": 42,
  "end_idx": 50,
  "kind": "decision|code|problem|note",
  "text": "Use modal dialog for export",
  "embedding": [0.123, -0.456, ...]
}
```

#### Tasks:
- [ ] Integrate OpenAI embeddings API (text-embedding-3-small)
  - [ ] Batch at 256-512 tokens/chunk for cost efficiency
  - [ ] Chunk summary items first (decisions/problems/next_steps)
  - [ ] Add per-message embeddings only if needed
- [ ] Store embeddings in .devsession vector_index with temporal metadata
- [ ] Implement hybrid retrieval
  - [ ] Dense ANN search (cosine similarity on embeddings)
  - [ ] BM25 sparse search (on text + kind + filenames)
  - [ ] Reciprocal Rank Fusion (RRF) to combine results
- [ ] Add temporal boosts
  - [ ] Exponential decay: `score *= exp(-Δt/τ)` where τ=3 days
  - [ ] Same-section boost: `score *= 1.2` if in current section
  - [ ] Near-decision boost: favor spans near key decisions
- [ ] Implement temporal scopes API
  - [ ] `search(query, time={'lastHours': 48})`
  - [ ] `search(query, time={'between': [t1, t2]})`
  - [ ] `search(query, time={'around': {'event': 'dec_7a1e', 'window_min': 30}})`
- [ ] Create `search(query, k=30, scope={...})` function
  - [ ] Return results with message_range for O(1) expansion
  - [ ] Include badges: recent, same-section, near-decision
- [ ] Add confidence threshold (drop results with cosine <0.25 unless BM25 strong)
- [ ] Cache embeddings (don't regenerate)

#### Acceptance Tests:
- [ ] Query "why modal" returns decision item first; expanding yields exact discussion
- [ ] "yesterday's crash" favors last day's logs over older similar text
- [ ] Results show why (badges: recent, same section, doc/decision)
- [ ] `search("error", time={'around': {'event': 'dec_7a1e', 'window_min': 30}})` returns logs ±30min from decision

**Deliverable**: Hybrid semantic search with temporal awareness and scoped queries

**Definition of Done**: `reccli search "why modal"` returns decision item with badges; "expand 42-50" works

**Duration**: 2-3 days

---

### Phase 6: Memory Middleware (The Magic)
**Goal**: Hydrate LLM prompts from .devsession memory in ~2K tokens reliably

#### This is the breakthrough - replacing 200K tokens with 2K intelligent tokens.

#### The Predictive Stack: Three Complementary Systems

RecCli uses a **three-layer predictive stack** for instant-feeling UX:

```
User opens chat
    ↓
[Layer 3: ML Predictive Retrieval] - "Based on recent events, pre-fetch likely artifacts"
    ↓
User asks question
    ↓
Answer with pre-fetched context (faster!)
    ↓
[Layer 2: Post-Answer Reasoning] - "Spend 100 tokens predicting next question"
    ↓
Pre-fetch context for predicted next question
    ↓
User accepts code edit
    ↓
[Layer 1: Work Package Continuity] - "Generate related files while user was reading"
    ↓
User clicks accept → all files ready instantly
```

**Layer 1: Work Package Continuity (WPC)** - Pre-generating artifacts (Phase 6)
- **What**: Generate multiple files while user reviews first one
- **When**: During code generation (user hasn't clicked accept yet)
- **Example**: Edit `retry.ts` → also generate `test_retry.spec.ts` + `retry_helpers.ts`
- **Goal**: Instant multi-file edits

**Layer 2: Post-Answer Reasoning** - Predict next query (Phase 6)
- **What**: After answering, use extra 100 tokens to predict next query
- **When**: Immediately after providing answer to user
- **Example**: User asks "how does auth work?" → Answer + reasoning "they'll probably ask about session storage next" → pre-fetch that context
- **Goal**: Next question feels instant because context already loaded

**Layer 3: ML Predictive Retrieval** - Artifact prediction model (Phase 11+)
- **What**: Train tiny model on session logs to predict needed artifacts
- **When**: When user opens chat session
- **Input**: Recent K events → **Output**: Probabilities over artifact types (doc, code, error)
- **Example**: See recent test failures → predict 60% chance user will ask about source under test → pre-fetch it
- **Goal**: Context pre-loaded before user even asks
- **Status**: Could-Have feature, train on logs after MVP

#### Work Package Continuity (WPC) - Layer 1 Implementation

**What**: While you're reviewing/typing, RecCli quietly generates the next 1-3 artifacts you'll need.

**Why**: Feels instantaneous when you hit "accept" (like Claude Code).

**Heuristic Predictor** (no ML yet):
1. **Files touched in last 10 min** + neighbors in same folder
2. **Files referenced in next_steps** or latest decision
3. **Recent failing test → source under test**
4. **Doc/spec linked in last 30 min**
5. **Logs around last error (±5m)**

**Signals Used**:
- Recent K events (last 50-100 msgs)
- Active section/episode
- Latest summary "next_steps"
- Cursor file focus or most-mentioned file
- Pending diffs/tool outputs

**Prefetch Queue**:
- Bounded queue (size 3-5) of "likely next artifacts"
- Pre-run search (Phase 5) and stage expansions
- Memory budget ≤900 tokens pre-staged
- Evict LRU when budget exceeded

**UX**:
- Quiet prefetch (runs when idle >1.5s)
- Show "Up next" pills: `retry.ts`, `test_retry.spec.ts`, `payments_api.md`
- Instant injection when user accepts/asks
- Provenance badges: *touched recently, failing test, next step*

**Safety**:
- Don't auto-apply changes (stage only)
- Back off if last 3 predictions rejected
- Respect secrets/PII filters before prefetch
- Never prefetch outside current project/session

#### MemoryMiddleware Design

**`hydrate_prompt(user_input)` flow:**
1. **Pins + recent** (always include): ~700-900 tokens
2. **Summary slice** (top N high-impact decisions/problems): ~600-900 tokens
3. **Relevant history** via search (Phase 5): pick ≤3 spans, ~300-600 tokens
4. **Pre-staged WPC items**: inject artifacts from prefetch queue
5. Emit tools: `EXPAND(range_id)` and `SEARCH(query)` instructions

**Prompt header (compact):**
- Timeline (3-5 lines)
- Current section
- Active TODOs
- Model rules: "Prefer recent evidence unless contradicted by canonical doc"

#### Tasks:
- [ ] Implement `MemoryMiddleware` class
  - [ ] `hydrate_prompt(user_input)` - Build context
  - [ ] Load summary layer (~600-900 tokens)
  - [ ] Get recent messages (~700-900 tokens)
  - [ ] Vector search relevant history (~300-600 tokens)
  - [ ] Construct structured prompt with EXPAND/SEARCH tools
  - [ ] Token budget enforcement (soft cap 1.6-1.8K; hard cap 2K)

- [ ] Implement Work Package Continuity (WPC) - Layer 1
  - [ ] `predictNext(signal)` - Heuristic predictor
  - [ ] `prefetch(items, budgetTokens=900)` - Pre-retrieve spans
  - [ ] `useStagedContext(staged)` - Inject into hydrate_prompt
  - [ ] Prefetch queue with LRU eviction
  - [ ] Idle detection (trigger after 1.5s no input)
  - [ ] Adaptive cooldown (back off if predictions rejected)

- [ ] Implement Post-Answer Reasoning - Layer 2
  - [ ] After generating answer, append reasoning prompt: "What will user likely ask next?"
  - [ ] Budget: 100 tokens for prediction reasoning
  - [ ] Parse prediction → identify likely artifacts/context needed
  - [ ] Pre-fetch predicted context (via Phase 5 search)
  - [ ] Store in prefetch queue alongside WPC items
  - [ ] Track prediction accuracy for learning

- [ ] Add time-aware reranker features (from Phase 5)
  - [ ] Append `[Δt:2h][episode:15][near:CP_12]` to chunks
  - [ ] Favor current episode in scoring

- [ ] Test hydration with various scenarios
- [ ] Benchmark token usage vs raw context

#### Acceptance Tests:
- [ ] Continuing session after 24h: model proposes same pinned Next Steps
- [ ] Asking "why did we switch X?" returns decision + citations + EXPAND option
- [ ] Token budget respected (soft cap 1.6-1.8K; hard cap 2K)
- [ ] **Layer 1 (WPC)**: After editing `retry.ts`, WPC prefetches `test_retry.spec.ts` + `retry_helpers.ts`
- [ ] **Layer 1 (WPC)**: When test fails, WPC stages failing test + source under test in "Up next"
- [ ] **Layer 1 (WPC)**: If 3 predictions in row unused, WPC halves prefetch frequency for 10 min
- [ ] **Layer 2 (Post-Answer)**: After answering "how does auth work?", next question about sessions feels instant (context pre-fetched)
- [ ] **Layer 2 (Post-Answer)**: Prediction reasoning budget stays ≤100 tokens

**Deliverable**: Working context hydration from .devsession + predictive retrieval

**Definition of Done**: `reccli chat --session s.devsession` answers "what next?" correctly after a day

**Duration**: 3-4 days

---

### Phase 7: Preemptive Compaction
**Goal**: Auto-compact at ~90-95% of window (190K tokens) and keep working seamlessly

#### Triggering & Flow
Watch `token_counts`. When ≥ 180K (warn) / 190K (compact):

1. **Generate fresh summary** (Phase 4 single-stage Sonnet)
2. **Extract last K messages** (implicit goal - what we're working on now)
3. **Use Phase 5 search** to pull ≤3 key spans for continuity
4. **Replace live context** with: Summary (3-5K) + Recent (20K) + Key spans (~2K)
5. **Log compaction event** into .devsession file
6. **Use WPC predictions** to include likely-next spans so conversation flows

**Result**: ~25-30K tokens total (leaves 170K headroom to continue)

#### Manual Checkpoints
- [ ] Implement `reccli checkpoint add "CP_12 - pre-release"` command
- [ ] Store checkpoint: `{id, t, label, criteria}`
- [ ] Support "show changes since CP_x" queries
- [ ] Include checkpoint metadata in compaction events

#### Episode Continuity
- [ ] Heuristic episode detection (bursts, topic shifts, file set changes)
- [ ] Assign episode_id to summary items
- [ ] Favor current episode when selecting key spans post-compaction
- [ ] Display current episode in "Up next" context

#### Tasks:
- [ ] Implement compaction trigger logic
  - [ ] Monitor token_counts (warn at 180K, compact at 190K)
  - [ ] Auto-trigger or manual via `reccli compact <file>`
- [ ] Generate fresh summary from ALL events (Phase 4)
- [ ] Extract recent N messages as implicit goal (~20K tokens)
- [ ] Vector search earlier events using recent as query (≤3 spans)
- [ ] Use WPC predictions to add likely-next artifacts
- [ ] Persist compaction event to .devsession history
  - [ ] Store: timestamp, tokens_before, tokens_after, summary_id, retained_spans
- [ ] Reset context to ~25-30K tokens
- [ ] Continue seamlessly without user intervention
- [ ] Implement manual checkpoints
  - [ ] CLI command `reccli checkpoint add <label>`
  - [ ] Query "since CP_x" → list spans & code changes
- [ ] Episode detection heuristic
  - [ ] Detect bursts (surge in errors)
  - [ ] Detect new file set
  - [ ] Detect vocabulary shift
  - [ ] Assign episode_id to spans

#### Safety
- [ ] Always preserve: pins, tool/file events, last K messages
- [ ] UI/CLI message: "Context compacted → 2.1K tokens; expand any span on demand"
- [ ] No hard-limit errors - model keeps responding

#### Acceptance Tests:
- [ ] After compaction, asking about just-resolved bug retrieves span instantly
- [ ] No hard-limit errors; model continues responding normally
- [ ] "What changed since CP_12?" lists spans & code changes in chronological order
- [ ] After compaction, "what next?" pulls from current episode first
- [ ] Compaction log shows tokens_before=190K, tokens_after=28K

**Deliverable**: Automatic compaction with zero context loss + manual checkpoints + episode awareness

**Definition of Done**: Context hits 190K → auto-compacts → chat continues without error

**Duration**: 2-3 days

---

### Phase 8: LLM Adapters
**Goal**: Pluggable providers with deterministic JSON/tool outputs

#### Interface
```python
llm.generate(
    messages,
    tools=None,          # Tool calling support
    schema=None,         # JSON schema for structured output
    model_id="claude-3-5-sonnet-20241022",
    temperature=0.0      # Determinism
)
```

#### Common Features
- JSON/tool calling support (for summaries, structured outputs)
- Temperature 0-0.2 for determinism
- Streaming support
- Token counting
- Cost tracking

#### Tasks:
- [ ] Create base LLM adapter interface
  - [ ] `generate()` method with consistent signature
  - [ ] JSON schema enforcement
  - [ ] Tool/function calling abstraction
  - [ ] Streaming interface
- [ ] Implement Claude adapter (Anthropic API)
  - [ ] Message format conversion
  - [ ] Tool calling via Anthropic's format
  - [ ] JSON mode via system prompt
- [ ] Implement OpenAI adapter (GPT-4/GPT-5)
  - [ ] Message format conversion
  - [ ] Function calling via OpenAI's format
  - [ ] JSON mode via response_format
- [ ] Implement Ollama adapter (local models)
  - [ ] Graceful degradation (no tools → pure text)
  - [ ] Local JSON parsing fallback
- [ ] Add model selection in CLI (`--model claude|openai|ollama`)
- [ ] Store provenance: `{model, model_version, created_at}` on summaries

#### Acceptance Tests:
- [ ] Swap Claude↔OpenAI with `--model` flag; summaries & hydration succeed
- [ ] Ollama small model: graceful degradation (no tools → pure text)
- [ ] All adapters respect temperature=0 for deterministic output

**Deliverable**: Works with any LLM provider

**Definition of Done**: `--model openai` works with same flows as `--model claude`

**Duration**: 2 days

---

### Phase 9: CLI Commands (User Interface)
**Goal**: Happy-path UX with zero-config defaults

#### Core Commands:
```bash
# Recording
reccli start [name]           # Start recording session
reccli stop                   # Stop and finalize

# Chat (with memory)
reccli chat --session <file> --model <provider>

# Session management
reccli list                   # Show all sessions
reccli resume <file>          # Load session context
reccli summarize <file>       # Generate/update summary
reccli search "query"         # Hybrid search with badges + expand IDs
reccli expand <range_id>      # Expand summary item to full context

# Compaction
reccli compact <file>         # Manual compaction
reccli check-tokens <file>    # Show token count

# Checkpoints
reccli checkpoint add <label> # Create manual checkpoint
reccli checkpoint list        # Show checkpoints
reccli diff-since <checkpoint> # Show changes since checkpoint

# Project
reccli project init           # Create .devproject
reccli project status         # Show overview (roll-up decisions/issues)
```

#### Polish
- Progress spinners for long ops (embed, summarize)
- Pretty "why this result" output with badges:
  - `[RECENT]` - within last 48h
  - `[SAME-SECTION]` - current section
  - `[NEAR-DECISION]` - near key decision
  - `[FAILING-TEST]` - related to test failure
- Color-coded output (decisions=green, problems=red, next_steps=blue)
- Token counts and cost estimates in progress messages
- "Up next" pills showing WPC predictions

#### Tasks:
- [ ] Implement CLI using `click` or `typer`
  - [ ] All core commands with proper argument parsing
  - [ ] `--help` text and usage examples
  - [ ] Model selection (`--model claude|openai|ollama`)
  - [ ] Debug mode (`--debug` for verbose logging)
- [ ] Add search command with badges
  - [ ] Display relevance score
  - [ ] Show why result matched (badges)
  - [ ] Provide expand IDs for drill-down
- [ ] Add expand command
  - [ ] Retrieve full context for summary item
  - [ ] Display core range + ±N context messages
  - [ ] Mark which messages are in core vs context
- [ ] Add checkpoint commands
  - [ ] Create checkpoints with labels
  - [ ] List all checkpoints with timestamps
  - [ ] Diff since checkpoint (show spans + code changes)
- [ ] Error handling and validation
  - [ ] File not found → helpful message
  - [ ] Invalid .devsession → schema errors
  - [ ] API errors → retry logic + clear messages
- [ ] Progress indicators for long operations
  - [ ] Spinner for embedding generation
  - [ ] Progress bar for batch operations
  - [ ] "Compacting... 190K → 28K tokens (saving $0.45)" messages
- [ ] Pretty output formatting
  - [ ] Color-coded by item type
  - [ ] Tables for search results
  - [ ] Tree view for lineage queries
  - [ ] Compact headers with key metadata

#### Acceptance Tests:
- [ ] New user can record → summarize → chat with memory in <5 minutes
- [ ] `reccli search "modal"` shows badges and expand IDs
- [ ] `reccli expand dec_7a1e` shows full discussion with context
- [ ] Help text is clear and includes examples
- [ ] All commands work without reading docs

**Deliverable**: Full CLI interface with polished UX

**Definition of Done**: Fresh install → record → summarize → chat in <5 minutes, no docs needed

**Duration**: 3 days

---

### Phase 10: .devproject (Project Layer)
**Goal**: Cross-session project memory with causal edges and lineage

#### Schema (Lean)
```json
{
  "project": {
    "name": "RecCli",
    "stack": ["python", "vite", "anthropic"],
    "created_at": "2025-11-01T10:00:00Z"
  },
  "sessions": [
    {
      "id": "sess_042",
      "path": "./session_042.devsession",
      "t_first": "2025-11-01T10:00:00Z",
      "t_last": "2025-11-01T18:30:00Z",
      "highlights": ["dec_7a1e", "code_3f2b"]
    }
  ],
  "key_decisions": [
    {
      "id": "dec_7a1e",
      "from_session": "sess_042",
      "summary": "Use modal dialog for export",
      "t": "2025-11-01T14:22:00Z"
    }
  ],
  "open_issues": [
    {
      "id": "issue_9c4d",
      "from_session": "sess_042",
      "severity": "high",
      "description": "Memory leak in recorder"
    }
  ],
  "checkpoints": [
    {
      "id": "CP_12",
      "t": "2025-11-01T18:00:00Z",
      "label": "pre-release",
      "criteria": "all tests passing"
    }
  ],
  "causal_edges": [
    {
      "from_id": "dec_7a1e",
      "to_id": "code_3f2b",
      "rel": "derived_from",
      "t_edge": "2025-11-01T14:30:00Z"
    }
  ]
}
```

#### Causal Reasoning Graph
Build DAG of decisions/problems/code-changes with edges:
- `supports` - decision supports implementation
- `derived_from` - code derived from decision
- `blocked_by` - issue blocks progress
- `resolved_by` - fix resolves issue

Edges enable:
- "Why did we do X?" → returns decision chain with timestamps + citations
- Retrieval = hop graph first, then pull text spans
- Provenance queries

#### Lineage Queries
Since everything is timestamped, expose lineage:
```bash
reccli lineage billing/retry.ts --since CP_9
# Shows chain of changes/decisions/problems in chronological order
```

Great for audits and onboarding.

#### Commands
```bash
reccli project init               # Scan folder; attach sessions
reccli project status             # Roll-up: key decisions/open issues
reccli project search "query"     # Cross-session search
reccli explain-decision <id>      # Show causal chain with times + refs
reccli lineage <file> --since <cp> # File history since checkpoint
```

#### Tasks:
- [ ] Define .devproject schema with causal edges
  - [ ] Project metadata (name, stack, created_at)
  - [ ] Session list with highlights
  - [ ] Cross-session key_decisions rollup
  - [ ] Cross-session open_issues rollup
  - [ ] Checkpoints list
  - [ ] Causal edges graph
- [ ] Implement project commands
  - [ ] `project init` - Scan folder for .devsession files
  - [ ] `project status` - Show rollup of decisions/issues
  - [ ] `project search` - Search across all sessions
  - [ ] `explain-decision` - Show causal chain
  - [ ] `lineage` - File/feature history
- [ ] Auto-update project from sessions
  - [ ] Extract highlights when adding session
  - [ ] Update key_decisions rollup
  - [ ] Update open_issues list
  - [ ] Derive causal edges from summary references
- [ ] Load project context for new sessions
  - [ ] Include project overview in initial prompt
  - [ ] Reference key decisions from earlier sessions
  - [ ] Flag open issues in context
- [ ] Causal edge extraction
  - [ ] Parse summary item references
  - [ ] Infer edges: decision→code, problem→fix, decision→decision
  - [ ] Store as `{from_id, to_id, rel, t_edge}`
  - [ ] Build graph for traversal

#### Acceptance Tests:
- [ ] Search over project returns hits from older sessions; expand works via `message_range`
- [ ] `project status` shows key decisions and open issues from all sessions
- [ ] `explain-decision dec_7a1e` shows causal chain: prior decisions → this decision → implementations
- [ ] `lineage billing/retry.ts --since CP_9` lists chain of changes in order
- [ ] New session automatically includes project context in initial prompt

**Deliverable**: Multi-session project awareness with causal reasoning

**Definition of Done**: `reccli project status` shows cross-session decisions/open issues

**Duration**: 2-3 days

---

### Phase 11: Testing & Benchmarking
**Goal**: Prove .devsession beats "raw long context" and plain RAG

#### Benchmark Suite

**1. Continuity Test** (5-session refactor)
- Success metric: Correct file touched + correct rationale
- Compare context awareness across session boundaries
- Measure: Accuracy, tokens per task, TTFU (time to first useful answer)

**2. Decision Recall** (Why questions)
- Query: "Why choose modal?"
- Should return: decision + span + quote
- Measure: Accuracy, retrieval time, token count

**3. Token Efficiency**
- Tokens per solved task
- Context size over time
- Cost per session

**4. Latency (TTFU)**
- Time to first useful answer
- Search latency
- Expansion overhead

**5. Time-Sensitive Tasks** (prove temporal advantage)
- **Root cause since checkpoint**: "What broke billing since CP_12?"
- **Resume after 3 days**: Load session, continue correctly
- **Policy override detection**: Flag when decision B contradicts A

#### Comparison Matrix

| Metric | Raw 200K | Chunked RAG | .devsession |
|--------|----------|-------------|-------------|
| Tokens/task | 200K | 50K | 2-5K |
| TTFU | Slow | Medium | Fast |
| Accuracy | Baseline | -10% | +5% |
| Cost/session | $0.60 | $0.15 | $0.01 |
| Temporal queries | ❌ | ❌ | ✅ |
| Lossless retrieval | ❌ | ❌ | ✅ |

**Target Outcomes**:
- ≥3× lower tokens than RAG
- ≥2× faster TTFU
- Equal or better accuracy
- Only system with temporal reasoning

#### Tasks:
- [ ] Create benchmark suite
  - [ ] 5-session continuity scenario (real refactor)
  - [ ] 20 "why?" decision recall queries
  - [ ] 10 time-sensitive queries (since checkpoint, resume, contradictions)
  - [ ] Token tracking across all methods
- [ ] Implement comparison systems
  - [ ] Raw context baseline (dump all 200K)
  - [ ] Standard chunked RAG (no summary layer)
  - [ ] .devsession (summary + expand)
- [ ] Run comparative tests
  - [ ] Same prompts across all 3 systems
  - [ ] Blind evaluation of quality
  - [ ] Measure tokens, cost, latency
- [ ] Document results
  - [ ] Accuracy by task type
  - [ ] Token efficiency charts
  - [ ] Cost comparison
  - [ ] TTFU distribution
  - [ ] Temporal query success rate (only .devsession can do this)
- [ ] Create performance charts
  - [ ] Bar chart: tokens per task
  - [ ] Line chart: accuracy over session count
  - [ ] Table: feature comparison matrix

**Deliverable**: Benchmark report with graphs proving .devsession wins on tokens/latency/accuracy

**Definition of Done**: Benchmark report shows .devsession wins on tokens, latency, and accuracy

**Duration**: 3-4 days

---

### Phase 12: Documentation & Examples
**Goal**: Unblocked adoption with clear docs and working examples

#### Deliverables

**1. 5-Minute Quickstart** (with GIFs)
- Install → record → summarize → chat workflow
- Copy-paste commands that just work
- Visual examples of search results with badges
- "Up next" WPC predictions demo

**2. "How Compaction Works" Page**
- Visual diagram of 190K → 28K flow
- Explanation of what's kept vs summarized
- Post-compaction retrieval examples
- Cost savings breakdown

**3. Example .devsession Files**
- `simple.devsession` - Small 20-message example
- `full-session.devsession` - Complete 200K session with summary
- Annotated to explain structure

**4. API Documentation**
- `MemoryMiddleware.hydrate_prompt()`
- `ContextRetriever.two_level_search()`
- `SessionSummarizer.generate()`
- LLM adapter interface
- WPC predictor API

**5. Architecture Docs**
- Two-level linked retrieval explanation
- Temporal indexing design
- Work Package Continuity flow
- Causal reasoning graph
- Schema evolution strategy

**6. Format Spec**
- `.devsession` JSON schema with examples
- `.devproject` JSON schema
- Summary schema v1.1
- Vector index format
- Checkpoint format

#### Tasks:
- [ ] Update README with examples
  - [ ] Installation instructions
  - [ ] Quick start commands
  - [ ] Feature highlights with badges
  - [ ] Link to full docs
- [ ] Write quickstart guide (5 min to wow)
  - [ ] Step-by-step with screenshots/GIFs
  - [ ] Record → summarize → chat workflow
  - [ ] Search and expand examples
  - [ ] WPC predictions demo
- [ ] Create example .devsession files
  - [ ] Small example (annotated)
  - [ ] Full example (real session)
  - [ ] Include all layers (events, conversation, summary, vector_index)
- [ ] Document .devsession format spec
  - [ ] JSON schema with field descriptions
  - [ ] Required vs optional fields
  - [ ] Versioning strategy
  - [ ] Migration guide
- [ ] Write architecture documentation
  - [ ] High-level overview diagram
  - [ ] Two-level retrieval deep-dive
  - [ ] Temporal indexing design
  - [ ] WPC flow chart
  - [ ] Compaction process
  - [ ] Causal graph structure
- [ ] Add API reference
  - [ ] All public classes and methods
  - [ ] Parameter descriptions
  - [ ] Return value formats
  - [ ] Usage examples for each API
- [ ] Create video demo (optional)
  - [ ] 2-minute overview
  - [ ] Real coding session with compaction
  - [ ] Search/expand demonstration

**Deliverable**: Complete documentation + examples published

**Definition of Done**: README + Quickstart + Examples published; copy-paste commands work

**Duration**: 2 days

---

## 🚀 Phase 11+: Advanced Features (Could-Have)

These features enhance the system but are not required for MVP. Implement after Phases 0-12 are complete.

### ML Predictive Retrieval (Layer 3 of Predictive Stack)

**Goal**: Train a tiny next-step model on session logs to predict needed artifacts before user asks

**Why**: Pre-loads context before user even opens chat, feels magical

#### Model Design

**Input Features** (from recent K events):
- File tokens (bag of words from recent files)
- Action verbs (edit, test, debug, refactor, etc.)
- Test failure indicators (which tests failed recently)
- Summary tags (decisions, problems, next_steps)
- Episode ID (current work phase)
- Time since last activity
- Error keywords

**Output**:
- Probabilities over **artifact types**: `{doc: 0.3, code: 0.5, test: 0.15, error: 0.05}`
- Top-k file candidates with confidence scores
- Predicted next action type

**Training Data**:
- Your own .devsession logs (implicit labels from user actions)
- What user opened/expanded next after each state
- Timestamped sequence of artifacts accessed

**Model Architecture**:
- Tiny classifier (few hours of data is enough)
- Simple feedforward or small transformer
- Fast inference (<50ms)
- Regularly retrained on new sessions

#### Implementation

```python
class MLPredictor:
    def __init__(self, model_path=None):
        self.model = load_model(model_path) if model_path else None
        self.fallback_heuristic = HeuristicPredictor()  # Layer 1 fallback

    def predict_next_artifacts(self, recent_events, top_k=5):
        """
        Predict what artifacts user will need next

        Returns:
            [
                {"type": "code", "path": "retry.ts", "confidence": 0.85, "reason": "recent_test_failure"},
                {"type": "test", "path": "test_retry.spec.ts", "confidence": 0.72, "reason": "adjacent_to_code"},
                ...
            ]
        """
        if not self.model:
            return self.fallback_heuristic.predict(recent_events)

        features = self.extract_features(recent_events)
        predictions = self.model.predict(features)

        # Blend with heuristic for robustness
        heuristic_preds = self.fallback_heuristic.predict(recent_events)
        return self.blend_predictions(predictions, heuristic_preds, alpha=0.7)

    def collect_training_example(self, state, user_action):
        """Implicit labeling: what did user do next?"""
        self.training_buffer.append({
            "features": self.extract_features(state),
            "label": user_action  # what they actually opened/asked
        })

    def retrain(self, min_examples=100):
        """Retrain on accumulated session logs"""
        if len(self.training_buffer) < min_examples:
            return

        X, y = self.prepare_training_data(self.training_buffer)
        self.model.fit(X, y)
        self.save_model()
```

#### Integration Points

**On session open**:
```python
# Predict what user will need
predictions = ml_predictor.predict_next_artifacts(recent_events, top_k=5)

# Pre-fetch top predictions
for pred in predictions[:3]:
    if pred["confidence"] > 0.6:
        prefetch_queue.add(pred, reason=pred["reason"])
```

**After each user action**:
```python
# Collect training data
ml_predictor.collect_training_example(
    state=current_state,
    user_action=what_user_just_did
)

# Periodic retraining
if session_count % 10 == 0:
    ml_predictor.retrain()
```

#### Tasks:
- [ ] Design feature extraction from recent events
- [ ] Implement training data collection (implicit labels from user actions)
- [ ] Train initial tiny classifier on your own logs
- [ ] Implement prediction blending (ML + heuristic)
- [ ] Add periodic retraining pipeline
- [ ] Measure prediction accuracy (hit rate ≥60%)
- [ ] A/B test: ML predictor vs heuristic-only

#### Acceptance Tests:
- [ ] Prefetch hit rate ≥60% (predicted artifact was used within next 3 queries)
- [ ] Inference latency <50ms
- [ ] Model retrains automatically every 10 sessions
- [ ] Graceful fallback to heuristic if model unavailable
- [ ] Blended predictions outperform heuristic-only baseline

**Deliverable**: ML-powered predictive retrieval with automatic retraining

**Duration**: 1-2 weeks (after MVP complete)

---

## 🎯 Advanced Temporal Features - Priority Matrix

### What We're Building vs What Can Wait

The user provided extensive context about advanced temporal indexing capabilities. Here's what's **must-have** vs **nice-to-have**:

| Feature | Priority | Phase | Why Now / Why Later |
|---------|----------|-------|---------------------|
| **Temporal boosts** (`exp(-Δt/τ)`, same-section) | **MUST** | P5 | Immediate relevance/TTFU gains |
| **Temporal scopes** (`LAST_48H`, `BETWEEN`, `AROUND`) | **MUST** | P5 | First-class time filtering |
| **Time-aware reranker** (Δt, episode, checkpoint) | **SHOULD** | P6 | Precision bump, low cost |
| **Episode detection** (heuristic) | **SHOULD** | P6-P7 | Stabilizes "resume work" |
| **Manual checkpoints** | **SHOULD** | P7 | Makes "what changed since X?" trivial |
| **Causal edges** (supports/derived_from/blocks) | **SHOULD** | P10 | Enables "why?" answers |
| **Lineage queries** (file/feature history) | **SHOULD** | P10 | Killer onboarding/audit feature |
| **Time-sensitive benchmarks** | **MUST** | P11 | Prove advantage vs vector-only |
| **Delta vectors** (before/after embeddings) | **COULD** | P11+ | Nice for "what changed", not MVP |
| **Layer 1: Work Package Continuity** (pre-gen artifacts) | **MUST** | P6 | Magic UX feel (instant multi-file) |
| **Layer 2: Post-Answer Reasoning** (predict next query) | **SHOULD** | P6 | Next question feels instant |
| **Layer 3: ML Predictive Retrieval** (artifact prediction) | **COULD** | P11+ | Magic feel; train on logs after MVP |
| **Contradiction checks** (policy overrides) | **COULD** | P10-P11 | Governance layer, not MVP |

### Minimal Path to "Wow"
- **P5**: Scopes + boosts (feels next-gen vs plain vector)
- **P6**: Reranker features + episode heuristic + **Predictive Stack Layers 1 & 2** (magic UX)
  - Layer 1: Work Package Continuity (pre-generate artifacts)
  - Layer 2: Post-Answer Reasoning (predict next query)
- **P7**: Manual checkpoints + "since CP_x" diffs
- **P10**: Causal edges + explain/lineage commands
- **P11**: Time-sensitive benchmarks proving temporal advantage
- **P11+**: Layer 3 ML Predictive Retrieval (train on logs after MVP)

### Cross-Cutting Upgrades (Bake In Now)
- **JSON schema/tool-calling** for summaries (already in Phase 4 ✅)
- **Provenance everywhere**: `{model, model_version, created_at}` on summary/search indices
- **Secrets/PII guard** before any LLM call (already in Phase 4 ✅)
- **Confidence flags** on summary items → surface low-confidence for review

### Temporal Structure is the Differentiator

> **User insight**: "Temporal boosts help; temporal structure differentiates."

Use `.devsession` to model:
- **Episodes** (heuristic segmentation by bursts/topic shifts)
- **Checkpoints** (manual markers at meaningful moments)
- **Causality** (decision→code→problem→fix chains)
- **Lineage** (file/feature evolution over time)

This turns RecCli from "better vector search" into a **time-aware reasoning memory** - something others can't fake with a decay factor.

---

## 🗓️ Timeline

| Phase | Duration | Cumulative | Status |
|-------|----------|------------|--------|
| 0. Terminal Recording | 1 day | 1 day | ✅ COMPLETE |
| 0.5. Native LLM + GUI | 1 day | 2 days | ✅ COMPLETE |
| 1. File Management | 1 hour | 2 days | ✅ COMPLETE |
| 2. Conversation Parsing | 2 hours | 2 days | ✅ COMPLETE |
| 3. Token Counting | 1 hour | 2 days | ✅ COMPLETE |
| 4. Summary Generation | 2 hours | 2 days | ✅ COMPLETE |
| 5. Vector Embeddings + Temporal | 2-3 days | 4-5 days | ⏳ NEXT |
| 6. Memory Middleware + WPC | 3-4 days | 7-9 days | ⏸️ |
| 7. Preemptive Compaction + Episodes | 2-3 days | 9-12 days | ⏸️ |
| 8. LLM Adapters | 2 days | 11-14 days | ⏸️ |
| 9. CLI Commands | 3 days | 14-17 days | ⏸️ |
| 10. .devproject + Causal Edges | 2-3 days | 16-20 days | ⏸️ |
| 11. Testing & Benchmarking | 3-4 days | 19-24 days | ⏸️ |
| 12. Documentation | 2 days | 21-26 days | ⏸️ |

**Total Estimated Duration**: ~21-26 days (3-4 weeks)

**MVP Target** (Phases 0-9): ~14-17 days

**Phases 0-4 Complete**: ✅ 2 days (actual)
**Remaining to MVP**: ~12-15 days

---

## 🏗️ Repository Structure

```
RecCli/
├── reccli/
│   ├── __init__.py
│   ├── cli.py                 # CLI entry point (Phase 9)
│   ├── recorder.py            # Modified asciinema recorder (Phase 0)
│   ├── devsession.py          # .devsession file manager (Phase 1)
│   ├── parser.py              # Conversation parser (Phase 2)
│   ├── tokens.py              # Token counting (Phase 3)
│   ├── summarizer.py          # Summary generation (Phase 4)
│   ├── embeddings.py          # Vector embeddings (Phase 5)
│   ├── memory.py              # Memory middleware (Phase 6)
│   ├── compaction.py          # Preemptive compaction (Phase 7)
│   ├── llm_adapters/          # LLM provider adapters (Phase 8)
│   │   ├── base.py
│   │   ├── claude.py
│   │   ├── openai.py
│   │   └── ollama.py
│   └── devproject.py          # Project management (Phase 10)
│
├── tests/
│   ├── test_recorder.py
│   ├── test_devsession.py
│   ├── test_parser.py
│   ├── test_memory.py
│   └── benchmarks/
│
├── devsession/                # Documentation & specs
│   ├── README.md
│   ├── DEVSESSION_FORMAT.md
│   ├── ARCHITECTURE.md
│   ├── CONTEXT_LOADING.md
│   ├── examples/
│   │   ├── simple.devsession
│   │   └── full-session.devsession
│   └── schemas/
│       ├── devsession.schema.json
│       └── devproject.schema.json
│
├── examples/
│   ├── quickstart/
│   └── benchmarks/
│
├── setup.py
├── requirements.txt
├── PROJECT_PLAN.md           # This file
└── README.md
```

---

## 🎯 Success Criteria

### Technical Success
- [ ] Can record terminal sessions to .devsession format
- [ ] Can parse LLM conversations from terminal events
- [ ] Can generate intelligent summaries
- [ ] Can search conversation history semantically
- [ ] Can hydrate LLM context from summary + vectors
- [ ] Can auto-compact at 190K tokens
- [ ] Achieves 99% token reduction with equal/better quality

### Product Success
- [ ] Works with Claude, GPT-4, and local models
- [ ] Survives compaction without losing important context
- [ ] Users can resume sessions days/weeks later
- [ ] Benchmark shows measurable improvements
- [ ] Clean CLI that "just works"

### Community Success
- [ ] Open source on GitHub
- [ ] Clear documentation
- [ ] Working examples
- [ ] Positive feedback from early users
- [ ] Becomes reference implementation for .devsession format

---

## 🚧 Current Blockers & Decisions

### ✅ Resolved:
- Terminal recording approach: Modified asciinema
- File format: .devsession (not .cast or dual files)
- Compaction trigger: 190K tokens (preemptive)
- Context hydration: Summary + recent + vector search

### ⏳ Need to Resolve:
- [ ] Exact asciinema modification approach (fork vs wrapper?)
- [ ] Which LLM API for summarization? (Anthropic Claude recommended)
- [ ] Embedding provider? (OpenAI text-embedding-3-small recommended)
- [ ] CLI library? (typer recommended for modern Python CLIs)
- [ ] How to detect different LLM CLI tools in terminal output?

---

## 📊 Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Asciinema modification too complex | High | Fall back to custom PTY recorder (Option B) |
| Summary quality insufficient | High | Iterate on prompt, allow manual editing |
| Vector search retrieves wrong context | Medium | Add relevance threshold, allow manual pins |
| Token counting inaccurate | Medium | Use official tokenizers (tiktoken) |
| LLM providers change APIs | Low | Abstract behind adapter interface |
| .devsession files too large | Low | Add compression, archive old sections |

---

## 🎉 MVP Definition (Minimum Viable Product)

**What can we ship in 2 weeks?**

### MVP Features:
1. ✅ Record terminal sessions to .devsession
2. ✅ Parse conversation (basic - detect user input vs LLM output)
3. ✅ Generate summary (using Claude API)
4. ✅ Load session and resume with context
5. ✅ Basic CLI commands (start, stop, chat, summarize)
6. ✅ Works with Claude Code

### MVP Excludes (v2.0):
- Vector embeddings (use simple keyword search instead)
- Multiple LLM providers (Claude only)
- .devproject layer
- Advanced compaction
- Benchmarks

**MVP Timeline**: 14 days

---

## 🔄 Development Workflow

### Daily:
1. Update PROJECT_PLAN.md with progress
2. Commit working code to Git
3. Test manually with real terminal sessions
4. Document learnings

### Weekly:
1. Review phase completion
2. Update timeline estimates
3. Test end-to-end workflow
4. Demo to early testers

### Monthly:
1. Release version (0.1, 0.2, etc.)
2. Write changelog
3. Update documentation
4. Gather feedback

---

## 📝 Next Steps (Immediate)

### This Week:
1. **Phase 0**: Study asciinema source code
2. Identify modification points for .devsession output
3. Create proof-of-concept: terminal recording → .devsession
4. Test basic playback compatibility

### Action Items (Right Now):
- [ ] Clone asciinema repository
- [ ] Study their recorder.py and writer.py modules
- [ ] Document how they capture PTY events
- [ ] Design .devsession writer integration point
- [ ] Create branch: `feature/devsession-recorder`

---

## 💡 Future Vision (Beyond MVP)

### v2.0 Features:
- VS Code extension (inline .devsession viewer)
- Web viewer for .devsession files
- Team collaboration (shared sessions)
- Session branching (fork conversations)
- Diff between sessions
- Export to other formats (markdown, HTML)

### v3.0 Features:
- Claude Code native integration
- Real-time collaborative sessions
- Session analytics (productivity insights)
- AI-suggested next steps
- Automatic code documentation from sessions

---

## 📚 Resources & References

### Technical:
- [asciinema GitHub](https://github.com/asciinema/asciinema)
- [Python pty module](https://docs.python.org/3/library/pty.html)
- [OpenAI Embeddings API](https://platform.openai.com/docs/guides/embeddings)
- [Anthropic Claude API](https://docs.anthropic.com/claude/reference)
- [tiktoken (token counting)](https://github.com/openai/tiktoken)

### Inspiration:
- Git (version control for code)
- Jupyter notebooks (.ipynb format)
- Event sourcing architecture
- Conversation context in LLMs

---

**Last Updated**: 2025-11-01
**Next Review**: After Phase 0 completion
**Maintained By**: RecCli Team
