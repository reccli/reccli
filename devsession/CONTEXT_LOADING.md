# Context Loading Strategy for .devsession

## The Core Problem

When loading a `.devsession` file to continue work, what context should the LLM receive?

**Constraints:**
- Can't load full 50K token conversation (too expensive, too noisy)
- Summary alone might miss critical details
- Need to be relevant to current problem
- Must be better than raw context or compaction

**Goal:** Load ~2000 tokens that give LLM the RIGHT context to continue effectively.

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
def load_context_for_continuation(
    devsession_file,
    current_goal,
    max_tokens=2000
):
    """
    Load optimal context for continuing a dev session

    Args:
        devsession_file: Path to .devsession file
        current_goal: User's current problem/objective
        max_tokens: Maximum context to load (default 2000)

    Returns:
        Structured context for LLM
    """

    session = load_devsession(devsession_file)
    context = {}

    # LAYER 1: Always load summary
    context['summary'] = session.summary
    tokens_used = count_tokens(session.summary)

    # LAYER 2: Vector search around current goal
    goal_embedding = embed(current_goal)

    # Search conversation for semantic similarity
    similar_messages = vector_search(
        vectors=session.conversation,
        query=goal_embedding,
        top_k=15,
        threshold=0.7  # Cosine similarity threshold
    )

    # Prioritize by:
    # 1. Semantic similarity (already sorted)
    # 2. Recency (boost recent messages)
    # 3. Importance tags (decisions, code changes, problems)
    similar_messages = rerank_by_importance(similar_messages)

    context['relevant_history'] = similar_messages[:10]
    tokens_used += count_tokens(similar_messages[:10])

    # LAYER 3: Recent context (conversational continuity)
    recent_messages = session.conversation[-20:]

    # Remove duplicates if already in relevant_history
    recent_messages = [m for m in recent_messages
                      if m.id not in [r.id for r in similar_messages]]

    context['recent_context'] = recent_messages
    tokens_used += count_tokens(recent_messages)

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

### Scenario: User Returns After 3 Days

**Session Summary:**
- Built Stripe webhook integration (2 hours, 187 messages)
- Key decision: Use req.rawBody for signature verification
- Problem solved: Body-parser was consuming request
- Open issue: Need error handling for failed transfers

**User's Current Goal (Day 4):**
```
"The webhook is failing again with a 400 error.
 Not sure if it's the same signature issue."
```

### Context Loading Process:

**Step 1: Load Summary (500 tokens)**
```
Summary loaded:
- Goal was: Build Stripe webhook integration
- Decision: Use req.rawBody for signature verification
- Problem solved: Body-parser consuming request (msg_167-180)
- Open: Error handling needed
```

**Step 2: Vector Search (1000 tokens)**
```
Query: "webhook failing 400 error signature issue"
Embedding: [0.234, -0.567, ...]

Top matches (by similarity + importance):
1. msg_167: "Webhook signature verification failing with 400" (0.92)
2. msg_168: "Body-parser is consuming req.body" (0.89)
3. msg_169: "Need to use req.rawBody" (0.87)
4. msg_178: "Created middleware/rawBody.js" (0.85)
5. msg_134: "Webhook authentication setup" (0.78)
... (10 total messages)
```

**Step 3: Recent Context (400 tokens)**
```
Last 20 messages from session:
- Final testing discussion
- "Next step: test end-to-end flow"
- "Should add error handling"
```

**Step 4: Linked Context (from summary references)**
```
Summary references msg_167-180 for problem_solved.
Load those as thread: [msg_167, msg_168, msg_169, msg_178, msg_179, msg_180]
```

### What LLM Receives:

```
CONTEXT FOR CONTINUATION:

=== SUMMARY (Day 3) ===
Goal: Built Stripe webhook integration
Decision: Use req.rawBody for signature verification (reasoning: body-parser modifies req.body)
Problem Solved: Signature verification (msg_167-180)
Open: Error handling for failed transfers

=== RELEVANT HISTORY (Vector Matches) ===
msg_167: "The webhook signature verification is failing with a 400 error"
msg_168: "This is because body-parser consumes req.body. Need raw body."
msg_169: "Solution: Use req.rawBody for stripe.webhooks.constructEvent()"
msg_178: [code] Created middleware/rawBody.js
[+ 6 more related messages]

=== RECENT CONTEXT (Last session end) ===
msg_205: "Great! Webhook is working now."
msg_210: "Next steps: test end-to-end flow and add error handling"
msg_215: "Should we test it now?"

=== CURRENT PROBLEM ===
User: "The webhook is failing again with a 400 error.
      Not sure if it's the same signature issue."
```

**Total: ~2000 tokens**

### LLM Response:

```
I see we already solved a signature verification issue on Day 3.
Let me check if you're using the rawBody middleware correctly.

From the previous session (msg_178), we created middleware/rawBody.js
that preserves the original request body.

The solution was:
- Use req.rawBody instead of req.body
- Apply this before body-parser runs

Can you check:
1. Is the rawBody middleware being used?
2. Is it applied to the /webhooks route?
3. What does the error message say specifically?

[Has full context from vector search to help debug]
```

**LLM has exactly what it needs. No more, no less.**

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
def load_context_simple(session, goal):
    return {
        'summary': session.summary,
        'relevant': vector_search(session, goal, top_k=10),
        'recent': session.conversation[-15:]
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
# Auto-select strategy based on goal type
strategy = classify_goal(goal)  # debugging, new_feature, etc.
context = load_with_strategy(session, goal, strategy)
```

---

## Key Insight: Goal Relevance

**You're right** - relevance to user's **current goal** is the primary factor.

The algorithm should:
1. ✅ Embed the current goal/problem
2. ✅ Find messages similar to that goal
3. ✅ Boost important message types
4. ✅ Include recent context for continuity
5. ✅ Allow expansion via summary links

**This gives focused, relevant context instead of everything or nothing.**

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
