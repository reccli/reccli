# RecCli Document Inventory

**Status:** Working documentation inventory
**Last reviewed:** 2026-03-14
**Scope:** 49 Markdown docs currently in the repo

This file is the index-first control surface for documentation cleanup.

Use [README.md](./README.md) for reader navigation. Use this file for inventory, classification, and cleanup decisions.

Dates below are **first seen in git**, not guaranteed filesystem creation dates. For docs created in the current cleanup pass and not yet committed, the date is marked as `2026-03-14 (working tree)`.

## Working Buckets

- `canonical`: active source of truth for a topic
- `reference`: useful active documentation, but not the primary contract
- `historical`: worth keeping for context, but not current authority
- `delete`: low-value or redundant; remove once any remaining useful content is extracted

## Repo Entry Docs

| Path | First seen | Purpose | Bucket | Action | Notes |
| --- | --- | --- | --- | --- | --- |
| `README.md` | 2025-10-13 | Repo entrypoint and project framing | canonical | keep | Should stay aligned with `PROJECT_PLAN.md` and active docs. |
| `PROJECT_PLAN.md` | 2025-11-01 | Current project status and implementation truth | canonical | keep | Best current status doc in the repo. |
| `email_templates.md` | 2025-10-13 | Product/commercial email copy | delete | delete or relocate | Not part of the memory-engine documentation set. |
| `apps/web/SETUP_GUIDE.md` | 2025-10-13 | Web app component/setup note | reference | rewrite or archive | Keep only if the web app is still an active surface. |

## Documentation Meta

| Path | First seen | Purpose | Bucket | Action | Notes |
| --- | --- | --- | --- | --- | --- |
| `docs/README.md` | 2026-03-13 | Reader-facing docs index | canonical | keep | Primary navigation page for `docs/`. |
| `docs/document-index.md` | 2026-03-14 (working tree) | Full document inventory with buckets and actions | reference | keep during cleanup | This file is the inventory source of truth. |
| `docs/restructuring-plan.md` | 2026-03-14 (working tree) | Cleanup workflow, batch plan, and rules | reference | keep during cleanup | Archive or condense after restructure stabilizes. |

## Product

| Path | First seen | Purpose | Bucket | Action | Notes |
| --- | --- | --- | --- | --- | --- |
| `docs/product/RECCLI_ONE_PAGER.md` | 2026-03-13 | Product-level summary of what RecCli is | canonical | keep | Best short positioning doc. |
| `docs/product/PROJECT_INITIALIZATION.md` | 2025-10-28 | Optional future project-selection/init flow | reference | keep with status note | Useful, but not current shipped behavior. |
| `docs/product/PROJECT_ONBOARDING.md` | 2025-10-29 | Optional future onboarding/scoping flow | reference | keep with status note | Future-state product doc, not startup contract. |
| `docs/product/AGENT_HARNESS.md` | 2026-04-25 (working tree) | Feature-scoped multi-agent audit harness (MCP-implemented) | reference | keep current | RecCli-generic design plus current `audit_feature`/`replay_audit_agent` behavior: PII-redacted context packs, sequential-by-default dispatch with quota abort, audit_analysis overlap measurement. |

## Architecture

| Path | First seen | Purpose | Bucket | Action | Notes |
| --- | --- | --- | --- | --- | --- |
| `docs/architecture/ARCHITECTURE.md` | 2025-10-28 | Main system architecture and memory model | canonical | keep | Split optional `.devproject` material later if needed. |
| `docs/architecture/CONTEXT_LOADING.md` | 2025-10-28 | Current context-loading strategy and layers | canonical | keep | Core architectural explanation for memory hydration. |
| `docs/architecture/RECCLI_CLI_UI.md` | 2025-11-09 | Terminal UI architecture note | reference | keep | Current and useful, but not top-level system contract. |

## Specs

| Path | First seen | Purpose | Bucket | Action | Notes |
| --- | --- | --- | --- | --- | --- |
| `docs/specs/README.md` | 2026-03-13 | Specs entrypoint and reading order | reference | keep | Navigation doc for the specs set. |
| `docs/specs/DEVSESSION_FORMAT.md` | 2025-10-28 | `.devsession` format contract | canonical | keep | One of the strongest current specs. |
| `docs/specs/MESSAGE_RANGE_SPEC.md` | 2025-11-09 | `message_range` semantics contract | canonical | keep | Canonical range-semantics source. |
| `docs/specs/UNIFIED_VECTOR_INDEX.md` | 2025-10-29 | Cross-session index contract and rationale | canonical | keep | Still central to retrieval/index understanding. |
| `docs/specs/DEVPROJECT_FORMAT.md` | 2025-10-28 | Optional `.devproject` companion format | reference | keep with status note | Forward-looking spec, not required current behavior. |
| `docs/specs/SESSION_STORAGE.md` | 2025-10-28 | Optional future storage strategy | reference | keep with status note | Design-facing, not mainline implementation. |

