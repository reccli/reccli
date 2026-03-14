# Context Loading Strategy for .devsession

**Status:** Mixed implementation/design document.

The live code implements summary loading, recent-message continuity, vector retrieval, streaming hydration, and optional `.devproject` loading when a project file is present. References below that treat `.devproject` as mandatory or automatically maintained should be read as planned project-layer behavior unless they match the current middleware code.

## The Core Problem

When **compacting a long session** (approaching 180K+ tokens), what context should remain?

**Constraints:**
- Can't keep full 180K tokens (at limit)
- Summary alone might miss critical details
- Need relevance to what user is currently working on
- Must maintain conversation continuity

**Goal:** Compact 180K tokens → 2K tokens with the RIGHT context to continue seamlessly.

---

## Three-Layer Context Architecture

The .devsession format provides three layers of context:

### Layer 1: Project Overview (Macro, Optional)
**Source:** `.devproject` file at project root, if present
**Size:** ~300-500 tokens
**Contains:** Project name, purpose, architecture, key decisions, tech stack, milestones
**Updated:** Planned project-layer behavior
**Loaded:** Conditionally when available (see Conditional Loading section below)

**Purpose:** Keeps the model grounded in "what is this project?" when project-level context exists, but RecCli should still function without it.

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

## The Strategy: Implicit Goal from Recent Messages

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

## Strategy: Multi-Layered Retrieval

### Layer 1: Summary (Always Loaded)
**What:** Complete summary from last session
**Size:** ~500-1000 tokens
**Contains:**
- Current goal
- Key decisions made
- Code changes
- Problems solved
- Open issues
- Next steps

**Why:** Provides high-level context always. LLM knows what happened without details.

### Layer 2: Goal-Relevant Context (Vector Search)
**What:** Messages semantically similar to current goal/problem
**Size:** ~800-1200 tokens (10-15 messages)
**Strategy:** Vector search with current query

### Layer 3: Recent Context (Temporal)
**What:** Last N messages from previous session
**Size:** ~400-600 tokens (15-20 messages)
**Why:** Immediate context, conversational continuity

### Layer 4: On-Demand Expansion (As Needed)
**What:** Specific sections LLM requests
**Size:** Variable
**Strategy:** LLM can request specific decision/problem details

**Total:** ~2000-3000 tokens vs 50,000 raw

---

## Implementation: The Algorithm

```python
def compact_session_intelligently(
    session,
    num_recent_messages=20,
    max_tokens=2000
):
    """
    Compact a session by extracting relevant context using recent messages as query

    Args:
        session: Loaded .devsession session object
        num_recent_messages: Number of recent messages to use as implicit goal
        max_tokens: Maximum context to load (default 2000)

    Returns:
        Compacted context for LLM continuation
    """

    context = {}

    # LAYER 1: Always load summary
    context['summary'] = session.summary
    tokens_used = count_tokens(session.summary)

    # LAYER 2: Vector search using recent messages as implicit goal
    recent_for_query = session.conversation[-num_recent_messages:]
    goal_embedding = embed_messages(recent_for_query)

    # Search earlier conversation for semantic similarity
    # (exclude the recent messages we used as query)
    # Performance: <5ms for 10,000 messages with binary .npy storage
    earlier_messages = session.conversation[:-num_recent_messages]
    similar_messages = vector_search(
        vectors=earlier_messages,
        query=goal_embedding,
        top_k=15,
        threshold=0.7  # Cosine similarity threshold
    )  # Uses vectorized numpy operations with binary file loading

    # Prioritize by:
    # 1. Semantic similarity (already sorted)
    # 2. Recency (boost recent messages)
    # 3. Importance tags (decisions, code changes, problems)
    similar_messages = rerank_by_importance(similar_messages)

    context['relevant_history'] = similar_messages[:10]
    tokens_used += count_tokens(similar_messages[:10])

    # LAYER 3: Recent context (conversational continuity)
    # Use the same recent messages that defined our query
    context['recent_context'] = recent_for_query
    tokens_used += count_tokens(recent_for_query)

    # LAYER 4: Linked context (follow references)
    # If summary references specific decisions/problems,
    # include those full message threads
    linked_context = get_linked_messages(
        session,
        context['summary'],
        max_messages=5
    )

    if tokens_used + count_tokens(linked_context) < max_tokens:
        context['linked'] = linked_context
        tokens_used += count_tokens(linked_context)

    return context, tokens_used


def rerank_by_importance(messages, current_time=None):
    """
    Rerank vector search results by importance factors
    """
    scored = []

    for msg in messages:
        score = msg.similarity_score  # Base: cosine similarity

        # Boost recent messages
        age_hours = (current_time - msg.timestamp).total_seconds() / 3600
        recency_boost = 1.0 / (1.0 + age_hours / 24)  # Decay over days
        score *= (1 + recency_boost * 0.2)  # 20% boost for recency

        # Boost important types
        if is_decision(msg):
            score *= 1.3
        if is_code_change(msg):
            score *= 1.2
        if is_problem_solved(msg):
            score *= 1.25

        # Boost if in summary
        if msg.id in get_summary_references(session.summary):
            score *= 1.4

        scored.append((score, msg))

    # Sort by final score
    scored.sort(reverse=True, key=lambda x: x[0])
    return [msg for score, msg in scored]


def get_linked_messages(session, summary, max_messages=5):
    """
    Get messages that summary links to for full context
    """
    linked = []

    # Extract all message references from summary
    for decision in summary.decisions:
        for ref in decision.references:
            msg = session.get_message(ref)
            if msg and msg not in linked:
                linked.append(msg)

    for problem in summary.problems_solved:
        for ref in problem.references[:2]:  # First 2 refs per problem
            msg = session.get_message(ref)
            if msg and msg not in linked:
                linked.append(msg)

    return linked[:max_messages]
```

