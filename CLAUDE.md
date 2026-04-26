# RecCli

RecCli is a temporal memory engine for AI coding agents. It implements a tri-layer memory system with temporal-semantic linking between layers.

## How to run

```bash
# Tests
PYTHONPATH=packages python3 -m unittest discover -s packages/reccli/tests -p 'test_*.py'
```

The current project structure, MCP tool surface, and per-file responsibilities are loaded automatically (via `load_project_context` and the MCP tool listing) — they are not duplicated here, since the source of truth is the codebase itself.

## Hooks (Claude Code integration)

Hooks in `~/.claude/settings.json` fire automatically on Claude Code events:

| Hook | What it does |
|------|-------------|
| `SessionStart` | Creates WAL, injects registered project list into context |
| `UserPromptSubmit` | Appends user prompt to WAL, checks pre-compaction threshold, injects auto-reason scaffold |
| `Stop` | Appends assistant response to WAL, extracts session-signal forward pointer |
| `PostToolUse` | Appends tool call + result to WAL |
| `PostCompact` | Flushes WAL to .devsession, validates .devproject, re-injects project context |
| `SessionEnd` | Finalizes WAL → .devsession, merges with existing summary, spawns background embed+summarize |

### Session lifecycle (MCP path)

1. **SessionStart** → project list injected, WAL created
2. **User selects project** → Claude calls `load_project_context` → breadcrumb written for hooks
3. **Every message** → appended to WAL via hooks
4. **~800K tokens** (Opus 4.7 1M context) → pre-compaction reminder injected via `UserPromptSubmit`
5. **User wraps up** → Claude proactively calls `save_session_notes` (SESSION RULE in start prompt)
6. **`/exit`** → WAL merged into .devsession, background: summarize + embed + index

### Auto-Reason and MMC

Both gated by config flags (off by default), triggered by intent detection at `UserPromptSubmit`.

- **Auto-Reason** injects a single-agent diverge→converge→validate scaffold. Fallback for when the agent is stuck.
- **MMC** supersedes auto-reason: instructs the main agent to spawn 3 parallel sub-agents, each with the full reasoning scaffold under a different analytical lens (debug: recent changes / data flow / assumptions; planning: simplicity / robustness / performance), then extract consensus.

### Session-Signal + Autonomous Continuation

A SESSION RULE injected at session start asks Claude to append `<!--session-signal: goal=<...> | resolved=<...> | open=<...>-->` to each response. The `Stop` hook extracts the tag, strips it from stored content, and saves the parsed signal as a `session_signal` field on the WAL record. The `goal` anchors the chain — open items must serve the goal, preventing carry-forward of unrelated items.

The `evaluate_continuation` MCP tool reads the latest signal, filters open items against the goal, and returns a continuation brief. A SESSION RULE (AUTONOMOUS CONTINUATION) tells the agent to call this after a reasoning chain with open items: `action=continue` self-directs to the next item; `action=wait`/`done` stops.

## Architecture: tri-layer memory

### Layer 1: .devproject (project level)
- Feature map loaded at the start of every session
- Generated from codebase scan via Tree-sitter + LLM clustering
- Bidirectional authority: .devsession proposes, .devproject confirms
- Located at `<project-root>/<project-name>.devproject`
- Contains: features, file boundaries, document links, session index, proposals

### Layer 2: .devsession summary (session level)
- Compacted working memory (~500-1000 tokens)
- Five universal categories: decisions, code_changes, problems_solved, open_issues, next_steps
- Every item links back to full conversation via span_ids + references + message_range
- Delta ops (add_item, update_item, close_span, merge_items, no_change) for incremental updates
- Battle-tested across 4 LLM providers (Claude, GPT-5, GPT-4, Haiku)

### Layer 3: .devsession full conversation (session level)
- Append-only chronological source of truth
- Every message gets a stable msg_* identifier
- Semantic spans (spn_*) laid over raw messages as a first-class linking layer

### Linking model
- **Temporal linking** between summary and conversation: message_range [start_index, end_index) enables O(1) array slicing for exact reconstruction
- **Semantic linking** between .devproject and summaries: feature_id is the cross-layer identifier
- **Span linking**: summary items carry span_ids pointing to semantic discussion regions; spans carry message_ids for precise membership

## Range semantics

All message ranges use inclusive-exclusive 0-based indexing: [start_index, end_index)
- msg_042 (42nd message) -> start_index=41, end_index=42 for a single message
- msg_042 to msg_050 -> start_index=41, end_index=50

## Development rules

- Do NOT restructure the package layout — it was recently reorganized
- Do NOT change the delta op mechanism (update_item, add_item, close_span, merge_items, no_change) — battle-tested across 4 LLM providers
- Do NOT change post-op deduplication logic — it solves a real cross-provider behavioral gap
- Do NOT remove the validation layer that rejects bad ops before state mutation
- Summary categories (decisions, code_changes, problems_solved, open_issues, next_steps) are intentionally universal — do not add project-specific categories
- The 3 FEATURE_DOMAIN_RULES in devproject.py (api_routes, jobs_and_workers, testing) are the only universal structural patterns — project-specific domains are discovered by LLM clustering, not hardcoded
- Conversation array is append-only once persisted; use tombstoning for deletions, not removal
