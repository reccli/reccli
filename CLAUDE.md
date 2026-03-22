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
  runtime/        cli.py, llm.py, config.py, tokens.py, chat_ui.py, wpc.py
  tests/          test_*.py (58 tests), fixtures/, benchmarks
  backend/        server.py (JSON-RPC bridge for TypeScript UI)
  ui/             TypeScript + Ink terminal UI (src/components/, src/bridge/)
docs/
  specs/          DEVSESSION_FORMAT.md, DEVPROJECT_FORMAT.md, MESSAGE_RANGE_SPEC.md
  architecture/   ARCHITECTURE.md, CONTEXT_LOADING.md, RECCLI_CLI_UI.md
  product/        RECCLI_ONE_PAGER.md, PROJECT_ONBOARDING.md
apps/web/         Legacy Next.js prototype (not active)
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
```

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
