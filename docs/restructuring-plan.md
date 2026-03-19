# RecCli Documentation Restructuring Plan

**Status:** Active cleanup guide
**Last reviewed:** 2026-03-14
**Owner:** Codex + project maintainer

This file is the working plan for cleaning up the RecCli documentation set.

Keep [README.md](./README.md) as the stable index for readers. Use this file for cleanup rules and migration progress. Use [document-index.md](./document-index.md) as the per-document inventory source of truth.

## Completed In This Pass

- archived the phase-era implementation, walkthrough, and testing docs under `docs/archive/progress/phases/`
- archived the Phase 7 audit under `docs/archive/decisions/`
- archived historical implementation retrospectives under `docs/archive/implementation/`
- updated in-repo references so active docs point to the new archive locations
- created a full per-document inventory in `docs/document-index.md`

## Current Assessment

There are 45 Markdown files under `docs/` and 49 Markdown files in the repo overall.

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
- `historical`: useful context, but not current authority
- `delete`: low-value or redundant once extraction is complete

Archive is a location and an action, not a separate bucket.

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

The authoritative per-document inventory now lives in [document-index.md](./document-index.md).

That file records, for every current Markdown doc:

- first seen in git
- purpose
- bucket
- action
- cleanup notes

Use it before any further moves, merges, or deletions.

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
**Status:** canonical | reference | historical
**Last reviewed:** YYYY-MM-DD
**Owner:** name or team
```

For docs that supersede older files, add:

```md
**Replaces:** old-file.md
```

## Immediate Next Moves

1. Keep this plan as the working cleanup surface.
2. Keep [document-index.md](./document-index.md) updated before making further structural changes.
3. Leave `docs/README.md` as the stable reader-facing index.
4. In the next pass, review `docs/implementation/prompts/AI_PROMPTS.md` against the live code.
