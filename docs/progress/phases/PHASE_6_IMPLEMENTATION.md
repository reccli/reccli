# Phase 6: Memory Middleware - Implementation

**Goal**: Hydrate LLM prompts from .devsession memory in ~2K tokens reliably

**Duration**: 3-4 days

**Status**: 🟡 Ready to implement

---

## Overview

Phase 6 implements the **Memory Middleware** - the breakthrough that replaces 200K tokens with 2K intelligent tokens. This is the core innovation that makes RecCli's context management superior to raw conversation history.

### The Core Problem

When compacting a long session (approaching 180K+ tokens), what context should remain?

**Constraints:**
- Can't keep full 180K tokens (at limit)
- Summary alone might miss critical details
- Need relevance to what user is currently working on
- Must maintain conversation continuity

**Goal:** Compact 180K tokens → 2K tokens with the RIGHT context to continue seamlessly.

---

## Architecture: Three-Layer Context Loading

### Layer 1: Project Overview (Macro)
**Source:** `.devproject` file at project root
**Size:** ~300-500 tokens
**Contains:** Project name, purpose, architecture, key decisions, tech stack, milestones
**Updated:** Each session end
**Loaded:** Conditionally (see Conditional Loading section)

**Purpose:** Keeps LLM grounded in "what is this project?" - the macro perspective.

### Layer 2: Session Summary (This Session)
**Source:** `.devsession` file's summary object
**Size:** ~500-1000 tokens
**Contains:** Session goal, decisions made, code changes, problems solved
**Updated:** Session end or compaction
**Loaded:** Always

**Purpose:** Answers "what happened in this session?" - the current work context.

### Layer 3: Full Conversation (Micro)
**Source:** `.devsession` file's conversation array with embeddings
**Size:** Full session (50K+ tokens), but only subset loaded via vector search
**Contains:** Every message with vector embeddings
**Updated:** Real-time during session
**Loaded:** Selectively via vector search (~700-1000 tokens worth)

**Purpose:** Provides "how did we do it?" - the implementation details.

---

## The Key Insight: Implicit Goal from Recent Messages

**Key Insight:** The user's "current goal" is implicit in their recent messages. Use those as the vector search query.

### The Flow:

```
BEFORE COMPACTION (180K tokens):
├─ [Messages 1-180]: Historical conversation
└─ [Messages 181-200]: Current work ← This IS the goal

COMPACTION PROCESS:
1. Extract recent messages (last 20)
2. Embed them as query vector
3. Search earlier messages for semantic similarity
4. Generate summary of full session

AFTER COMPACTION (2K tokens):
├─ Summary (500 tokens): What happened overall
├─ Recent messages (500 tokens): What we're doing RIGHT NOW
└─ Vector matches (1000 tokens): Related earlier context
```

**No explicit goal needed - recent work defines relevance automatically.**

---

## The Predictive Stack: Three Complementary Systems

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

### Layer 1: Work Package Continuity (WPC) - Phase 6

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

### Layer 2: Post-Answer Reasoning - Phase 6

**What**: After answering, use extra 100 tokens to predict next query

**When**: Immediately after providing answer to user

**Example**: User asks "how does auth work?" → Answer + reasoning "they'll probably ask about session storage next" → pre-fetch that context

**Goal**: Next question feels instant because context already loaded

**Implementation**:
1. After generating answer, append reasoning prompt
2. Budget: 100 tokens for prediction
3. Parse prediction → identify likely artifacts/context needed
4. Pre-fetch predicted context (via Phase 5 search)
5. Store in prefetch queue alongside WPC items
6. Track prediction accuracy for learning

### Layer 3: ML Predictive Retrieval - Deferred to Phase 11+

**What**: Train tiny model on session logs to predict needed artifacts

**Status**: Could-Have feature, train on logs after MVP

---

## MemoryMiddleware Design

### Core API: `hydrate_prompt(user_input)`

**Flow:**
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

### Token Budget Allocation

