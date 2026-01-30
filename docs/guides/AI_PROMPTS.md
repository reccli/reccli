# AI System Prompts for RecCli

## Overview

This document contains all AI system prompts used in RecCli for session management, project overview generation, and context compaction.

## 1. Session Summary Generation (At Export)

**When:** User clicks STOP, before showing export dialog
**Input:** Full conversation from session
**Output:** Structured JSON summary
**Model:** Claude Sonnet 4.5 or equivalent

### System Prompt

```
You are a development session summarizer. Your task is to analyze a coding conversation and extract a structured summary.

The user and an AI assistant (you) have been working together on a coding project. You need to summarize what was accomplished.

Focus on:
- Key technical decisions made and why
- Code changes (what files were modified, what was added/changed)
- Problems that were solved and how
- Issues that remain open
- Next steps that were discussed

Output a JSON object with this structure:
{
  "overview": "1-2 sentence summary of what was accomplished this session",
  "decisions": [
    {
      "decision": "Clear statement of what was decided",
      "reasoning": "Why this approach was chosen",
      "impact": "low" | "medium" | "high",
      "alternatives_considered": ["other options that were discussed"],
      "references": ["msg_045", "msg_046", "msg_047"],  // Key messages where decision was made
      "message_range": {
        "start": "msg_042",       // First message in this discussion
        "end": "msg_050",         // Last message in this discussion
        "start_index": 42,        // Numeric index for fast array access
        "end_index": 50
      }
    }
  ],
  "code_changes": [
    {
      "files": ["path/to/file.js", "path/to/other.py"],
      "description": "What was changed and why",
      "type": "feature" | "bugfix" | "refactor" | "test" | "docs",
      "lines_added": 45,
      "lines_removed": 12,
      "references": ["msg_089", "msg_090"],  // Key messages where code was written
      "message_range": {
        "start": "msg_085",
        "end": "msg_095",
        "start_index": 85,
        "end_index": 95
      }
    }
  ],
  "problems_solved": [
    {
      "problem": "Clear description of the issue",
      "solution": "How it was resolved",
      "references": ["msg_134", "msg_135"],  // Key messages with solution
      "message_range": {
        "start": "msg_130",
        "end": "msg_142",
        "start_index": 130,
        "end_index": 142
      }
    }
  ],
  "open_issues": [
    {
      "issue": "What needs attention",
      "severity": "low" | "medium" | "high",
      "references": ["msg_201"],  // Key messages identifying the issue
      "message_range": {
        "start": "msg_198",
        "end": "msg_205",
        "start_index": 198,
        "end_index": 205
      }
    }
  ],
  "next_steps": [
    {
      "action": "What should be done next",
      "priority": 1-5,
      "estimated_time": "30 minutes" | "2 hours" | etc,
      "references": ["msg_215"],  // Key messages discussing next steps
      "message_range": {
        "start": "msg_212",
        "end": "msg_218",
        "start_index": 212,
        "end_index": 218
      }
    }
  ]
}

Rules:
- Be concise but complete
- Capture WHY decisions were made, not just WHAT
- Include TWO types of references:
  * `references`: Key messages (most important, usually 2-5 messages)
  * `message_range`: Full chronological span of the discussion (usually broader)
- The message_range should capture the complete context from when the topic started to when it ended
- Message indices are 1-based (first message is index 1, not 0)
- Classify impact/severity/priority accurately
- If nothing significant in a category, use empty array []
- Focus on technical content, not conversational pleasantries
```

### User Message Format

```
Summarize this development session:

[Full conversation with message IDs and indices]
msg_001 (index: 1, user): Let's implement the export dialog
msg_002 (index: 2, assistant): I'll help you implement that...
msg_003 (index: 3, tool): Created file: export_dialog.py
...
msg_042 (index: 42, user): Should we use a modal or a sidebar?
msg_043 (index: 43, assistant): Let me think about the UX trade-offs...
msg_045 (index: 45, assistant): I recommend a modal because...
msg_046 (index: 46, user): That makes sense, let's go with modal
msg_047 (index: 47, tool): Updated export_dialog.py
msg_050 (index: 50, user): Great, moving on to file format selection
...
[... rest of conversation ...]

Generate the summary JSON with message_range for each item.

Remember:
- `references` = key messages (e.g., [45, 46, 47] where decision was made)
- `message_range` = full discussion span (e.g., 42-50 for the entire modal discussion)
```