## Reference

| Path | First seen | Purpose | Bucket | Action | Notes |
| --- | --- | --- | --- | --- | --- |
| `docs/reference/NATIVE_LLM_GUIDE.md` | 2025-11-01 | Current CLI usage guide | reference | keep | Best active operational guide for chat usage. |
| `docs/reference/SETTINGS_AND_AUTH.md` | 2025-10-28 | Current configuration and auth reference | reference | keep | Good operational reference with future notes clearly marked. |
| `docs/reference/API_KEY_SECURITY.md` | 2025-11-09 | Current API-key storage/security note | reference | keep | Important operational note; keep aligned with code. |

## Integrations

| Path | First seen | Purpose | Bucket | Action | Notes |
| --- | --- | --- | --- | --- | --- |
| `docs/integrations/playwright.md` | 2026-04-26 (working tree) | Playwright screenshot/UX-test integration patterns | reference | keep current | Two flavors: Bash + CLI for single-shot iteration, `@playwright/mcp` for persistent context. RecCli does not bundle Playwright; agent installs on demand. |

## Implementation

| Path | First seen | Purpose | Bucket | Action | Notes |
| --- | --- | --- | --- | --- | --- |
| `docs/implementation/indexing/README.md` | 2026-03-14 (working tree) | Current indexing implementation walkthrough | reference | keep | Best current entrypoint for indexing internals. |
| `docs/implementation/retrieval/README.md` | 2026-03-14 (working tree) | Current retrieval implementation walkthrough | reference | keep | Best current retrieval entrypoint. |
| `docs/implementation/retrieval/STREAMING_HYBRID_RETRIEVAL.md` | 2025-11-09 | Progressive retrieval design and flow | reference | keep | Keep while it still matches live retrieval behavior. |
| `docs/implementation/prompts/AI_PROMPTS.md` | 2025-10-28 | Mixed prompt catalog spanning live and design-era flows | historical | extract then archive | Live prompt truth now lives mostly in code. |

## Decisions

| Path | First seen | Purpose | Bucket | Action | Notes |
| --- | --- | --- | --- | --- | --- |
| `docs/decisions/RANGE_SEMANTICS_FIX.md` | 2025-11-09 | ADR-style record for range-semantics fix | canonical | keep | Durable design-fix record. |

## Progress

| Path | First seen | Purpose | Bucket | Action | Notes |
| --- | --- | --- | --- | --- | --- |
| `docs/progress/README.md` | 2026-03-14 (working tree) | Progress/history index for visible time-bound docs | historical | keep | Light wrapper around archived progress material. |
| `docs/progress/session-notes/SESSION_2025_11_07_SUMMARY.md` | 2025-11-09 | Session-level delivery summary | historical | keep | Useful session snapshot; not canonical system documentation. |

## Archive: Decisions

| Path | First seen | Purpose | Bucket | Action | Notes |
| --- | --- | --- | --- | --- | --- |
| `docs/archive/decisions/DESIGN_DECISIONS.md` | 2025-11-01 | Earlier design-decision log | historical | keep archived | Good rationale source, but no longer current authority. |
| `docs/archive/decisions/PHASE_7_AUDIT.md` | 2025-11-09 | Phase 7 self-audit report | historical | keep archived | Useful for implementation history, not current decision canon. |

## Archive: Product And Ideation

| Path | First seen | Purpose | Bucket | Action | Notes |
| --- | --- | --- | --- | --- | --- |
| `docs/archive/product/MVP.md` | 2025-10-29 | Earlier MVP and roadmap shape | historical | keep archived | Preserves product evolution context. |
| `docs/archive/ideation/RecCli_intelligent_context_management_overview.md` | 2025-11-09 | Origin-story ideation for the memory model | historical | keep archived | Messy, but contains conceptual starting points. |

## Archive: Implementation