```python
TOKEN_BUDGET = 2000  # Soft cap 1.6-1.8K; hard cap 2K

allocation = {
    'summary': 500,      # Always loaded (session summary)
    'recent': 500,       # Always loaded (last N messages)
    'vector': 700,       # Vector search results
    'project': 300,      # Conditional (project overview)
}

# Dynamic allocation based on conditional loading
if not load_project_overview:
    allocation['vector'] += 300  # Use saved tokens for more vector results
```

### Conditional Project Overview Loading

**When to Load** `.devproject`:
- ✅ Session start
- ✅ User asks macro questions ("What is this project?")
- ✅ Context switch (debugging → new feature)
- ✅ Project overview changed recently
- ✅ Long break (>7 days since last session)

**When to Skip** `.devproject`:
- ❌ Deep in implementation details (debugging specific function)
- ❌ Incremental work (continuing same task)
- ❌ Recent messages show narrow focus

**Benefit**: Save 300 tokens for MORE vector search results when deep in work.

---

## Implementation Tasks

### Task 1: MemoryMiddleware Core

**File**: `reccli/memory_middleware.py` (new)

```python
class MemoryMiddleware:
    """
    Intelligent context loading from .devsession files

    Replaces 200K tokens with 2K intelligent tokens through:
    - Summary layer (high-level overview)
    - Recent messages (conversational continuity)
    - Vector search (relevant history)
    - Conditional project overview
    """

    def __init__(self, session: DevSession, sessions_dir: Path):
        self.session = session
        self.sessions_dir = sessions_dir
        self.token_budget = 2000

    def hydrate_prompt(
        self,
        user_input: str,
        num_recent: int = 20,
        include_wpc: bool = True
    ) -> Dict:
        """
        Build context for LLM from .devsession memory

        Args:
            user_input: Current user query
            num_recent: Number of recent messages to include
            include_wpc: Include Work Package Continuity prefetch

        Returns:
            Context dict with allocated tokens
        """
        context = {}
        tokens_used = 0

        # Layer 1: Always load summary
        context['summary'] = self.session.summary
        tokens_used += self._count_tokens(self.session.summary)

        # Layer 2: Recent messages (conversational continuity)
        recent = self.session.conversation[-num_recent:]
        context['recent'] = recent
        tokens_used += self._count_tokens(recent)

        # Layer 3: Conditionally load project overview
        if self._should_load_project_overview(recent):
            context['project_overview'] = self._load_project_overview()
            tokens_used += 300
            vector_budget = 700
        else:
            vector_budget = 1000

        # Layer 4: Vector search using recent as implicit goal
        query_embedding = self._embed_messages(recent)
        earlier = self.session.conversation[:-num_recent]

        top_k = vector_budget // 70  # ~70 tokens per message
        similar = self._vector_search(
            earlier,
            query_embedding,
            top_k=top_k,
            threshold=0.7
        )

        # Rerank by importance
        similar = self._rerank_by_importance(similar)

        context['relevant_history'] = similar
        tokens_used += self._count_tokens(similar)

        # Layer 5: Work Package Continuity (if enabled)
        if include_wpc and hasattr(self, 'wpc'):
            staged = self.wpc.get_staged_context(budget=900)
            if staged:
                context['wpc_staged'] = staged
                tokens_used += self._count_tokens(staged)

        # Build structured prompt
        prompt = self._build_prompt(context, user_input)

        return {
            'prompt': prompt,
            'context': context,
            'tokens_used': tokens_used,
            'budget': self.token_budget
        }
```

**Subtasks**:
- [ ] Create `MemoryMiddleware` class
- [ ] Implement `hydrate_prompt()` main flow
- [ ] Implement `_should_load_project_overview()` heuristics
- [ ] Implement `_vector_search()` using Phase 5 search
- [ ] Implement `_rerank_by_importance()` boosting
- [ ] Implement `_build_prompt()` structured output
- [ ] Token counting and budget enforcement

---

### Task 2: Work Package Continuity (WPC)

**File**: `reccli/wpc.py` (new)