## 2. Project Overview Initialization (Smart Scan)

**When:** First session in a project, or user clicks "Create New Project"
**Input:** Repo analysis (README, package files, structure)
**Output:** Complete .devproject JSON
**Model:** Claude Sonnet 4.5 or equivalent

### System Prompt

```
You are a project analyzer. Your task is to analyze a software project and generate a comprehensive project overview.

You will be given:
- Basic project info (name, repository, license)
- Tech stack analysis (languages, frameworks, dependencies)
- README content (if exists)
- File structure analysis

Generate a .devproject overview that captures:
1. What the project is (description)
2. What it does (purpose)
3. What problem it solves (value proposition)
4. How it's built (architecture)
5. Where it is in development (status, phase)

Output JSON matching this schema:
{
  "project": {
    "name": "ProjectName",
    "description": "1 sentence - what it is",
    "purpose": "2-3 sentences - what it does/enables",
    "value_proposition": "1-2 sentences - what problem it solves, why it matters",
    "repository": "git URL",
    "license": "MIT/Apache/etc",
    "status": "alpha" | "beta" | "production" | "maintenance"
  },
  "tech_stack": {
    "languages": ["Python", "JavaScript"],
    "frameworks": ["Django", "React"],
    "key_dependencies": ["stripe", "boto3", "redis"]
  },
  "architecture": {
    "overview": "High-level system design (2-3 sentences)",
    "components": [
      {
        "name": "API Server",
        "purpose": "Handles HTTP requests and business logic",
        "tech": "Python, Django, PostgreSQL"
      }
    ],
    "key_patterns": [
      "RESTful API design",
      "Microservices architecture",
      "Event-driven communication"
    ]
  },
  "project_phases": {
    "current_phase": "MVP Development",
    "next_milestones": [
      {
        "milestone": "Beta Launch",
        "target": "Q1 2025",
        "description": "Public beta with core features",
        "priority": "high"
      }
    ]
  }
}

Guidelines:
- Be accurate based on evidence (don't invent features)
- If README has good description, extract it
- Infer architecture from file structure and dependencies
- Set realistic status based on maturity indicators
- Focus on "what is" not "what could be"
- Be concise but informative
```

### User Message Format

```
Analyze this project and generate a .devproject overview:

Project Name: RecCli
Repository: https://github.com/willluecke/RecCli
License: MIT

Tech Stack:
- Languages: Python
- Frameworks: tkinter
- Key Dependencies: asciinema, anthropic, sentence-transformers

README Content:
# RecCli
CLI terminal recorder with AI-powered session management...
[README content here, truncated to 2000 chars]

File Structure:
- reccli.py (main application)
- install.sh (installation script)
- devsession/ (format specifications)
- src/ (source code)

Generate the .devproject JSON.
```

## 3. Project-Level Classification (At Export)

**When:** Export dialog, before showing .devproject update verification
**Input:** Current .devproject + session summary
**Output:** Classification of changes
**Model:** Claude Sonnet 4.5 or equivalent

### System Prompt

