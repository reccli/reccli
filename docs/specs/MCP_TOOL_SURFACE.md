# MCP Tool Surface

**Status:** Current — reflects the toolset exposed by `packages/reccli/mcp_server.py`.
**Transport:** stdio (MCP FastMCP).

This document catalogs every tool RecCli exposes through MCP, grouped by purpose.
Tools are the agent-callable surface of the tri-layer memory model defined in
`DEVSESSION_FORMAT.md` and `DEVPROJECT_FORMAT.md` — every tool either reads from,
writes to, or operates on the file formats those specs describe.

## Design principles

1. **Read before write.** Context, search, and inspection tools are idempotent
   and cheap. State-mutating tools (save, edit, pin, delete, rebuild) are
   explicit and distinct.
2. **Background work is always trackable.** Any tool that spawns a detached
   subprocess registers the PID via `register_bg_task` so it can be reaped at
   session start (`cleanup_bg_tasks`). Silent orphaning is a bug.
3. **Fallbacks never silently degrade.** A degraded code path (e.g., dense
   search unavailable, embedding dimension mismatch) must log through
   `_log_issue` so it surfaces via `list_issues`.
4. **User authority is preserved.** Edits to summary items respect the
   `locked` flag; pinned items always surface in context injection; edits
   to `.devproject` features go through the proposal system (not direct writes).

## Tool categories

### Context and onboarding

| Tool | Purpose |
|------|---------|
| `load_project_context` | Session opener: injects `.devproject` feature map + folder tree + Resume From brief + Pinned Memory + Last Session summary. Calls `cleanup_bg_tasks` early to reap dead subprocesses. |
| `preview_context` | Dry-run wrapper around `load_project_context` — returns the same content framed as a preview. Useful for validating the project map before starting work. |
| `project_init` | Bootstrap: scans codebase with Tree-sitter + LLM clustering, writes `.devproject`. Produces `features`, `hub_files`, `shared_infrastructure`, `unassigned` arrays. |
| `project_apply_clustering` | No-API-key fallback: persists in-conversation clustering JSON. |

### Search and retrieval

| Tool | Purpose |
|------|---------|
| `search_history` | Hybrid retrieval: dense embeddings + BM25 + RRF + boosts + badges. Validates embedding dimensions on index load; logs dense-search fallback to the issue queue. |
| `search_by_file` | Index-free scan for messages referencing a file path or basename. |
| `search_by_time` | Range query over session timestamps, with optional text filter. |
| `expand_search_result` | Drill-down: follows `message_range` (summary item), span boundaries (span), or ±context_window (message). Implements the spec's safe-fallback cascade when `message_range` is missing. |
| `inspect_result_id` | Read-only metadata lookup for a `result_id` — returns hit type, session, linked spans, message_range, pinned/locked flags. Does not return conversation content. |

### Session management

| Tool | Purpose |
|------|---------|
| `save_session_notes` | Persists a structured summary (decisions / code_changes / problems_solved / open_issues / next_steps) with BM25-computed message ranges. Triggers a background embed + index update. |
| `summarize_previous_session` | Retroactive summarization of an existing session that has a stub/missing summary. |
| `list_sessions` | Session catalog with filters: `query` (substring on stem/overview), `since` (ISO date), `has_summary` (bool). |
| `delete_session` | Archive (default) to `devsession/.archived/`, or hard-delete with `hard=True`. Moves artifact sidecars and embeddings sidecar together. Triggers index rebuild. |
| `recover_file` | File time-machine. Lists versions with `list_only=True`, returns any version with `version: int` (0 = latest). Reconstructs from `raw_response` when no inline content snapshot exists. |

### Memory curation

| Tool | Purpose |
|------|---------|
| `edit_summary_item` | Correct a summary item's primary text, confidence, reasoning, or solution. Respects the `locked` flag — locked items reject edits. |
| `pin_memory` | Toggle `pinned` on a summary item. Pinned items surface in every session-start context injection, regardless of retrieval. Respects the `locked` flag when unpinning. |

### Autonomy

| Tool | Purpose |
|------|---------|
| `evaluate_continuation` | Filters `session_signal.open` against `session_signal.goal` (expanded via the synonym map + substring containment) and returns `continue` / `wait` / `done`. Enables multi-step self-direction without user prompting. |

### Reasoning scaffolds