```python
class WorkPackageContinuity:
    """
    Predictive pre-fetching of likely-next artifacts

    Layer 1 of predictive stack: Pre-generate artifacts while
    user reviews, making multi-file edits feel instant.
    """

    def __init__(self, session: DevSession, sessions_dir: Path):
        self.session = session
        self.sessions_dir = sessions_dir
        self.prefetch_queue = []  # Max size 3-5
        self.max_budget = 900  # tokens
        self.prediction_accuracy = []  # Track accuracy
        self.cooldown = 0  # Adaptive backoff

    def predict_next(self, signal: Dict) -> List[str]:
        """
        Heuristic predictor for likely-next artifacts

        Args:
            signal: Recent events/context
                - recent_messages: Last 50-100 messages
                - section: Active section/episode
                - next_steps: From summary
                - cursor_file: Currently focused file

        Returns:
            List of artifact IDs to prefetch
        """
        predictions = []

        # Heuristic 1: Files touched in last 10 min + neighbors
        recent_files = self._extract_recent_files(signal['recent_messages'])
        for file in recent_files:
            neighbors = self._get_file_neighbors(file)
            predictions.extend(neighbors[:2])

        # Heuristic 2: Files in next_steps
        next_steps = signal.get('next_steps', [])
        for step in next_steps:
            files = self._extract_files_from_text(step)
            predictions.extend(files)

        # Heuristic 3: Recent failing test → source under test
        failing_tests = self._find_failing_tests(signal['recent_messages'])
        for test in failing_tests:
            source = self._infer_source_from_test(test)
            if source:
                predictions.append(source)

        # Heuristic 4: Docs/specs linked recently
        linked_docs = self._extract_linked_docs(signal['recent_messages'])
        predictions.extend(linked_docs)

        # Heuristic 5: Logs around last error
        last_error = self._find_last_error(signal['recent_messages'])
        if last_error:
            error_logs = self._get_error_context(last_error, window_min=5)
            predictions.extend(error_logs)

        # Deduplicate and score
        scored = self._score_predictions(predictions)

        return [p['id'] for p in scored[:5]]

    def prefetch(self, items: List[str], budget: int = 900):
        """
        Pre-retrieve artifacts and stage in queue

        Args:
            items: Artifact IDs to prefetch
            budget: Token budget for prefetch
        """
        from .search import expand_result

        staged = []
        tokens_used = 0

        for item_id in items:
            # Expand using Phase 5
            expanded = expand_result(
                self.sessions_dir,
                item_id,
                context_window=5
            )

            if expanded:
                item_tokens = self._count_tokens(expanded)

                if tokens_used + item_tokens <= budget:
                    staged.append({
                        'id': item_id,
                        'content': expanded,
                        'tokens': item_tokens,
                        'timestamp': datetime.now()
                    })
                    tokens_used += item_tokens

        # Add to queue with LRU eviction
        self._add_to_queue(staged)

    def get_staged_context(self, budget: int = 900) -> List[Dict]:
        """Get pre-fetched items within budget"""
        result = []
        tokens = 0

        for item in self.prefetch_queue:
            if tokens + item['tokens'] <= budget:
                result.append(item)
                tokens += item['tokens']

        return result

    def mark_prediction_used(self, item_id: str, used: bool):
        """Track prediction accuracy for adaptive learning"""
        self.prediction_accuracy.append({
            'id': item_id,
            'used': used,
            'timestamp': datetime.now()
        })

        # Adaptive cooldown: if last 3 unused, back off
        recent = self.prediction_accuracy[-3:]
        if len(recent) == 3 and not any(r['used'] for r in recent):
            self.cooldown = max(self.cooldown, 600)  # 10 min cooldown
```

**Subtasks**:
- [ ] Create `WorkPackageContinuity` class
- [ ] Implement `predict_next()` with 5 heuristics
- [ ] Implement `prefetch()` with Phase 5 integration
- [ ] Implement prefetch queue with LRU eviction
- [ ] Implement idle detection (trigger after 1.5s)
- [ ] Implement adaptive cooldown/backoff
- [ ] Track prediction accuracy