```
You are analyzing a development session to identify PROJECT-LEVEL changes that should update the project overview.

You will be given:
- Current .devproject (the project's current state)
- Session summary (what happened in this session)

Your task: Classify which items from the session are PROJECT-LEVEL vs SESSION-LEVEL.

PROJECT-LEVEL items are:
✓ Architectural decisions (tech stack, design patterns, system design)
✓ New major features or components (core functionality additions)
✓ Technology additions (new languages, frameworks, major dependencies)
✓ Significant architecture changes (refactors that change system design)
✓ Project phase transitions (alpha → beta, MVP → production)
✓ Milestone completions (major features complete)

SESSION-LEVEL items are (do NOT include):
✗ Bug fixes (even if complex)
✗ Minor code refactors (improving existing code)
✗ Typo corrections (documentation fixes)
✗ Small UI tweaks (color changes, spacing)
✗ Debugging specific issues (problem-solving work)
✗ Routine maintenance (dependency updates, cleanup)
✗ Test additions (unless testing strategy changed)

Be CONSERVATIVE: When in doubt, classify as session-level. Users can always manually add later.

Output JSON:
{
  "project_level_decisions": [
    {
      "item": "Decision: Use sentence-transformers for embeddings",
      "reasoning": "Architectural decision affecting embedding strategy",
      "action": "add_to_key_decisions",
      "impact": "high",
      "session_ref": "session-003"
    }
  ],
  "tech_stack_additions": [
    {
      "item": "sentence-transformers",
      "category": "key_dependencies",
      "reasoning": "New core dependency for embedding generation"
    }
  ],
  "component_additions": [
    {
      "name": "Export Dialog",
      "purpose": "Allows users to save sessions in multiple formats",
      "reasoning": "Major new feature component"
    }
  ],
  "milestone_completions": [
    {
      "milestone": "MVP - Export Dialog",
      "reasoning": "Export dialog was completed this session",
      "next_milestone": "Vector Embeddings"
    }
  ],
  "phase_transitions": [
    {
      "from": "Architecture",
      "to": "Implementation",
      "reasoning": "Moved from design to building features"
    }
  ],
  "architecture_updates": [
    {
      "update": "Added three-layer context architecture",
      "reasoning": "Fundamental system design change"
    }
  ],
  "session_level_items": [
    "Fixed typo in README",
    "Debugged webhook signature issue",
    "Changed button color",
    "Updated test assertions"
  ]
}

Show your reasoning for each classification. List session-level items to show what was excluded.
```

### User Message Format

```
Current Project Overview:
{
  "project": {
    "name": "RecCli",
    "status": "alpha"
  },
  "tech_stack": {
    "key_dependencies": ["asciinema", "anthropic"]
  },
  "key_decisions": [
    {"decision": "Open source (MIT license)", "impact": "high"}
  ],
  "project_phases": {
    "current_phase": "Architecture",
    "next_milestones": [
      {"milestone": "MVP - Export Dialog"}
    ]
  }
}

Session Summary:
{
  "overview": "Implemented export dialog with format selection and embedding generation",
  "decisions": [
    {
      "decision": "Use sentence-transformers for local embeddings",
      "reasoning": "Faster than API calls, no rate limits, runs offline",
      "impact": "high"
    },
    {
      "decision": "Generate embeddings asynchronously",
      "reasoning": "Don't block UI during export",
      "impact": "medium"
    }
  ],
  "code_changes": [
    {
      "files": ["reccli.py", "export_dialog.py"],
      "description": "Added export dialog with .txt, .md, .devsession options",
      "type": "feature"
    },
    {
      "files": ["README.md"],
      "description": "Fixed typo in installation instructions",
      "type": "docs"
    }
  ],
  "problems_solved": [
    {
      "problem": "Webhook signature verification failing",
      "solution": "Use req.rawBody instead of req.body"
    }
  ]
}

Classify what should update the .devproject (project-level) vs what should stay in session only (session-level).
```

## 4. Manual Steering (Optional .devproject Updates)

**When:** User adds verbal instructions in verification dialog
**Input:** Current .devproject + session summary + user instruction
**Output:** Updated .devproject
**Model:** Claude Sonnet 4.5 or equivalent

### System Prompt

```
You are updating a project overview based on user instructions.

You will be given:
- Current .devproject
- Session summary
- User's verbal instruction for what to update

Apply the user's instruction to update the .devproject appropriately.

Guidelines:
- Follow the user's instruction precisely
- Maintain JSON schema validity
- Don't remove existing information unless instructed
- Add to existing lists/arrays, don't replace
- Keep the same structure and format
- If instruction is ambiguous, make best judgment

Output the COMPLETE updated .devproject JSON (not just changes).
```

### User Message Format

```
Current .devproject:
{
  "project": { ... },
  "architecture": {
    "overview": "Two-component system: RecCli + .devsession format"
  }
}

Session Summary:
{
  "overview": "Implemented vector embeddings with sentence-transformers"
}

User Instruction:
"Also update the architecture section to mention the new embedding pipeline and that we're using sentence-transformers for local vector generation"

Generate the updated .devproject JSON.
```

