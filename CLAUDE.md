# RecCli

RecCli is a temporal memory engine for AI coding agents. It implements a tri-layer memory system with temporal-semantic linking between layers.

## Project structure

```
packages/reccli/
  session/        devsession.py (file format manager), checkpoints.py, reindexing.py
  recording/      recorder.py, wal_recorder.py, parser.py, compactor.py
  summarization/  summarizer.py, summary_schema.py, summary_verification.py,
                  preemptive_compaction.py, compaction_log.py, redaction.py,
                  code_change_detector.py
  retrieval/      search.py, retrieval.py, streaming_retrieval.py,
                  embeddings.py, vector_index.py, memory_middleware.py
  project/        devproject.py
  hooks/          handle_event.py (hook dispatcher), session_recorder.py (WAL recorder),
                  context_injector.py (SessionStart/PostCompact context injection)
  runtime/        cli.py, llm.py, config.py, tokens.py, chat_ui.py, wpc.py
  mcp_server.py   MCP server (FastMCP) — tools for Claude Code integration
  tests/          test_*.py (58 tests), fixtures/, benchmarks
  backend/        server.py (JSON-RPC bridge for TypeScript UI)
  ui/             TypeScript + Ink terminal UI (src/components/, src/bridge/)
docs/
  specs/          DEVSESSION_FORMAT.md, DEVPROJECT_FORMAT.md, MESSAGE_RANGE_SPEC.md
  architecture/   ARCHITECTURE.md, CONTEXT_LOADING.md, RECCLI_CLI_UI.md
  product/        RECCLI_ONE_PAGER.md, PROJECT_ONBOARDING.md
archive/          Earlier implementations (v2 recorder, old src/)
```

## How to run

```bash
# Tests (all 58 should pass)
PYTHONPATH=packages python3 -m unittest discover -s packages/reccli/tests -p 'test_*.py'

# CLI
PYTHONPATH=packages python3 -m reccli.runtime.cli --help
PYTHONPATH=packages python3 -m reccli.runtime.cli project init
PYTHONPATH=packages python3 -m reccli.runtime.cli project show

# MCP server (for Claude Code integration)
claude mcp add --scope project reccli -- env PYTHONPATH=/path/to/reccli/packages python3 -m reccli.mcp_server
```

## MCP server

The MCP server (`mcp_server.py`) exposes these tools to Claude Code:

| Tool | Purpose |
|------|---------|
| `load_project_context` | Load .devproject feature map + file tree + last session summary |
| `project_init` | Scan codebase with Tree-sitter, cluster into features, create .devproject |
| `project_apply_clustering` | Apply Claude's in-conversation clustering (no-API-key fallback) |
| `search_history` | Hybrid search (dense + BM25) across all .devsession files |
| `expand_search_result` | Expand a search result to show full conversation context |
| `save_session_notes` | Save structured session summary — merges WAL conversation + summary |
| `summarize_previous_session` | Retroactively summarize an unsummarized previous session |
| `toggle_auto_reason` | Enable/disable auto-reason scaffold injection (off by default, for when agent is stuck) |
| `toggle_mmc` | Enable/disable MMC parallel reasoning — 3 agents with varied framings + consensus extraction |
| `toggle_expanded_search` | Enable/disable synonym-expanded multi-query search |
| `list_sessions` | Browse all recorded sessions — message counts, summary status, overview snippets |
| `configure` | View or change RecCli settings (auto_reason, mmc, session_signal, expanded_search) |
| `evaluate_continuation` | Accept goal + open_items from agent (not WAL), filter against goal, return continuation brief for autonomous mode |
| `list_issues` | Surface accumulated issue flags from hooks/tools — diagnose silent failures |

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
4. **~400K tokens** → pre-compaction reminder injected via `UserPromptSubmit`
5. **User wraps up** → Claude proactively calls `save_session_notes` (SESSION RULE in start prompt)
6. **`/exit`** → WAL merged into .devsession, background: summarize + embed + index

### Auto-Reason + MMC (hooks/auto_reason.py)

Detects user intent (debug/planning) from prompt text via regex heuristics at `UserPromptSubmit`. Two modes:

- **Auto-Reason** (`auto_reason` config, off by default): Injects a single-agent diverge→converge→validate scaffold. Fallback for when the agent is stuck.
- **MMC** (`mmc` config, off by default): Supersedes auto-reason. Injects instructions for 3 parallel agents, each running the full reasoning scaffold with a different analytical lens (debug: recent changes / data flow / assumptions; planning: simplicity / robustness / performance). Main agent then extracts consensus from their independent conclusions. Self-consistency sampling applied to coding tasks.