---

### Task 3: Post-Answer Reasoning

**File**: `reccli/post_answer_reasoning.py` (new)

```python
class PostAnswerReasoning:
    """
    Layer 2 of predictive stack: Predict next query after answering

    Uses 100 tokens to reason about what user will ask next,
    then pre-fetches that context for instant follow-up.
    """

    def __init__(self, llm_client):
        self.llm_client = llm_client
        self.reasoning_budget = 100

    async def predict_next_query(
        self,
        conversation_history: List[Dict],
        last_answer: str
    ) -> Dict:
        """
        After answering, predict what user will ask next

        Args:
            conversation_history: Recent conversation
            last_answer: The answer we just gave

        Returns:
            Prediction dict with likely next query and artifacts
        """
        # Build reasoning prompt
        prompt = self._build_reasoning_prompt(
            conversation_history,
            last_answer
        )

        # Use 100 tokens for prediction
        prediction = await self.llm_client.complete(
            prompt,
            max_tokens=self.reasoning_budget,
            temperature=0.3  # Lower for more focused prediction
        )

        # Parse prediction
        parsed = self._parse_prediction(prediction)

        return {
            'next_query_likely': parsed['query'],
            'artifacts_needed': parsed['artifacts'],
            'confidence': parsed['confidence']
        }

    def _build_reasoning_prompt(
        self,
        conversation: List[Dict],
        answer: str
    ) -> str:
        """Build compact reasoning prompt"""
        return f"""
You just answered: "{answer[:200]}..."

Based on this conversation flow, what will the user likely ask next?

Reasoning (max 100 tokens):
- Most likely next question: [specific question]
- Artifacts needed: [list specific files/context]
- Confidence: [high/medium/low]

Keep response under 100 tokens.
"""

    def _parse_prediction(self, prediction: str) -> Dict:
        """Extract structured prediction from LLM response"""
        # Simple parsing logic
        lines = prediction.strip().split('\n')

        result = {
            'query': '',
            'artifacts': [],
            'confidence': 'medium'
        }

        for line in lines:
            if 'next question:' in line.lower():
                result['query'] = line.split(':', 1)[1].strip()
            elif 'artifacts needed:' in line.lower():
                artifacts_str = line.split(':', 1)[1].strip()
                result['artifacts'] = [a.strip() for a in artifacts_str.split(',')]
            elif 'confidence:' in line.lower():
                result['confidence'] = line.split(':', 1)[1].strip().lower()

        return result
```

**Subtasks**:
- [ ] Create `PostAnswerReasoning` class
- [ ] Implement `predict_next_query()` with LLM call
- [ ] Build compact reasoning prompt (≤100 tokens)
- [ ] Parse prediction to extract artifacts
- [ ] Integrate with WPC prefetch queue
- [ ] Track prediction accuracy

---

### Task 4: Reranking and Importance Scoring

**File**: `reccli/memory_middleware.py` (extend)

```python
def _rerank_by_importance(
    self,
    messages: List[Dict],
    current_time: datetime = None
) -> List[Dict]:
    """
    Rerank vector search results by importance factors

    Boosts:
    - Recency (1-20%)
    - Decision messages (1.3×)
    - Code changes (1.2×)
    - Problem solutions (1.25×)
    - In summary (1.4×)
    """
    if current_time is None:
        current_time = datetime.now()

    scored = []

    for msg in messages:
        score = msg.get('cosine_score', 0.5)  # Base: vector similarity

        # Boost recent messages
        timestamp = msg.get('timestamp', '')
        if timestamp:
            try:
                msg_time = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                age_hours = (current_time - msg_time).total_seconds() / 3600
                recency_boost = 1.0 / (1.0 + age_hours / 24)  # Decay over days
                score *= (1 + recency_boost * 0.2)  # Up to 20% boost
            except:
                pass

        # Boost important types
        kind = msg.get('kind', 'note')
        if kind == 'decision':
            score *= 1.3
        elif kind == 'code':
            score *= 1.2
        elif kind == 'problem':
            score *= 1.25

        # Boost if in summary
        if self._is_in_summary(msg):
            score *= 1.4

        scored.append((score, msg))

    # Sort by final score
    scored.sort(reverse=True, key=lambda x: x[0])

    return [msg for score, msg in scored]

def _is_in_summary(self, msg: Dict) -> bool:
    """Check if message is referenced in summary"""
    msg_id = msg.get('message_id', '')
    if not msg_id or not self.session.summary:
        return False

    # Check decisions
    for dec in self.session.summary.get('decisions', []):
        if msg_id in dec.get('message_ids', []):
            return True

    # Check problems
    for prob in self.session.summary.get('problems_solved', []):
        if msg_id in prob.get('message_ids', []):
            return True

    return False
```