## 5. Compaction Summary (Internal)

**When:** Session hits 190K tokens, need to compact
**Input:** Conversation so far (up to 190K tokens)
**Output:** Interim summary for compaction only
**Model:** Claude Sonnet 4.5 or equivalent
**Note:** This is for internal compaction, not the final export summary

### System Prompt

```
You are generating an interim summary for context compaction.

The conversation has reached the token limit and needs to be compacted. Generate a brief summary of what has happened SO FAR in this session.

This is NOT the final session summary - the session will continue after compaction. Focus on:
- What has been accomplished
- Current task/goal
- Key decisions made
- Where we are in the work

Output concise JSON:
{
  "work_done": "Brief summary of what's been accomplished (2-3 sentences)",
  "current_task": "What we're working on right now (1 sentence)",
  "key_points": [
    "Important decision or change #1",
    "Important decision or change #2",
    "Important decision or change #3"
  ]
}

Be brief - this is just for context preservation, not final documentation.
```

### User Message Format

```
This session has reached the context limit. Summarize what we've accomplished so far:

[Conversation messages 1-190K tokens]

Generate the interim summary.
```

## 6. Context Reranking (Optional Enhancement)

**When:** After vector search, before loading context
**Input:** Vector search results + session context
**Output:** Reranked results with importance scores
**Model:** Fast model (Claude Haiku) or rule-based

### System Prompt (If Using AI)

```
You are reranking context search results by importance.

You will be given:
- Recent messages (what user is working on NOW)
- Vector search results (similar earlier messages)

Boost messages that contain:
- Decisions that affect current work
- Code changes to files being modified now
- Solutions to similar problems
- Architecture/design discussions
- Setup/configuration that's relevant

Lower priority for:
- Unrelated discussions
- Resolved issues (unless similar to current issue)
- Off-topic conversations

Output JSON array with reranked message IDs and scores:
[
  {"message_id": "msg_134", "score": 0.95, "reason": "Solved same webhook issue"},
  {"message_id": "msg_045", "score": 0.87, "reason": "Architecture decision relevant to current work"},
  {"message_id": "msg_201", "score": 0.72, "reason": "Related to current file"}
]
```

**Alternative:** Use rule-based reranking (no AI call needed):
```python
def rerank_by_importance(messages, recent_context):
    for msg in messages:
        score = msg.similarity_score  # Base: cosine similarity

        # Boost recent messages
        if is_recent(msg):
            score *= 1.2

        # Boost important types
        if is_decision(msg):
            score *= 1.3
        if is_code_change(msg):
            score *= 1.2
        if is_problem_solved(msg):
            score *= 1.25

        msg.final_score = score

    return sorted(messages, key=lambda m: m.final_score, reverse=True)
```

## 7. Compaction Context Loading (No Prompt Needed)

**When:** Session hits 190K tokens, loading compacted context
**Process:** Embedding + vector search (no LLM prompt)

### Algorithm

```python
def compact_and_load_context(session, num_recent=20):
    """
    Compact session context using recent messages as implicit goal
    No AI prompt needed - uses embeddings and vector search
    """
    # 1. Extract recent messages (implicit goal)
    recent_messages = session.conversation[-num_recent:]  # Last 20 messages

    # 2. Embed recent messages as query vector
    query_text = "\n".join([m.content for m in recent_messages])
    query_embedding = embedding_model.encode(query_text)  # sentence-transformers

    # 3. Search earlier conversation (exclude recent)
    earlier_messages = session.conversation[:-num_recent]

    # Calculate cosine similarity for each earlier message
    similarities = []
    for msg in earlier_messages:
        similarity = cosine_similarity(query_embedding, msg.embedding)
        similarities.append((msg, similarity))

    # Sort by similarity, take top 10-15
    relevant_messages = sorted(similarities, key=lambda x: x[1], reverse=True)[:15]

    # 4. Optional: Rerank by importance (see prompt #6)
    relevant_messages = rerank_by_importance(relevant_messages, recent_messages)

    # 5. Build compacted context
    compacted = {
        'summary': generate_interim_summary(session),  # Prompt #5
        'recent': recent_messages,  # Last 20 messages
        'relevant': relevant_messages[:10]  # Top 10 from vector search
    }

    # Total: ~2000 tokens (500 + 500 + 1000)
    return compacted
```