---

## Example: How It Works in Practice

### Scenario: Session Hitting Context Limit During Active Debugging

**Session State (180K tokens):**
- Built Stripe webhook integration (2 hours, 187 messages)
- Key decision: Use req.rawBody for signature verification
- Problem solved: Body-parser was consuming request
- Open issue: Need error handling for failed transfers

**Recent Messages (messages 168-187) show:**
```
msg_168: "The webhook is failing again with a 400 error"
msg_169: "Let me check the signature verification"
msg_170: "It looks like the same issue from before"
msg_171-187: [debugging conversation continues...]
```

### Compaction Process:

**Step 1: Generate Summary (500 tokens)**
```
Summary loaded:
- Goal was: Build Stripe webhook integration
- Decision: Use req.rawBody for signature verification
- Problem solved: Body-parser consuming request (msg_167-180)
- Open: Error handling needed
```

**Step 2: Extract Recent Messages as Query (implicit goal)**
```
Messages 168-187 (last 20 messages):
"webhook failing 400 error signature issue..."
"debugging continues..."

Embed these together → Query vector: [0.234, -0.567, ...]
```

**Step 3: Vector Search Earlier Messages (1000 tokens)**
```
Search messages 1-167 for semantic similarity to recent work

Top matches (by similarity + importance):
1. msg_45: "Initial webhook signature verification setup" (0.91)
2. msg_67: "Body-parser is consuming req.body" (0.89)
3. msg_68: "Need to use req.rawBody" (0.87)
4. msg_78: "Created middleware/rawBody.js" (0.85)
5. msg_134: "Webhook authentication setup" (0.78)
... (10 total messages from earlier in session)
```

**Step 4: Linked Context (from summary references)**
```
Summary references msg_45-78 for the original problem.
Load those as thread for full context.
```

### Compacted Context (2K tokens):

```
COMPACTED CONTEXT FOR CONTINUATION:

=== SUMMARY ===
Goal: Built Stripe webhook integration
Decision: Use req.rawBody for signature verification
  Reasoning: body-parser modifies req.body causing signature failures
Problem Solved: Initial signature verification (msg_45-78)
Open Issues: Need error handling for failed transfers

=== RELEVANT EARLIER HISTORY (Vector Matches) ===
msg_45: "Setting up Stripe webhook signature verification"
msg_67: "Body-parser consumes req.body before we can verify signature"
msg_68: "Solution: Use req.rawBody for stripe.webhooks.constructEvent()"
msg_78: [code] Created middleware/rawBody.js to preserve original body
[+ 6 more related messages from earlier work]

=== RECENT WORK (Messages 168-187) ===
msg_168: "The webhook is failing again with a 400 error"
msg_169: "Let me check the signature verification"
msg_170: "It looks like the same issue from before"
msg_171: "Checking if rawBody middleware is properly applied..."
[... 17 more recent messages showing current debugging]

=== NOW CONTINUE ===
Session compacted: 180K → 2K tokens
LLM has context of original solution + current debugging
```

