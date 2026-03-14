# RecCli Documentation Restructuring Plan

**Status:** Active cleanup guide
**Last reviewed:** 2026-03-14
**Owner:** Codex + project maintainer

This file is the working plan for cleaning up the RecCli documentation set.

Keep [README.md](./README.md) as the stable index for readers. Use this file for cleanup rules, triage decisions, and migration progress.

## Completed In This Pass

- archived the phase-era implementation, walkthrough, and testing docs under `docs/archive/progress/phases/`
- archived the Phase 7 audit under `docs/archive/decisions/`
- archived historical implementation retrospectives under `docs/archive/implementation/`
- updated in-repo references so active docs point to the new archive locations

## Current Assessment

There are 44 Markdown files under `docs/` and 48 Markdown files in the repo overall.

The directory layout is mostly acceptable already:

- `product/`, `architecture/`, `specs/`, `reference/`, `implementation/`, `decisions/`, `progress/`, and `archive/` are reasonable top-level buckets
- an archive area already exists
- the main issue is content mixing, not folder shape

The cleanup should therefore prioritize:

- identifying which docs are actually canonical
- extracting durable ideas from phase-era or design-era documents
- moving historical material out of active paths when it is no longer current
- delaying renames until the surviving set is clear

## Working Rules

1. Do not mass-rename files during triage.
2. Keep one canonical document per topic whenever possible.
3. If a document contains one strong idea but is otherwise stale, extract the good material into a current doc and archive the original.
4. Historical implementation reports should not stay in active folders unless they are still the best explanation of current behavior.
5. Design docs for optional or future behavior must say so explicitly at the top.
6. Delete only after the useful content has either been extracted or confirmed redundant.

## Status Buckets

- `canonical`: primary source of truth for a topic
- `reference`: current and useful, but not the contract document
- `design`: intentional future-state or optional-flow material
- `historical`: useful context, but not current authority
- `archive`: already archived and should stay out of normal reading paths
- `delete-candidate`: redundant or low-value once extraction is complete

## Target Documentation Model

Use the existing folder structure, but enforce the role of each folder more strictly:

- `README.md` at repo root: project entrypoint
- `PROJECT_PLAN.md`: authoritative current project status
- `docs/README.md`: documentation index
- `docs/product/`: positioning and product intent
- `docs/architecture/`: current system shape and major mechanisms
- `docs/specs/`: normative data and contract docs
- `docs/reference/`: operational usage and environment details
- `docs/implementation/`: current subsystem walkthroughs only
- `docs/decisions/`: durable ADR-style records only
- `docs/progress/`: delivery history that is still worth keeping visible
- `docs/archive/`: superseded plans, walkthroughs, audits, and snapshots

## Triage Inventory

### Repo Entry Docs

| Path | Bucket | Action |
| --- | --- | --- |
| `README.md` | canonical | Keep as repo entrypoint; ensure claims stay aligned with live code and `PROJECT_PLAN.md`. |
| `PROJECT_PLAN.md` | canonical | Keep as project-status source of truth. |
| `email_templates.md` | historical | Move under `docs/archive/` or product/commercial docs later if still needed. |
| `apps/web/SETUP_GUIDE.md` | reference | Either rewrite as `apps/web/README.md` or archive if the web app is not active. |

### Docs Index And Product

| Path | Bucket | Action |
| --- | --- | --- |
| `docs/README.md` | canonical | Keep lean as the docs index; do not turn it into a cleanup worksheet. |
| `docs/product/RECCLI_ONE_PAGER.md` | canonical | Keep as product summary. |
| `docs/product/PROJECT_INITIALIZATION.md` | design | Keep, but only as optional future project-layer UX. |
| `docs/product/PROJECT_ONBOARDING.md` | design | Keep, but clearly future-facing. |

### Architecture

| Path | Bucket | Action |
| --- | --- | --- |
| `docs/architecture/ARCHITECTURE.md` | canonical | Keep; eventually split optional `.devproject` material if it keeps growing. |
| `docs/architecture/CONTEXT_LOADING.md` | canonical | Keep; ensure it remains aligned with middleware behavior. |
| `docs/architecture/RECCLI_CLI_UI.md` | reference | Keep as current UI architecture note. |

### Specs

| Path | Bucket | Action |
| --- | --- | --- |
| `docs/specs/README.md` | canonical | Keep as the specs entrypoint. |
| `docs/specs/DEVSESSION_FORMAT.md` | canonical | Keep as the main format spec. |
| `docs/specs/MESSAGE_RANGE_SPEC.md` | canonical | Keep as the range contract. |
| `docs/specs/UNIFIED_VECTOR_INDEX.md` | canonical | Keep as the indexing contract unless code diverges. |
| `docs/specs/DEVPROJECT_FILE.md` | design | Keep as optional future-state spec. |
| `docs/specs/SESSION_STORAGE.md` | design | Keep as optional storage strategy, not current behavior. |
| `docs/specs/schemas/devsession.schema.json` | historical | Keep, but label as draft unless brought back into sync with implementation. |

### Reference

| Path | Bucket | Action |
| --- | --- | --- |
| `docs/reference/NATIVE_LLM_GUIDE.md` | reference | Keep as the current CLI usage guide. |
| `docs/reference/SETTINGS_AND_AUTH.md` | reference | Keep as the current config/auth reference. |
| `docs/reference/API_KEY_SECURITY.md` | reference | Keep as the current security/storage note. |

### Implementation