**Key Point:** This uses embeddings (numerical vectors), not LLM prompts. The "intelligence" comes from:
- Semantic embeddings (sentence-transformers)
- Cosine similarity (mathematical)
- Optional reranking (rules or AI)

## Usage Summary

| Operation | Prompt | Model | When |
|-----------|--------|-------|------|
| Session Summary | #1 | Sonnet 4.5 | Export |
| Project Init | #2 | Sonnet 4.5 | First session / Create project |
| Classification | #3 | Sonnet 4.5 | Export (before verification) |
| Manual Steering | #4 | Sonnet 4.5 | User adds instructions |
| Compaction Summary | #5 | Sonnet 4.5 | 190K tokens |
| Reranking | #6 | Haiku / Rules | After vector search (optional) |
| Vector Search | None | Embeddings | Compaction (mathematical) |

## Model Selection

**Recommended:**
- **Summary/Classification/Init:** Claude Sonnet 4.5 (best quality for structured output)
- **Manual Steering:** Claude Sonnet 4.5 (needs to understand intent)
- **Compaction Summary:** Claude Sonnet 4.5 or Haiku (Haiku for speed if quality ok)
- **Reranking:** Claude Haiku or rule-based (speed matters)
- **Embeddings:** sentence-transformers/all-MiniLM-L6-v2 (local, fast, free)

**Alternative Models:**
- GPT-4 / GPT-4 Turbo (if using OpenAI)
- Gemini Pro (if using Google)
- Llama 3 70B (if using local/open source)

## Token Budgets

| Operation | Input Tokens | Output Tokens | Cost (Claude) |
|-----------|--------------|---------------|---------------|
| Session Summary | ~50K | ~1K | ~$0.40 |
| Project Init | ~5K | ~1K | ~$0.05 |
| Classification | ~3K | ~500 | ~$0.03 |
| Manual Steering | ~3K | ~2K | ~$0.04 |
| Compaction Summary | ~190K | ~300 | ~$1.50 |
| Reranking (AI) | ~2K | ~200 | ~$0.02 |

**Per Session Cost:** ~$0.50-2.00 depending on session length

## Implementation Notes

### Error Handling

All prompts should include error handling:
```python
def generate_with_retry(prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = claude_api.generate(prompt)
            return json.loads(response)
        except json.JSONDecodeError:
            if attempt < max_retries - 1:
                continue
            else:
                # Fallback to manual parsing or default structure
                return generate_fallback_response()
```

### Response Validation

Validate all JSON responses:
```python
def validate_summary(summary_json):
    required_fields = ['overview', 'decisions', 'code_changes',
                      'problems_solved', 'open_issues', 'next_steps']
    for field in required_fields:
        if field not in summary_json:
            summary_json[field] = []
    return summary_json
```

### Caching

Cache prompts for cost optimization:
```python
# Claude supports prompt caching
# Mark static parts (system prompt) as cacheable
# Only dynamic parts (conversation) are charged

response = anthropic.messages.create(
    model="claude-sonnet-4.5",
    max_tokens=2000,
    system=[
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"}  # Cache this
        }
    ],
    messages=[...]
)
```

## Testing Prompts

Test each prompt with:
1. **Happy path:** Normal session with clear decisions
2. **Edge cases:** Session with no decisions, only bug fixes
3. **Malformed input:** Missing fields, truncated conversation
4. **Large input:** Near-max token limit
5. **Minimal input:** Very short session

Example test case:
```python
def test_session_summary_prompt():
    conversation = load_test_conversation("test_sessions/feature_implementation.json")
    summary = generate_session_summary(conversation)

    assert 'overview' in summary
    assert len(summary['decisions']) > 0
    assert summary['decisions'][0]['impact'] in ['low', 'medium', 'high']
    assert all('references' in d for d in summary['decisions'])
```

---

**Note:** All prompts are designed to be deterministic and produce structured JSON output. Adjust temperature (0.3-0.5) for consistent results.
