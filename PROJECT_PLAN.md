# RecCli Project Plan

**Single source of truth for project status and completion.**

Last updated: 2026-03-14

This file supersedes the older phase-era planning narrative that accumulated across the project. Historical implementation notes still exist under `docs/progress/`, but this document is the authoritative statement of:

- what RecCli is
- what is implemented in the current codebase
- what is only partially implemented
- what remains before the project can be considered production-ready against its stated goals

## Project Definition

RecCli is a temporal memory engine for coding agents.

Its core idea is a linked memory system with three conceptual layers:
1. full conversation history as the durable source of truth (in .devsession)
2. compacted session summary as bounded working memory (in .devsession)
3. a project-level outline for cross-session continuity (.devproject)




The key differentiator is temporal semantic linking between the layers (primarily temporal linking between summary and full conversation layer and primarily semantic between .devproject file and summary layer )so that summaries can recover exact prior reasoning from the original conversation as well as `.devproject` as the primary context layer that's inserted on every new "dev session" so the LLM agent has a path for understanding and navigation for any aspect the user then works on'.

## Product Goals

The project goals are:

- record terminal and chat sessions into a durable `.devsession` format
- preserve full reasoning history while supporting aggressive compaction
- provide fast retrieval across current and prior sessions
- support exact drill-down from summary items back to source discussion
- keep active context small while retaining effective long-horizon recall
- initialize the .devproject file through opengraph import analysis with LLM clustering for feature extraction


## Current Architecture

The live implementation is centered in:

- `packages/reccli-core/reccli/`

The primary runtime surfaces are:

- Python CLI entry point: `python3 -m reccli.cli`
- TypeScript + Ink chat UI launched from the Python CLI
- JSON-RPC bridge between the UI and Python backend
- `.devsession` files under the configured sessions directory

The UI/backend bridge now expects and has a packaged backend module at:

- `packages/reccli-core/backend/server.py`

## Implementation Status

### Implemented Core

The following are implemented in the current codebase:

| Area | Status | Notes |
| --- | --- | --- |
| Native PTY/WAL recording | Implemented | `recorder.py`, `wal_recorder.py` |
| `.devsession` persistence | Implemented | `devsession.py` |
| Conversation parsing | Implemented | `parser.py` |
| Token counting | Implemented | `tokens.py` |
| Native LLM session support | Implemented | `llm.py` |
| TypeScript + Ink chat UI | Implemented | `ui/src`, launched via `chat_ui.py` |
| Python UI backend bridge | Implemented | `packages/reccli-core/backend/server.py`, `ui/src/bridge/python.ts` |
| Summary schema and verification | Implemented | `summary_schema.py`, `summary_verification.py` |
| Span-linked `.devsession` runtime | Implemented | first-class `spans`, explicit `message_ids`, `summary_sync`, append-only/tombstone semantics in `devsession.py` |
| Open-tail spans and frontier tracking | Implemented | closed summary frontier plus open active tail support |
| Embedding generation | Implemented | `embeddings.py` |
| Unified vector index | Implemented | `vector_index.py` |
| Hybrid search and expansion | Implemented | `search.py` |
| Two-level retrieval helpers | Implemented | `retrieval.py` |
| Memory middleware hydration | Implemented | `memory_middleware.py` |
| Streaming retrieval | Implemented | `streaming_retrieval.py` |
| Preemptive compaction | Implemented | `preemptive_compaction.py` |
| Checkpoints | Implemented | `checkpoints.py` |
| Episodes | Implemented | `episodes.py`, CLI integration in `cli.py` |

### Partially Implemented

These areas exist, but are not complete enough to count as finished product surfaces:

| Area | Status | Notes |
| --- | --- | --- |
| `.devproject` loading | Partial | Middleware can opportunistically load an existing `.devproject` and on "init" if none existing"'"|
| `.devproject` creation and maintenance | Not complete | Code is written for it however it needs improvement in feature extraction |
| Project onboarding/init UX | Not complete | Product/design docs exist, main CLI flow does not |
| Constrained delta summary updates | Partial | op-based incremental update flow exists for `decisions`, `open_issues`, and `next_steps`, with pre-apply validation and silent-loss checks, but it still depends on provider-backed LLM reliability |
| LLM-authored reasoned summary extraction | Partial | deterministic summary patching is real, but `stage2_generate_summary()` is still placeholder for broader narrative extraction |
| Rolling compaction heuristics | Partial | frontier-driven compaction and open tails exist, but semantic/task-complete triggers are not implemented |
| Tombstone-aware index maintenance | Partial | stale results are filtered against canonical sessions, but sidecar/index rows are not hard-deleted in place |
| Packaging/release polish | Partial | Local runtime works, but install/release surface is still rough |
| Automated test suite | Partial | A small discoverable regression suite exists; most older tests are executable scripts rather than integrated automation |

### Explicitly Not Complete

These should not be described as shipped:

- mandatory `.devproject` project layer
- project selector dropdown
- onboarding interview flow
- hosted auth or OAuth
- a polished release/install story for end users
- OpenClaw integration