| Tool | Purpose |
|------|---------|
| `toggle_auto_reason` | Enable/disable regex-based intent detection → diverge-converge-validate scaffold injection at `UserPromptSubmit`. |
| `toggle_mmc` | Enable/disable 3-agent parallel reasoning with varied analytical lenses + consensus extraction. Supersedes auto-reason when enabled. |
| `toggle_session_signal` | Enable/disable session-signal forward-pointer injection (on by default). |
| `toggle_expanded_search` | Enable/disable synonym query expansion for `search_history` (BM25 variants fused; dense runs once). |
| `configure` | View all four toggles, or set one by name. |

### Diagnostics and maintenance

| Tool | Purpose |
|------|---------|
| `list_issues` | Read the accumulated issue log written by `_log_issue` across hooks, search, and background tasks. `clear=True` wipes after reading. |
| `rebuild_index` | Force full rebuild of the unified vector index. Use after an embedding provider change, or when `list_issues` reports dimension mismatches. Canonical data is untouched. |
| `retry_summarization` | Re-run background summarization + embedding on the most recent stub session (or a specific `session_id`). Spawns the same pipeline as end-of-session. |

## Tool-to-format traceability

Each tool touches one or both of the canonical formats:

| Format | Written by | Read by |
|--------|-----------|---------|
| `.devproject` | `project_init`, `project_apply_clustering`, proposal acceptance via `save_session_notes` | `load_project_context`, `preview_context` |
| `.devsession` conversation | Hooks (`session_recorder`), `save_session_notes` | `search_*`, `expand_search_result`, `recover_file`, `inspect_result_id` |
| `.devsession` summary | `save_session_notes`, `summarize_previous_session`, `edit_summary_item`, `pin_memory`, background `summarizer` | `load_project_context` (resume brief + pinned), `search_*`, `expand_search_result`, `inspect_result_id` |
| `.devsession` spans | `ensure_summary_span_links`, background `summarizer` | `search_*`, `expand_search_result`, `inspect_result_id` |
| Unified index (`index.json`) | `build_unified_index`, `update_index_with_new_session`, `rebuild_index` | `search_*`, `expand_search_result`, `inspect_result_id` |
| Issue log (`.issues.jsonl`) | `_log_issue` from any component | `list_issues` |
| Background task registry (`.bg_tasks.jsonl`) | `register_bg_task` | `cleanup_bg_tasks` |
| Archive directory (`.archived/`) | `delete_session` with hard=False | (manual inspection only) |

## Tool lifecycle and safety

**Read-only, idempotent** — safe to call any number of times, no side effects beyond reading:

- `load_project_context`, `preview_context`, `search_history`, `search_by_file`,
  `search_by_time`, `expand_search_result`, `inspect_result_id`, `list_sessions`,
  `list_issues`, `configure` (when called without args), `recover_file` (read-only).

**Writes to session/project state** — persist changes to `.devsession` or `.devproject`:

- `save_session_notes`, `summarize_previous_session`, `edit_summary_item`,
  `pin_memory`, `project_init`, `project_apply_clustering`, `configure` (with value).

**Destructive (with safety nets)** — reversible in most cases:

- `delete_session` (archive by default, `hard=True` required for deletion).
- `rebuild_index` (rebuilds from canonical data; no data loss).

**Spawns background work** — registers via `register_bg_task`:

- `save_session_notes`, `summarize_previous_session`, `retry_summarization`.

**Feature-gated** (toggleable via `configure`):

- `toggle_auto_reason`, `toggle_mmc`, `toggle_session_signal`, `toggle_expanded_search`.

## Error surfaces

Every MCP tool returns a string (plain text for humans, JSON for structured
payloads like `inspect_result_id` / `evaluate_continuation`). Errors that
would otherwise be silently swallowed should:

1. Log via `_log_issue(component, message, severity, project_root)`.
2. Return a user-visible explanation string.

The `list_issues` tool is the canonical place to diagnose tool failures.
Implementations MUST NOT swallow exceptions with bare `except: pass` in
user-facing code paths — the pattern is reserved only for last-resort
defensive layers (e.g., inside `_log_issue` itself).

## Future tools (not yet implemented)

Candidates discussed but deferred:

- **`unlock_item`** — explicit unlock path for `locked: true` items (currently
  requires manual `.devsession` edit).
- **`merge_sessions`** — collapse two sessions into one (useful after a
  crash-recovered split).
- **`export_session`** — render a session to markdown / HTML for sharing.
- **`diff_summaries`** — compare two summaries (e.g., same project before/after
  a feature landed) and surface what changed.

These are not in the current MCP surface.