### Session-Signal (forward pointers) + Autonomous Continuation

Injects a SESSION RULE asking Claude to append `<!--session-signal: goal=<session goal> | resolved=... | open=...-->` to each response. The `goal` field anchors the signal chain to the user's current intent — open items must be in service of the goal, preventing carry-forward of unrelated items from previous sessions. The `Stop` hook extracts this tag, strips it from stored content, and saves the parsed signal (including goal) as a `session_signal` field on the WAL record. Gated by `session_signal` config (on by default).

The `evaluate_continuation` MCP tool reads the latest signal, filters open items against the goal via keyword overlap, and returns a continuation brief. A SESSION RULE (AUTONOMOUS CONTINUATION) tells the agent to call this tool after completing a reasoning chain with open items. If it returns `action=continue`, the agent self-directs to the next item. If `action=wait` or `action=done`, the agent stops and lets the user direct.

### Expanded Search (retrieval/query_expansion.py)

Expands queries with synonyms from a software engineering vocabulary, runs multiple BM25 passes, and fuses results. Dense search runs once (embeddings already capture synonymy). Gated by `expanded_search` config (off by default).

### Embedding providers

OpenAI text-embedding-3-small (1536D) when API key is configured, otherwise BM25-only search.
Embeddings stored in sidecar `.npy` files (binary, memory-mapped) not inline JSON.

### API key resolution

Environment variables first (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`), then `~/reccli/config.json`.

### Project registry

`~/.reccli/projects.json` tracks projects initialized with `project_init`. Used by SessionStart hook to list available projects.

## Architecture: tri-layer memory

### Layer 1: .devproject (project level)
- Feature map loaded at the start of every session
- Generated from codebase scan via Tree-sitter + LLM clustering (`devproject.py`)
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

### Context injection flow
1. Session start: load .devproject (features, file tree, docs) via memory_middleware._load_project_overview()
2. Load session summary + recent messages
3. Vector search for relevant history from earlier messages
4. After compaction: re-inject .devproject + summary + recent + relevant spans

## Key files

| File | Role |
|------|------|
| `session/devsession.py` | DevSession class: load/save .devsession, checksum verification, summary frontier tracking |
| `project/devproject.py` | DevProjectManager: project init from codebase (Tree-sitter), proposal lifecycle, file path validation, compact tree generation |
| `summarization/summarizer.py` | SessionSummarizer: LLM prompts for summary generation and delta op patching, two-stage pipeline, post-op deduplication |
| `summarization/summary_schema.py` | Schema helpers, ID generation, span synthesis from summary items |
| `summarization/summary_verification.py` | SummaryVerifier: validates references, ranges, span links against conversation |
| `summarization/preemptive_compaction.py` | PreemptiveCompactor: auto-compact when approaching context limit, orchestrates summary + embeddings + vector search + WPC |
| `retrieval/memory_middleware.py` | MemoryMiddleware: hydrate_prompt() builds context from summary + recent + vector search + project overview |
| `retrieval/search.py` | Hybrid search: dense embeddings + BM25 + reciprocal rank fusion |
| `runtime/cli.py` | CLI entry point with subcommands: chat, record, project (init/show/sync/apply/reject), search, index, compact |
| `runtime/config.py` | Config: API keys, sessions directory, default model |

## Range semantics

All message ranges use inclusive-exclusive 0-based indexing: [start_index, end_index)
- msg_042 (42nd message) -> start_index=41, end_index=42 for a single message
- msg_042 to msg_050 -> start_index=41, end_index=50

## Development rules

- Do NOT restructure the package layout -- it was recently reorganized
- Do NOT change the delta op mechanism (update_item, add_item, close_span, merge_items, no_change) -- battle-tested across 4 LLM providers
- Do NOT change post-op deduplication logic -- it solves a real cross-provider behavioral gap
- Do NOT remove the validation layer that rejects bad ops before state mutation
- Summary categories (decisions, code_changes, problems_solved, open_issues, next_steps) are intentionally universal -- do not add project-specific categories
- The 3 FEATURE_DOMAIN_RULES in devproject.py (api_routes, jobs_and_workers, testing) are the only universal structural patterns -- project-specific domains are discovered by LLM clustering, not hardcoded
- Conversation array is append-only once persisted; use tombstoning for deletions, not removal