## Current CLI Surface

The current top-level commands are:

- `chat`
- `ask`
- `config`
- `record`
- `list`
- `show`
- `export`
- `watch`
- `index`
- `search`
- `expand`
- `embed`
- `hydrate`
- `hydrate-stream`
- `compact`
- `check-tokens`
- `checkpoint`
- `episode`

The current config surface supports:

- `--anthropic-key`
- `--openai-key`
- `--default-model`

Config is stored in:

- `~/reccli/config.json`

Sessions default to:

- `~/reccli/sessions/`

## Verified During This Coherence Pass

The following items were directly verified or corrected during the current repo cleanup:

### Fixed Runtime Issues

1. `DevSession.save()` now supports saving back to the loaded session path when no explicit path is supplied.
2. `DevSession` now persists checkpoint data instead of silently dropping it.
3. Retrieval range handling was corrected so invalid stored indices do not produce negative counts or empty previews when message IDs are available.
4. The packaged Python backend for the TypeScript UI now exists at the path the UI bridge expects.
5. The package no longer emits a `runpy` warning when invoked as `python -m reccli.cli`.
6. Session commands now use the configured sessions directory rather than bypassing `Config`.
7. `.devsession` spans now carry explicit `message_ids`, so overlapping spans are distinguishable during retrieval and verification.
8. Incremental summary updates now support constrained patch operations and mechanically reject silent item loss across compaction.
9. LLM-emitted delta ops are now validated before mutation, rejecting invalid targets, invalid span closure, and obvious semantic duplicates.

### Validation Performed

The following validation was run successfully in the current environment:

- `python3 -m unittest discover -s packages/reccli-core/tests -p 'test_*.py'`
  - 18 discoverable regression tests passing
- executable validation scripts:
  - `packages/reccli-core/tests/test_token_counting.py`
  - `packages/reccli-core/tests/test_summarization.py`
  - `packages/reccli-core/tests/test_temporal_and_breakeven.py`
  - `packages/reccli-core/tests/test_two_level_retrieval.py`
- backend ping smoke test:
  - `python3 packages/reccli-core/backend/server.py` with a `ping` request returned ready
- manual real-provider harness scaffold:
  - `packages/reccli-core/tests/manual_provider_delta_harness.py`
  - `packages/reccli-core/tests/provider_delta_cases.json`


Those remain production-readiness tasks, not implementation unknowns.

## Production Readiness Status

RecCli is now best described as:

- implemented at the core memory-engine level
- coherent at the docs/code/plan level
- close to production-ready for developer use
- not yet fully productized for general external distribution

### Current Assessment

| Requirement | Status |
| --- | --- |
| Core memory engine implemented | Yes |
| Docs and plan coherent with code | Yes |
| Core regressions covered by automated tests | Partial |
| End-to-end interactive chat install path validated | Partial |
| Project-layer UX shipped | No, and not required for core production readiness |

## Remaining Work

The remaining work is now about stabilization and distribution, not inventing the core architecture.

### Priority 1: Stabilization

- keep `PROJECT_PLAN.md` aligned with the code after each meaningful change
- expand the discoverable automated test suite beyond the current regression set
- add one or two end-to-end smoke paths that exercise the documented CLI/UI startup path

### Priority 2: Packaging

- define the supported install flow for Python + Node dependencies
- ensure `reccli chat` works from that flow without manual path patching
- tighten dependency manifests and release notes

### Priority 3: Product Surface

- decide whether the main product surface remains the local CLI, an integration layer, or both
- treat `.devproject` as the primary context layer that's inserted on every new "dev session" so the LLM agent has a path for understanding and navigation for any aspect the user then works on'
- if project-layer work resumes, implement generation/refinement after project attachment rather than only through first-run onboarding

### Priority 4: Integration

- build the OpenClaw-facing integration as a plugin/context-engine surface
- expose search, retrieval, compaction, and lineage as the core external value

## Deferred Work

The following are valid future work, but they are not blockers for calling the memory engine complete:

- richer project onboarding UX
- project selector UI
- keychain-backed secret storage
- OAuth flows
- hosted service or remote sync
- broader product/UI polish

## Repository Guidance

For the current canonical understanding of the project, use these files first:

- `README.md`
- `PROJECT_PLAN.md`
- `docs/README.md`
- `docs/architecture/ARCHITECTURE.md`
- `docs/architecture/CONTEXT_LOADING.md`
- `docs/architecture/RECCLI_CLI_UI.md`
- `docs/specs/DEVSESSION_FORMAT.md`
- `docs/specs/UNIFIED_VECTOR_INDEX.md`

Historical phase narratives and one-off delivery notes should be treated as supporting context only:

- `docs/progress/`
- `docs/archive/`

## Completion Definition

For purposes of this codebase, the project should be considered complete at the core-technology level when:

- `.devsession` is stable
- recording, summarization, retrieval, indexing, and compaction are coherent and validated
- the docs describe the real system rather than historical snapshots

That state is now substantially achieved.

What remains is not defining the memory engine. What remains is hardening, packaging, and choosing the best distribution surface for it.
