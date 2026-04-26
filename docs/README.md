# RecCli Docs

This directory is organized by document function, not by chronology.

Use this page as the canonical documentation index for project readers.

Implementation note: the current codebase is strongest at the `.devsession`, retrieval, indexing, and compaction layers. `.devproject` should be read as an optional project-outline layer, not a startup requirement. Some product docs describe future onboarding and generation flows rather than features already wired into the main CLI.

Documentation maintenance note: cleanup rules and triage decisions live in [restructuring-plan.md](./restructuring-plan.md). Keep this file focused on the stable reading path.

## Start Here

- [Product One-Pager](./product/RECCLI_ONE_PAGER.md)
- [Architecture](./architecture/ARCHITECTURE.md)
- [`.devsession` Specification](./specs/DEVSESSION_FORMAT.md)
- [Unified Vector Index](./specs/UNIFIED_VECTOR_INDEX.md)
- [Project Plan](../PROJECT_PLAN.md)

## Documentation Cleanup

- [Document Inventory](./document-index.md) - full inventory with first-seen dates, buckets, actions, and notes
- [Restructuring Plan](./restructuring-plan.md) - working inventory, triage rules, and migration batches

## Product

Use these to understand what RecCli is and how it should be positioned.

- [RECCLI_ONE_PAGER.md](./product/RECCLI_ONE_PAGER.md)
- [PROJECT_INITIALIZATION.md](./product/PROJECT_INITIALIZATION.md)
- [PROJECT_ONBOARDING.md](./product/PROJECT_ONBOARDING.md)
- [AGENT_HARNESS.md](./product/AGENT_HARNESS.md)

## Architecture

These documents describe the stable system shape and core mechanisms.

- [ARCHITECTURE.md](./architecture/ARCHITECTURE.md)
- [CONTEXT_LOADING.md](./architecture/CONTEXT_LOADING.md)
- [RECCLI_CLI_UI.md](./architecture/RECCLI_CLI_UI.md)

## Specs

These are the normative format and contract documents.

- [README.md](./specs/README.md)
- [DEVSESSION_FORMAT.md](./specs/DEVSESSION_FORMAT.md)
- [MESSAGE_RANGE_SPEC.md](./specs/MESSAGE_RANGE_SPEC.md)
- [UNIFIED_VECTOR_INDEX.md](./specs/UNIFIED_VECTOR_INDEX.md)
- [DEVPROJECT_FORMAT.md](./specs/DEVPROJECT_FORMAT.md) - optional project-outline design
- [SESSION_STORAGE.md](./specs/SESSION_STORAGE.md)

## Reference

These are operational or usage-oriented docs.

- [NATIVE_LLM_GUIDE.md](./reference/NATIVE_LLM_GUIDE.md)
- [SETTINGS_AND_AUTH.md](./reference/SETTINGS_AND_AUTH.md)
- [API_KEY_SECURITY.md](./reference/API_KEY_SECURITY.md)

## Implementation

These are subsystem deep dives, optimization notes, and supporting technical material.

### Retrieval

- [README.md](./implementation/retrieval/README.md)
- [STREAMING_HYBRID_RETRIEVAL.md](./implementation/retrieval/STREAMING_HYBRID_RETRIEVAL.md)

### Indexing

- [README.md](./implementation/indexing/README.md)

### Prompts

- [AI_PROMPTS.md](./implementation/prompts/AI_PROMPTS.md)

## Decisions

These are decision records and design-fix analyses.

- [RANGE_SEMANTICS_FIX.md](./decisions/RANGE_SEMANTICS_FIX.md)

## Progress

These documents are useful delivery records, but they are time-bound rather than canonical.

- [README.md](./progress/README.md)
- [SESSION_2025_11_07_SUMMARY.md](./progress/session-notes/SESSION_2025_11_07_SUMMARY.md)

## Archive

Historical ideation and legacy working notes live here.

- [MVP.md](./archive/product/MVP.md)
- [DESIGN_DECISIONS.md](./archive/decisions/DESIGN_DECISIONS.md)
- [PHASE_7_AUDIT.md](./archive/decisions/PHASE_7_AUDIT.md)
- [RecCli_intelligent_context_management_overview.md](./archive/ideation/RecCli_intelligent_context_management_overview.md)
- `docs/archive/progress/phases/` - archived phase implementation, testing, and walkthrough docs
- `docs/archive/progress/` - superseded completion summaries and execution snapshots
- `docs/archive/implementation/indexing/` - superseded optimization snapshots and retrospectives
- `docs/archive/implementation/retrieval/` - historical retrieval safeguards and design notes