**Total: ~2000 tokens (from 180K)**

### What Happened:

The LLM seamlessly continues working with:
- **Summary**: High-level overview of what was accomplished
- **Relevant earlier work**: Vector search found the original signature fix (msg_45-78)
- **Recent context**: Current debugging attempt (msg_168-187)

The recent messages implicitly defined the goal ("webhook failing, signature issue") without explicitly asking the user. The vector search automatically found related earlier work.

**Result:** Conversation continues naturally, LLM has exactly the context it needs to help debug, no interruption to ask "what are you working on?"

---

## Strategy Variations

### 1. **Aggressive (More Context)**
```python
top_k = 20  # More vector results
recent = 30  # More recent messages
max_tokens = 3000
```
**When:** Complex debugging, need more history

### 2. **Conservative (Less Context)**
```python
top_k = 5   # Fewer vector results
recent = 10 # Fewer recent messages
max_tokens = 1000
```
**When:** Simple continuation, summary is enough

### 3. **Goal-Focused (Ignore Recent)**
```python
top_k = 15
recent = 0  # No recent context
linked = 10 # More linked refs
```
**When:** Jumping to different part of project

### 4. **Recent-Focused (New Problem)**
```python
top_k = 5   # Less history
recent = 30 # Lots of recent
linked = 0
```
**When:** Continuing from where you left off

---

## Tuning Parameters

### Vector Search Parameters:

**top_k** (default: 10-15)
- How many similar messages to retrieve
- Higher = more context, more noise
- Lower = focused, might miss details

**similarity_threshold** (default: 0.7)
- Minimum cosine similarity to include
- Higher = only very relevant
- Lower = cast wider net

**reranking_weights**
```python
{
    'recency': 0.2,       # Boost recent messages
    'decision': 1.3,      # Boost decision messages
    'code': 1.2,          # Boost code changes
    'problem': 1.25,      # Boost problem solutions
    'summary_ref': 1.4    # Boost if in summary
}
```

### Context Size Parameters:

**max_tokens** (default: 2000)
- Total context budget
- Adjust based on model capabilities

**layer_allocation**
```python
{
    'summary': 500,     # Fixed
    'vector': 1000,     # Variable
    'recent': 400,      # Variable
    'linked': 100       # Variable
}
```

---

## Measuring Success

### Key Metrics:

**1. Relevance Score**
```python
# Did retrieved context actually help?
def measure_relevance(context, user_query, llm_response):
    # Check if LLM referenced retrieved context
    referenced = count_context_references(llm_response, context)
    relevance = referenced / len(context)
    return relevance
```

**2. Token Efficiency**
```python
# Are we using tokens efficiently?
efficiency = useful_tokens / total_tokens_loaded
```

**3. User Satisfaction**
```python
# Did user get helpful response?
# Track: upvotes, follow-up questions, time to resolution
```

**4. Comparison Benchmark**
```python
# Compare to alternatives
results = {
    'raw_context': test_with_full_context(),
    'compaction': test_with_compaction(),
    'devsession': test_with_vector_search()
}
# Measure: quality, speed, cost
```

---

## Implementation Priority

### Phase 1: Simple Strategy (MVP)
```python
# Just summary + vector search + recent
def compact_simple(session, num_recent=20):
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

### Phase 2: Reranking
```python
# Add importance-based reranking
results = vector_search(...)
results = rerank_by_importance(results)
```

### Phase 3: Linked Context
```python
# Follow summary references
linked = get_linked_messages(session, summary)
```

### Phase 4: Dynamic Strategy Selection
```python
# Auto-select strategy based on recent message patterns
strategy = classify_recent_work(session.conversation[-20:])
context = compact_with_strategy(session, strategy)
```

---

## Key Insight: Goal Relevance

**The Key Insight:** Recent messages implicitly contain the current goal. No need to ask.

The algorithm:
1. ✅ Extract recent messages (what user is working on NOW)
2. ✅ Embed those as the query vector (implicit goal)
3. ✅ Find earlier messages similar to that work
4. ✅ Boost important message types (decisions, code, problems)
5. ✅ Include summary for high-level overview
6. ✅ Keep recent messages for continuity

**This gives focused, relevant context with zero friction.**

---

## Conditional Project Overview Loading

### Where Project Overview Lives

If present, the project overview can be stored in a **`.devproject` file at the project root**:

```
~/projects/YourProject/
├── .devproject              # ← Optional project overview cache
├── .gitignore               # ← May exclude .devproject, depending on user choice
├── README.md
└── ...
```

**Lifecycle today vs. planned behavior:**
- **Today:** middleware can read `.devproject` if one already exists nearby
- **Planned:** RecCli may generate or refine `.devproject` from repo state and session history
- **Planned:** Gitignore management would be opt-out or user-confirmed, not assumed as a hard requirement

See `DEVPROJECT_FILE.md` for complete specification.

### The Token Budget Problem

With three-layer architecture, we need to manage token budget carefully:

```
Token budget: 2000 tokens