| Path | Bucket | Action |
| --- | --- | --- |
| `docs/implementation/indexing/README.md` | reference | Keep as the current indexing walkthrough. |
| `docs/implementation/retrieval/README.md` | reference | Keep as the current retrieval walkthrough. |
| `docs/implementation/retrieval/STREAMING_HYBRID_RETRIEVAL.md` | reference | Keep if still aligned with current retrieval pipeline; otherwise fold into the retrieval README. |
| `docs/implementation/prompts/AI_PROMPTS.md` | historical | Verify against live prompts in code. If stale, extract only current prompt contracts and archive the rest. |

### Decisions

| Path | Bucket | Action |
| --- | --- | --- |
| `docs/decisions/RANGE_SEMANTICS_FIX.md` | canonical | Keep as a durable decision record. |

### Progress

| Path | Bucket | Action |
| --- | --- | --- |
| `docs/progress/README.md` | historical | Keep as a light index for delivery history. |
| `docs/progress/session-notes/SESSION_2025_11_07_SUMMARY.md` | historical | Keep as a session note if desired, but leave outside the canonical path. |

### Already Archived

| Path | Bucket | Action |
| --- | --- | --- |
| `docs/archive/product/MVP.md` | archive | Leave archived. |
| `docs/archive/decisions/DESIGN_DECISIONS.md` | archive | Leave archived. |
| `docs/archive/ideation/RecCli_intelligent_context_management_overview.md` | archive | Leave archived; mine only for origin-story context if needed. |
| `docs/archive/progress/DONE.md` | archive | Leave archived. |
| `docs/archive/progress/execution_summary.md` | archive | Leave archived. |
| `docs/archive/implementation/indexing/NUMPY_OPTIMIZATION_PLAN.md` | archive | Leave archived. |
| `docs/archive/implementation/indexing/VECTOR_OPTIMIZATION_STATUS.md` | archive | Leave archived. |
| `docs/archive/implementation/indexing/VECTOR_SEARCH_ANALYSIS.md` | archive | Leave archived. |
| `docs/archive/implementation/indexing/VECTOR_SEARCH_BOTTLENECK_ANALYSIS.md` | archive | Leave archived. |
| `docs/archive/implementation/indexing/VECTOR_SEARCH_COMPLETION.md` | archive | Leave archived. |
| `docs/archive/implementation/indexing/VECTOR_SEARCH_FINAL.md` | archive | Archived on 2026-03-14 after active indexing docs were updated. |
| `docs/archive/implementation/retrieval/SUMMARIZER_LINKING_INDEX_SAFEGUARDS.md` | archive | Archived on 2026-03-14; canonical range semantics stay in `docs/specs/MESSAGE_RANGE_SPEC.md`. |
| `docs/archive/decisions/PHASE_7_AUDIT.md` | archive | Archived on 2026-03-14 after extracting durable conclusions. |
| `docs/archive/progress/phases/PHASE_5_IMPLEMENTATION.md` | archive | Archived on 2026-03-14. |
| `docs/archive/progress/phases/PHASE_6_IMPLEMENTATION.md` | archive | Archived on 2026-03-14. |
| `docs/archive/progress/phases/PHASE_7_IMPLEMENTATION.md` | archive | Archived on 2026-03-14. |
| `docs/archive/progress/phases/PHASE_7_POST_AUDIT_FIXES.md` | archive | Archived on 2026-03-14. |
| `docs/archive/progress/phases/PHASE_7_TESTING.md` | archive | Archived on 2026-03-14. |
| `docs/archive/progress/phases/PHASE_7_WALKTHROUGH.md` | archive | Archived on 2026-03-14. |
| `docs/archive/progress/phases/PHASE_8_IMPLEMENTATION.md` | archive | Archived on 2026-03-14. |
| `docs/archive/progress/phases/PHASE_8_USAGE.md` | archive | Archived on 2026-03-14. |

## Batch Plan

### Batch 1: Freeze Authority

- keep `README.md`, `PROJECT_PLAN.md`, and `docs/README.md` as the stable entrypoints
- add explicit status headers to any active doc that still mixes current and future behavior
- avoid moving files in this batch

### Batch 2: Extract Logic Gems

- review `docs/implementation/prompts/AI_PROMPTS.md`
- review `docs/archive/implementation/retrieval/SUMMARIZER_LINKING_INDEX_SAFEGUARDS.md`
- review `docs/archive/implementation/indexing/VECTOR_SEARCH_FINAL.md`
- review `docs/archive/decisions/PHASE_7_AUDIT.md`
- extract durable facts into current architecture, spec, reference, or decision docs

### Batch 3: Reduce Active Surface Area

- completed on 2026-03-14 for the obvious historical phase docs and implementation retrospectives
- keep `docs/progress/README.md` as a short explanation of what remains in-progress versus archived

### Batch 4: Final Naming Cleanup

- rename only the surviving active docs that still have misleading names
- prefer descriptive names over phase names for anything that remains active
- skip renames that create churn without improving discoverability

## Metadata Standard For Kept Docs

Add this header block to active docs over time:

```md
**Status:** current | reference | design | historical | archived
**Last reviewed:** YYYY-MM-DD
**Owner:** name or team
```

For docs that supersede older files, add:

```md
**Replaces:** old-file.md
```

## Immediate Next Moves

1. Keep this plan as the working cleanup surface.
2. Leave `docs/README.md` as the stable index.
3. In the next pass, review `docs/implementation/prompts/AI_PROMPTS.md` against the live code.
4. Decide whether any remaining design docs should be split further between active and archive paths.