**Subtasks**:
- [ ] Implement `_rerank_by_importance()` with boosts
- [ ] Recency boost (up to 20%)
- [ ] Kind weights (decision=1.3, code=1.2, problem=1.25)
- [ ] Summary reference boost (1.4×)
- [ ] Temporal decay formula

---

### Task 5: CLI Integration

**File**: `reccli/cli.py` (extend)

Add new command for testing memory middleware:

```bash
reccli hydrate <session> <query>
```

Shows what context would be loaded for a given query.

**Subtasks**:
- [ ] Add `hydrate` command to CLI
- [ ] Display context breakdown (summary/recent/vector/wpc)
- [ ] Show token allocation
- [ ] Pretty-print with badges

---

## Acceptance Tests

### Core Functionality

- [ ] **Implicit goal extraction**: Recent messages correctly define current work
- [ ] **Vector search relevance**: Retrieved context is semantically relevant
- [ ] **Token budget respected**: Soft cap 1.6-1.8K; hard cap 2K
- [ ] **Continuing after 24h**: Model proposes same pinned Next Steps
- [ ] **"Why did we switch X?"**: Returns decision + citations + EXPAND option

### Conditional Loading

- [ ] **Session start**: Loads project overview
- [ ] **Deep debugging**: Skips project overview, uses tokens for more vector results
- [ ] **Context switch**: Loads project overview when switching features
- [ ] **Macro question**: Loads project overview for "What is this project?"

### Layer 1: Work Package Continuity (WPC)

- [ ] **File prediction**: After editing `retry.ts`, prefetches `test_retry.spec.ts` + `retry_helpers.ts`
- [ ] **Test failure**: When test fails, stages failing test + source under test
- [ ] **Adaptive backoff**: If 3 predictions unused, halves prefetch frequency for 10 min
- [ ] **Idle detection**: Triggers prefetch after 1.5s of no input
- [ ] **Token budget**: WPC stays ≤900 tokens

### Layer 2: Post-Answer Reasoning

- [ ] **Prediction works**: After "how does auth work?", next question about sessions feels instant
- [ ] **Budget respected**: Prediction reasoning ≤100 tokens
- [ ] **Accuracy tracking**: Tracks which predictions were used/unused
- [ ] **Integration with WPC**: Predicted artifacts added to prefetch queue

---

## File Structure

```
packages/reccli-core/
├── reccli/
│   ├── memory_middleware.py     # NEW - Core context loading
│   ├── wpc.py                   # NEW - Work Package Continuity
│   ├── post_answer_reasoning.py # NEW - Post-answer prediction
│   ├── search.py                # EXISTING - Phase 5 search (used by middleware)
│   ├── embeddings.py            # EXISTING - Phase 5 embeddings
│   └── cli.py                   # EXTEND - Add hydrate command
├── tests/
│   ├── test_memory_middleware.py  # NEW
│   ├── test_wpc.py               # NEW
│   └── test_post_answer.py       # NEW
└── docs/progress/phases/PHASE_6_IMPLEMENTATION.md  # This file
```

---

## Configuration