Option A - Always load project overview:
- Project overview: 300 tokens
- Session summary: 500 tokens
- Recent messages: 500 tokens
= 1300 base tokens
= Only 700 left for vector search (10 messages)

Option B - Conditionally load project overview:
- Session summary: 500 tokens
- Recent messages: 500 tokens
= 1000 base tokens
= 1000 left for vector search (15 messages)
```

**Strategy:** Load project overview only when it's relevant, use saved tokens for more vector search results when deep in implementation.

### When to Load Project Overview

```python
def should_load_project_overview(context):
    """
    Decide if project overview is relevant for current compaction
    """
    # ✅ Always load at session start
    if context.is_session_start:
        return True

    # ✅ Load if user asks macro questions
    if is_macro_query(context.recent_messages):
        # "What is this project?"
        # "What are we building?"
        # "What's the architecture?"
        return True

    # ✅ Load if switching contexts
    if is_context_switch(context.previous_work, context.current_work):
        # Was debugging → now building new feature
        # Was working on API → now working on UI
        return True

    # ✅ Load if project overview changed recently
    if context.project_overview.last_updated in context.recent_sessions:
        # New decision made in last 1-2 sessions
        return True

    # ✅ Load after long break
    if days_since_last_session(context) > 7:
        # Haven't worked on project in over a week
        return True

    # ❌ Skip if deep in implementation details
    if is_deep_implementation_work(context.recent_messages):
        # Debugging specific function
        # Fixing typo
        # Adjusting CSS
        return False

    # ❌ Skip for incremental work
    if is_continuing_same_task(context):
        # Mid-feature, same work as last 20 messages
        return False

    # Default: load it (safer to have macro context)
    return True
```

### Implementation

```python
def compact_with_conditional_overview(session, recent_messages):
    """
    Smart compaction with conditional project overview
    """
    context = {
        'recent': recent_messages,  # ~500 tokens (always)
        'summary': session.summary  # ~500 tokens (always)
    }

    tokens_used = 1000

    # Conditionally load project overview
    if should_load_project_overview(session):
        context['project_overview'] = session.project_overview
        tokens_used += 300
        vector_budget = 700  # Fewer vector results
    else:
        vector_budget = 1000  # More vector results

    # Vector search with dynamic budget
    query_embedding = embed_messages(recent_messages)

    # Calculate top_k based on available budget
    # Assume ~70 tokens per message
    top_k = vector_budget // 70  # ~10-15 messages

    context['relevant'] = vector_search(
        session.conversation,
        query_embedding,
        top_k=top_k
    )

    return context  # ~2000 tokens total
```

### Examples

#### Example 1: Session Start (Load Project Overview)

```python
Context:
- is_session_start = True
- User: "Continue working on RecCli"

Decision: LOAD project overview
- Project overview: 300 tokens ✓
- Session summary: 500 tokens ✓
- Recent messages: 500 tokens ✓
- Vector search: 700 tokens (10 messages)
Total: 2000 tokens

Why: Session start needs macro context - what is this project,
     where are we in development, what's the next milestone.
```

#### Example 2: Deep Implementation (Skip Project Overview)

```python
Context:
- is_session_start = False
- is_deep_implementation = True
- Recent messages: All about debugging webhook signature issue

Decision: SKIP project overview
- Session summary: 500 tokens ✓
- Recent messages: 500 tokens ✓
- Vector search: 1000 tokens (15 messages)
Total: 2000 tokens