| Path | First seen | Purpose | Bucket | Action | Notes |
| --- | --- | --- | --- | --- | --- |
| `docs/archive/implementation/indexing/VECTOR_SEARCH_FINAL.md` | 2025-11-21 | Final indexing optimization retrospective | historical | keep archived | Best historical retrospective in this cluster. |
| `docs/archive/implementation/retrieval/SUMMARIZER_LINKING_INDEX_SAFEGUARDS.md` | 2025-11-09 | Retrieval-linking safeguards review | historical | keep archived | Some rationale still useful beyond the canonical spec. |
| `docs/archive/implementation/indexing/NUMPY_OPTIMIZATION_PLAN.md` | 2025-11-21 | Early indexing optimization plan | delete | delete after quick spot-check | Planning snapshot is likely redundant now. |
| `docs/archive/implementation/indexing/VECTOR_OPTIMIZATION_STATUS.md` | 2025-11-21 | Intermediate optimization status snapshot | delete | delete after quick spot-check | Overlaps heavily with `VECTOR_SEARCH_FINAL.md`. |
| `docs/archive/implementation/indexing/VECTOR_SEARCH_ANALYSIS.md` | 2025-11-21 | Early performance analysis snapshot | delete | delete after quick spot-check | Likely superseded by later retrospective. |
| `docs/archive/implementation/indexing/VECTOR_SEARCH_BOTTLENECK_ANALYSIS.md` | 2025-11-21 | Bottleneck snapshot during optimization pass | delete | delete after quick spot-check | Probably too granular to keep long term. |
| `docs/archive/implementation/indexing/VECTOR_SEARCH_COMPLETION.md` | 2025-11-21 | Intermediate completion/update snapshot | delete | delete after quick spot-check | Redundant with final retrospective. |

## Archive: Progress

| Path | First seen | Purpose | Bucket | Action | Notes |
| --- | --- | --- | --- | --- | --- |
| `docs/archive/progress/phases/PHASE_5_IMPLEMENTATION.md` | 2025-11-09 | Phase 5 implementation plan/report | historical | keep archived | Contains rationale around index design and search behavior. |
| `docs/archive/progress/phases/PHASE_6_IMPLEMENTATION.md` | 2025-11-09 | Phase 6 middleware/context-loading plan | historical | keep archived | Still useful for memory-model rationale. |
| `docs/archive/progress/phases/PHASE_7_IMPLEMENTATION.md` | 2025-11-09 | Phase 7 implementation report | historical | keep archived | Preserves compaction rollout context. |
| `docs/archive/progress/phases/PHASE_7_POST_AUDIT_FIXES.md` | 2025-11-09 | Follow-up fixes after the Phase 7 audit | historical | keep archived | Useful paired context for the audit. |
| `docs/archive/progress/phases/PHASE_7_WALKTHROUGH.md` | 2025-11-09 | User-facing walkthrough of Phase 7 compaction flow | historical | keep archived | May still be useful as a historical explainer. |
| `docs/archive/progress/phases/PHASE_8_IMPLEMENTATION.md` | 2025-11-21 | Phase 8 retrieval tool implementation report | historical | keep archived | Good retrieval-history reference. |
| `docs/archive/progress/phases/PHASE_7_TESTING.md` | 2025-11-09 | Phase-era testing guide | delete | delete after extracting any surviving regression scenarios | Mostly stale command-era testing detail. |
| `docs/archive/progress/phases/PHASE_8_USAGE.md` | 2025-11-21 | Phase-era retrieval usage guide | delete | delete after confirming overlap with `NATIVE_LLM_GUIDE.md` | Strong overlap with current operational docs. |
| `docs/archive/progress/DONE.md` | 2025-11-01 | One-off “complete” snapshot | delete | delete | Superseded by `PROJECT_PLAN.md` and archived phase docs. |
| `docs/archive/progress/execution_summary.md` | 2025-11-09 | Older execution summary snapshot | delete | delete | Redundant with `PROJECT_PLAN.md` and other archived progress docs. |

## Delete Queue

These are the clearest reduction targets once you are comfortable that no remaining ideas need extraction:

- `email_templates.md`
- `docs/archive/implementation/indexing/NUMPY_OPTIMIZATION_PLAN.md`
- `docs/archive/implementation/indexing/VECTOR_OPTIMIZATION_STATUS.md`
- `docs/archive/implementation/indexing/VECTOR_SEARCH_ANALYSIS.md`
- `docs/archive/implementation/indexing/VECTOR_SEARCH_BOTTLENECK_ANALYSIS.md`
- `docs/archive/implementation/indexing/VECTOR_SEARCH_COMPLETION.md`
- `docs/archive/progress/phases/PHASE_7_TESTING.md`
- `docs/archive/progress/phases/PHASE_8_USAGE.md`
- `docs/archive/progress/DONE.md`
- `docs/archive/progress/execution_summary.md`

## Immediate Follow-Up

1. Keep this inventory updated as the source of truth for per-document decisions.
2. Use [restructuring-plan.md](./restructuring-plan.md) for batch execution order.
3. Extract anything valuable from `docs/implementation/prompts/AI_PROMPTS.md` before archiving it.
4. Do one quick final spot-check on the `delete` bucket before physically removing files.