**File**: `.reccli/config.yml` (extend)

```yaml
memory_middleware:
  token_budget: 2000
  soft_cap: 1800
  num_recent_messages: 20
  vector_search_threshold: 0.7

  allocation:
    summary: 500
    recent: 500
    vector: 700
    project_overview: 300  # Conditional

  reranking:
    recency_weight: 0.2
    decision_boost: 1.3
    code_boost: 1.2
    problem_boost: 1.25
    summary_ref_boost: 1.4

wpc:
  enabled: true
  max_queue_size: 5
  token_budget: 900
  idle_timeout_ms: 1500
  prediction_backoff: 600  # seconds

  heuristics:
    recent_files_weight: 1.0
    next_steps_weight: 1.2
    failing_test_weight: 1.5
    linked_docs_weight: 0.8
    error_logs_weight: 1.3

post_answer_reasoning:
  enabled: true
  reasoning_budget: 100
  temperature: 0.3
  min_confidence: 0.6
```

---

## Performance Metrics

### Context Quality

```python
def measure_context_quality(context, user_query, llm_response):
    """Measure if retrieved context was useful"""

    # 1. Relevance: Did LLM reference retrieved context?
    referenced = count_context_references(llm_response, context)
    relevance_score = referenced / len(context['relevant_history'])

    # 2. Efficiency: Were tokens used well?
    efficiency = useful_tokens / total_tokens_loaded

    # 3. Accuracy: Did vector search find right context?
    accuracy = measure_semantic_relevance(context, user_query)

    return {
        'relevance': relevance_score,
        'efficiency': efficiency,
        'accuracy': accuracy
    }
```

### WPC Metrics

```python
def measure_wpc_performance(wpc):
    """Track WPC prediction accuracy"""

    total = len(wpc.prediction_accuracy)
    used = sum(1 for p in wpc.prediction_accuracy if p['used'])

    accuracy = used / total if total > 0 else 0

    # Time savings (estimated)
    avg_prefetch_time = 2.0  # seconds
    time_saved = used * avg_prefetch_time

    return {
        'accuracy': accuracy,
        'total_predictions': total,
        'used_predictions': used,
        'time_saved_seconds': time_saved
    }
```

---

## Migration Strategy

### Phase 1: Core Middleware (MVP)

Just summary + vector search + recent:

```python
def hydrate_simple(session, num_recent=20):
    recent = session.conversation[-num_recent:]
    query_embedding = embed_messages(recent)
    earlier = session.conversation[:-num_recent]

    return {
        'summary': session.summary,
        'relevant': vector_search(earlier, query_embedding, top_k=10),
        'recent': recent
    }
```

**Ship this first. Test if it works.**

### Phase 2: Conditional Project Overview

Add intelligent project overview loading based on heuristics.

### Phase 3: Work Package Continuity (WPC)

Add predictive pre-fetching with heuristics.

### Phase 4: Post-Answer Reasoning

Add post-answer prediction layer.

### Phase 5: Tuning and Optimization

- Track metrics
- Adjust weights
- Improve heuristics based on data

---

## Definition of Done

✅ `reccli chat --session s.devsession` answers "what next?" correctly after a day

**Checklist**:
- [ ] MemoryMiddleware class working
- [ ] Conditional project overview loading
- [ ] Vector search with reranking
- [ ] Token budget enforcement
- [ ] Work Package Continuity (WPC) implemented
- [ ] Post-Answer Reasoning implemented
- [ ] CLI `hydrate` command
- [ ] All acceptance tests passing
- [ ] Performance metrics tracked
- [ ] Documentation updated

---

## Next Steps After Phase 6

Phase 6 completes the context loading infrastructure. Next phases build on this:

- **Phase 7**: Preemptive Compaction uses Phase 6 to compact at 190K tokens
- **Phase 8**: Chat Interface uses Phase 6 for intelligent context hydration
- **Phase 10**: .devproject uses Phase 6 for cross-session reasoning

---

**Ready to implement!** 🚀