Why: Deep in debugging specific issue. Don't need to know "what is
     RecCli" right now. Use extra 300 tokens for MORE debugging
     context from vector search.
```

#### Example 3: Context Switch (Load Project Overview)

```python
Context:
- is_session_start = False
- is_context_switch = True
- Previous work: Debugging webhooks
- Current work: "Let's implement the export dialog"

Decision: LOAD project overview
- Project overview: 300 tokens ✓
- Session summary: 500 tokens ✓
- Recent messages: 500 tokens ✓
- Vector search: 700 tokens (10 messages)
Total: 2000 tokens

Why: Switching from debugging to new feature. Need to re-ground
     in project architecture and goals.
```

#### Example 4: Macro Question (Load Project Overview)

```python
Context:
- Recent message: "What's our overall architecture again?"

Decision: LOAD project overview
- Project overview: 300 tokens ✓ (has architecture section!)
- Session summary: 500 tokens ✓
- Recent messages: 500 tokens ✓
- Vector search: 700 tokens
Total: 2000 tokens

Why: User explicitly asking for macro context. Project overview
     contains architecture details.
```

### Detection Heuristics

```python
def is_macro_query(recent_messages):
    """Detect if user is asking project-level questions"""
    macro_keywords = [
        'project', 'architecture', 'what is', 'overview',
        'purpose', 'goals', 'decisions', 'tech stack',
        'how does', 'explain the', 'big picture'
    ]

    recent_text = ' '.join([m.content for m in recent_messages[-3:]])
    return any(keyword in recent_text.lower() for keyword in macro_keywords)


def is_context_switch(previous_work, current_work):
    """Detect if switching between different areas of work"""
    # Compare embeddings of work descriptions
    prev_embedding = embed(previous_work.description)
    curr_embedding = embed(current_work.description)

    similarity = cosine_similarity(prev_embedding, curr_embedding)

    # Low similarity = different contexts
    return similarity < 0.7


def is_deep_implementation_work(recent_messages):
    """Detect if deep in implementation details"""
    implementation_patterns = [
        'debug', 'error', 'fix', 'bug', 'typo',
        'line \\d+', 'function \\w+\\(',
        'variable', 'import', 'syntax'
    ]

    recent_text = ' '.join([m.content for m in recent_messages[-10:]])

    # Count implementation patterns
    matches = sum(1 for pattern in implementation_patterns
                  if re.search(pattern, recent_text, re.I))

    # If 5+ implementation patterns in last 10 messages
    return matches >= 5
```

### Benefits

**✅ Optimized Token Usage**
- Use 300 tokens for macro context when it matters
- Use 300 tokens for MORE vector results when deep in work

**✅ Context-Aware**
- Session start? Get grounded in project
- Deep in debugging? Get more debugging context
- Switching features? Re-orient with project overview

**✅ Flexible**
- Default to loading (safer)
- Skip when clearly not needed
- User can override if needed

**✅ Best of Both Worlds**
- Macro perspective when needed
- Maximum micro detail when deep in work

---

## Next: Prove It Works

Once implemented, we can benchmark:

```python
# Test cases
test_cases = [
    {
        'scenario': 'Return to debugging after 3 days',
        'session': 'stripe-webhook.devsession',
        'goal': 'Webhook failing with 400 error',
        'expected': 'Should find previous signature fix'
    },
    {
        'scenario': 'New feature on same codebase',
        'session': 'stripe-webhook.devsession',
        'goal': 'Add refund handling',
        'expected': 'Should find Stripe setup code'
    }
]

# Compare strategies
for test in test_cases:
    results = {
        'raw': test_with_full_context(test),
        'compact': test_with_compaction(test),
        'vector': test_with_vector_search(test)
    }

    print(f"Quality: {results['vector'].quality} vs {results['raw'].quality}")
    print(f"Tokens: {results['vector'].tokens} vs {results['raw'].tokens}")
```

**Data proves the approach.**

---

## Summary

**Strategy:** Goal-oriented vector search + recent context + summary

**Why It Works:**
- Relevance to current problem (vector search)
- Conversational continuity (recent messages)
- High-level awareness (summary always loaded)
- Details on demand (linked expansion)

**Result:** ~2000 focused tokens vs 50,000 noisy tokens

**Better reasoning with less context.**
