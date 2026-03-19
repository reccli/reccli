# `.devproject` File

**Status:** Canonical design spec for the project-layer context engine above all .devsession files, linked to them via temporal semantic identifiers is nearly fully implemented, testing 

The current RecCli codebase implements `.devproject` partially in the main CLI path. 

## Overview

`.devproject` is the project-level memory and control document for a repository. The key differentiator is temporal semantic linking between the layers (primarily temporal linking between summary and full conversation layer (both .devsession)and primarily semantic between .devproject file and summary layer of .devsession )so that summaries can recover exact prior reasoning from the original conversation as well as `.devproject` as the primary context layer that's inserted on every new "dev session" so the LLM agent has a path for understanding and navigation for any aspect the user then works on'.

It sits above individual `.devsession` files and answers:

- what this project is
- which features define its scope
- which sessions contributed to which features
- which files or file boundaries belong to which features
- which documents describe which features or the project as a whole
- what progress state each feature is in

It is both:

- **bottom-up generated** from `.devsession` summaries, code-change evidence, or codebase scan
- **top-down authoritative** once the user accepts or edits the project document

That means:

- for new projects, work emerges first in full conversation and session summaries
- for existing projects, `reccli project init` can scan the codebase to bootstrap the feature map
- RecCli proposes `.devproject` updates from that evidence
- the user accepts, edits, or rejects those structural changes
- the accepted `.devproject` becomes the canonical project feature map

`.devproject` is not a freeform summary blob. It is a project data document that should remain inspectable, editable, diffable, and stable over time.

## Location

```text
<project-root>/
  .devproject
  .git/
  ...
```

**Path:** `<project-root>/.devproject`

**Tracking:** user choice. It may be gitignored for privacy or committed as a project control document.

## Purpose

`.devproject` is the primary context layer inserted at the start of every new dev session. It provides the LLM agent with a structured project map so it can understand scope, navigate features, and locate relevant session history for any aspect of the work.

Its main jobs are:

- serve as the first document loaded into every new session's context
- provide a low-token project overview that orients the LLM immediately
- define project scope in terms of features and goals
- link features to session files chronologically
- link features to concrete files or file boundaries
- link existing project documents to the features or project-level concerns they describe
- act as the canonical place to confirm or correct feature attribution
- make project state understandable without reading every `.devsession`
- support future agent dispatch and conflict prevention through feature-to-file ownership

## Organizing Principles

The three memory layers use different primary organizing principles:

| Layer | Primary organizing principle | Secondary principle |
| --- | --- | --- |
| `.devproject` | `feature_id` | status / project scope |
| `.devsession` summary | `feature_id` | chronology inside a session |
| `.devsession` full conversation | chronology | diluted feature linkage |

So:

- chronology dominates inside full conversation
- feature identity dominates across sessions and at project level
- `.devproject` is the canonical cross-session feature map

This is why multiple `.devsession` files per project are expected:

- full conversations are chronological and can grow beyond practical active context
- session summaries stay smaller but still accumulate over time
- `.devproject` is the stable project dashboard above those many sessions

## Core Design Rules

### 1. `.devproject` is the primary context layer

`.devproject` is the first document loaded at the start of every new dev session. It provides the LLM agent with a structured understanding of the project so it can navigate any aspect the user works on.

For new projects, `.devproject` is auto-generated on the first `project init` or first compaction. For existing projects, it is bootstrapped from a codebase scan. RecCli can function from `.devsession` alone in degraded mode, but `.devproject` is the intended and recommended operating state.

### 2. `.devproject` is canonical after acceptance

The system may propose feature mappings from session evidence, but once the user accepts or edits `.devproject`, it becomes the authoritative project feature map.

### 3. Updates flow both ways

Bottom-up:

- codebase scan (for existing projects without prior sessions)
- full conversation
- span and summary extraction
- proposed feature / session / file updates

Top-down:

- accepted feature IDs
- canonical file boundaries
- canonical status / naming / scope

`.devsession` proposes. `.devproject` confirms.

### 4. Proposed updates must be structural diffs

The system should not say "I updated the project overview" in prose.

It should show a concrete proposed edit to `.devproject`, for example:

```text
.devproject update proposed

+ feature: stripe-webhooks
+   status: in-progress
+   files_touched: [api/webhooks.js, api/stripe.js]
+   session_ids: [session-005]

~ feature: stripe-connect
~   files_touched: + api/webhooks.js
~   session_ids: + session-005
```

The user should be able to:

- accept
- reject
- edit before accepting

### 5. Human edits are first-class

`.devproject` is user-editable.

It must not drift into an opaque machine-owned blob. User-written naming, scope, status, and descriptions should be preserved unless the user explicitly accepts a conflicting proposed change.

### 6. `.devproject` should stay focused

`.devproject` should contain what the LLM needs to orient itself at session start:

- feature identity and descriptions
- progress status
- file boundaries and evidence
- linked session files
- linked documents

It should not contain project-level decision graphs, heavy orchestration metadata, or implementation details that belong in `.devsession` summaries and full conversation.

## File Format

`.devproject` is a JSON document.

```json
{
  "format": "devproject",
  "version": "2.1.0",
  "project_root": "/Users/will/coding-projects/RecCli",
  "updated_at": "2026-03-14T12:00:00Z",
  "last_updated_session": "session-012",
  "project": {
    "name": "RecCli",
    "description": "Temporal memory engine for coding agents",
    "status": "active",
    "source": "manual"
  },
  "features": [
    {
      "feature_id": "feat_temporal_memory",
      "feature_version": 3,
      "title": "Temporal memory engine",
      "description": "Linked session memory with exact drill-down from summaries into full conversation.",
      "status": "in-progress",
      "source": "manual",
      "files_touched": [
        "packages/reccli-core/reccli/devsession.py",
        "packages/reccli-core/reccli/summarizer.py",
        "packages/reccli-core/reccli/retrieval.py"
      ],
      "file_boundaries": [
        "packages/reccli-core/reccli/**"
      ],
      "docs": [
        {
          "path": "docs/specs/DEVSESSION_FORMAT.md",
          "title": "`.devsession` Format",
          "relevance": "primary",
          "score": 3.7,
          "signals": [
            "bm25"
          ]
        }
      ],
      "session_ids": [
        "session-010",
        "session-011",
        "session-012"
      ],
      "last_updated_session": "session-012",
      "updated_at": "2026-03-14T12:00:00Z",
      "staleness": {
        "status": "current",
        "checked_at": "2026-03-14T12:00:00Z",
        "signals": []
      }
    }
  ],
  "project_docs": [
    {
      "path": "README.md",
      "title": "RecCli",
      "scope": "project",
      "signals": [
        "unresolved_doc"
      ]
    }
  ],
  "session_index": [
    {
      "session_id": "session-010",
      "path": ".devsessions/session-010.devsession",
      "started_at": "2026-03-11T09:00:00Z",
      "ended_at": "2026-03-11T11:00:00Z",
      "feature_ids": [
        "feat_temporal_memory"
      ]
    },
    {
      "session_id": "session-012",
      "path": ".devsessions/session-012.devsession",
      "started_at": "2026-03-14T09:00:00Z",
      "ended_at": "2026-03-14T12:00:00Z",
      "feature_ids": [
        "feat_temporal_memory"
      ]
    }
  ],
  "proposals": [
    {
      "proposal_id": "projupd_001",
      "status": "pending",
      "created_at": "2026-03-14T12:00:00Z",
      "source_session_id": "session-012",
      "diff": [
        {
          "op": "update_feature",
          "feature_id": "feat_temporal_memory",
          "changes": {
            "files_touched_add": [
              "packages/reccli-core/reccli/summary_verification.py"
            ],
            "session_ids_add": [
              "session-012"
            ]
          }
        }
      ]
    }
  ],
  "update_history": [
    {
      "proposal_id": "projupd_000",
      "status": "accepted",
      "created_at": "2026-03-11T11:00:00Z",
      "source_session_id": "session-010"
    }
  ]
}
```

## Required Top-Level Fields

- `format`
- `version`
- `project_root`
- `updated_at`
- `last_updated_session`
- `project`
- `features`
- `project_docs`
- `session_index`
- `proposals`

Recommended top-level fields (included when available):

- `update_history`

## `project` Object

The `project` object should stay intentionally small.

Required fields:

- `name`
- `description`
- `status`
- `source`

`source` is one of:

- `manual`
- `auto`

## `features`

Each feature is the core unit of project scope.

Required fields:

- `feature_id`
- `feature_version`
- `title`
- `description`
- `status`
- `source`
- `files_touched`
- `session_ids`
- `last_updated_session`
- `updated_at`

Recommended fields (included when available):

- `file_boundaries`
- `docs`
- `staleness`
- `notes`

### Feature status

Recommended statuses:

- `planned`
- `in-progress`
- `complete`
- `blocked`
- `reverted`
- `archived`

### Feature identity

`feature_id` is the canonical cross-layer semantic link.

It should be stable once created.

`feature_version` is a monotonic counter that increments whenever the canonical feature definition changes materially, for example:

- user edits title or description
- user changes scope or ownership boundaries
- accepted proposal changes feature status or grouping

This makes it possible to detect when later sessions were linked against a newer feature definition than earlier sessions.

Session summaries may carry `feature_ids` as provisional upward links, but `.devproject` is the canonical source of final feature attribution.

### Feature scope and files

`files_touched` is the concrete evidence list populated from linked sessions.

`file_boundaries` is the user-declared ownership-boundary list intended for future orchestration and conflict detection.

Examples:

- exact files
- directories
- globs

Design rules:

- file ownership should be as non-overlapping as practical
- if multiple features claim the same file boundary, the system should warn before agent dispatch or project update acceptance
- file overlap should be the primary signal when matching session evidence to an existing feature

Expected relationship:

- `files_touched` answers: what files did linked sessions actually modify?
- `file_boundaries` answers: what files or directories should this feature own?

If `files_touched` grows outside `file_boundaries`, one of these is true:

- the feature boundary needs to be updated
- the session evidence was misattributed
- the project has drifted from the declared feature scope

### Feature documents

`docs` is the list of repository documents linked to a feature.

Recommended fields for each document link:

- `path`
- `title`
- `relevance`
- `score`
- `signals`

Recommended relevance values:

- `primary`
- `reference`

Meaning:

- `primary`: the document mainly defines, specifies, or explains the feature
- `reference`: the document mentions or supports the feature but is not primarily about it

The score does not need to be user-facing; it exists mainly for ranking and debugging the automatic linker.

## `session_index`

`session_index` is the chronological ledger of `.devsession` files linked into the project.

Each entry should include:

- `session_id`
- `path`
- `started_at`
- `ended_at`
- `feature_ids`

This is how feature-level retrieval spans many session files without scanning all summaries blindly.

## `project_docs`

`project_docs` stores documents that are relevant to the repository as a whole rather than to a single feature.

Examples:

- README files
- architecture overviews
- onboarding guides
- product one-pagers
- project plans that span multiple features

Recommended fields:

- `path`
- `title`
- `scope`
- `signals`

Recommended `scope` value for now:

- `project`

## `proposals`

`proposals` stores pending or historical structural updates suggested from session evidence.

Each proposal should include:

- `proposal_id`
- `status`
- `created_at`
- `source_session_id`
- `diff`

Proposal status:

- `pending`
- `accepted`
- `rejected`
- `edited`

The important rule is:

- proposals are not canonical
- accepted `.devproject` state is canonical

### Proposal retention

`proposals` is for pending review, not permanent history.

Recommended retention rules:

- keep only pending proposals in `proposals`
- move accepted, rejected, or edited proposals into `update_history`
- truncate `update_history` aggressively, for example keeping only the last `20-50` records or the last `30-90` days

The goal is to keep `.devproject` compact. Long-term audit history belongs elsewhere if needed.

## Proposal Diff Format

Proposal diffs should be structural and machine-checkable.

Recommended operations:

- `add_feature`
- `update_feature`
- `link_session`
- `unlink_session`
- `mark_status`
- `archive_feature`

The diff should be concrete enough that the user can review the exact project-state change without having to interpret prose.

## Codebase Sync (Project Initialization)

`.devproject` can be generated from session evidence over time, but it can also be initialized from an existing codebase that has no prior `.devsession` history.

### The problem

When adopting `.devproject` on an existing project, there may be no `.devsession` files yet. Without codebase sync, `.devproject` would start empty and only learn about features as new conversations happen. That means the project dashboard would be incomplete for potentially weeks or months, missing features that already exist in code.

### The solution

`reccli project init` scans the existing codebase and generates an initial `.devproject` by discovering features from the code itself.

### How it works

1. Walk the codebase structure (directories, modules, entry points, route definitions, package boundaries)
2. Read key structural files (README, package.json, pyproject.toml, main entry points, configuration files)
3. Use the LLM to cluster files into logical features with titles and short descriptions
4. Scan repository documents from the same inventory walk
5. Link documents to features using deterministic matches first, then lexical or embedding relevance
6. Generate a `.devproject` proposal with discovered features and document links
7. Show the result as a structural diff for user review
8. User accepts, edits, or rejects — same flow as any other `.devproject` proposal

### Features created by codebase sync

Features discovered from the codebase use the same schema as any other feature:

- `source`: `"auto"`
- `files_touched`: populated from the scan (the actual files that belong to the feature)
- `file_boundaries`: inferred from directory/module structure
- `session_ids`: empty (no sessions have contributed yet)
- `last_updated_session`: null or omitted
- `status`: inferred where possible (e.g., `"complete"` for clearly functional code, `"in-progress"` if the code appears partial), defaulting to `"in-progress"`

These features are distinguishable from session-derived features because they have `files_touched` populated but no `session_ids`. Once a session touches files belonging to a codebase-scanned feature, normal session linking takes over.

### Planned features vs. existing features

Features discovered from the codebase should have `files_touched` populated because they already have code. Features that are `planned` and have no code yet should have `files_touched` empty. This is a natural distinction: if the code exists, the files are evidence; if the code doesn't exist, there's nothing to reference.

### Re-sync

`reccli project init` should be safe to run on an existing `.devproject`. In that case it:

- compares discovered features against existing features by file overlap
- proposes new features for code clusters that don't match any existing feature
- flags potential staleness where existing features reference files that have changed significantly
- does not overwrite or remove existing features — only proposes additions or updates

This makes re-sync a staleness check as well as a discovery tool.

## Document Linking

`.devproject` should not ignore existing repository documents. They are evidence of intent, architecture, and feature scope even when RecCli aims to reduce reliance on manually maintained plans over time.

### Inventory

Document discovery should reuse the same repository walk used for code discovery.

Recommended document extensions:

- `.md`
- `.txt`
- `.rst`
- `.adoc`

Inventory items should carry `kind: "doc"` rather than introducing a separate file-walking implementation.

### Linking order

Recommended linking cascade:

1. Deterministic matches
2. Lexical relevance (BM25 / TF-IDF style)
3. Embedding similarity
4. LLM fallback only for ambiguous leftovers

### Deterministic signals

Prefer deterministic signals first:

- explicit code-path references in the document
- document path inside a feature boundary
- feature-title or slug matches in document title / path
- obvious symbol-name matches where available

### Lexical relevance

For unresolved docs, compare document text against feature text profiles built from:

- feature title
- feature description
- file names
- file boundaries
- optionally comments/docstrings from key files

BM25-style ranking is preferred before embeddings because it is local, cheap, and often accurate for technical repositories.

### Embedding similarity

For documents still unresolved after deterministic and lexical passes, compare document embeddings against feature-profile embeddings using the same embedding provider stack already used elsewhere in RecCli.

Recommended behavior:

- high-confidence match -> attach to feature `docs`
- low-confidence across all features -> attach to `project_docs`

### Quality policy

The linker should optimize for stable, reviewable defaults rather than perfect classification. Over-linking can be corrected during review; under-linking can be recovered by later sync passes.

## Lifecycle

### Session work

During a session:

- work happens in full conversation
- spans, summary items, and code changes are extracted
- summary items may carry `feature_ids` as provisional upward links

### Compaction or manual project update

At compaction time or via explicit user command:

- RecCli examines session summary items and file evidence
- matches them against existing features by canonical file overlap first
- if no good match exists, proposes a new feature
- generates a `.devproject` structural diff

### User review

The user reviews the proposed diff in the conversation.

They may:

- accept it
- reject it
- edit it

Only the accepted result becomes canonical `.devproject` state.

## Upward Linking From `.devsession`

The intended upward flow is:

1. full conversation produces spans, summary items, and file evidence
2. summary items may carry `feature_ids`
3. project update logic groups those items by existing feature overlap
4. `.devproject` receives a proposed feature / session / file update

Important:

- the system should prefer file overlap over name similarity when matching to an existing feature
- semantic title matching is allowed as a secondary heuristic, not the primary one
- multiple `.devsession` files per project are normal and expected

## Downward Linking From `.devproject`

Once `.devproject` is accepted, it is loaded as the primary context at the start of every new session. This gives the LLM agent immediate access to:

- known feature IDs and their current status
- known file boundaries for each feature
- known feature names and descriptions
- canonical session-to-feature attribution
- linked documents and session history paths

This lets the agent orient itself to any part of the project without the user re-explaining context, and lets future `.devsession` summaries attach to an existing feature ID rather than constantly inventing new feature names.

## Bidirectional Authority Model

The authority model is:

- `.devsession` is authoritative about what happened in a session
- `.devproject` is authoritative about how session work maps into project scope

That means:

- `.devsession` proposes
- `.devproject` confirms

This is bottom-up generation with top-down authority.

## Staleness Model

Staleness should stay simple in v1.

The main question is:

- does this feature description still match the codebase and recent session history?

Suggested feature-level staleness fields:

- `status`: `current` | `stale` | `unknown`
- `checked_at`
- `signals`

Signals may include:

- files in `files_touched` deleted or moved
- feature boundary files changed in sessions not linked to that feature
- feature marked `complete` but still receiving active session updates
- feature marked `in-progress` but reverted by later session evidence

This is enough for useful drift detection without building a full causal graph.

## Human Edit Policy

`.devproject` must remain human-editable.

Rules:

- user-written feature descriptions should not be silently overwritten
- accepted manual changes become canonical immediately
- auto-generated updates should appear as proposals, not silent rewrites
- features should carry `source` so the system can distinguish `manual` from `auto`

## Relationship To Agent Dispatch

`.devproject` is the primary context layer and project dashboard. It is also shaped so that agent orchestration can use it directly.

That means:

- `feature_id` should be stable
- `file_boundaries` should be machine-checkable
- conflicting file ownership should be detectable
- `status` should make planned vs active work visible

Future agent dispatch can then use:

- selected feature
- linked session history
- canonical file boundaries

without redesigning the file format.

## Non-Goals

`.devproject` should not include:

- full project decision graphs (that detail belongs in `.devsession`)
- automatic project-level prioritization
- nested feature hierarchies
- cross-machine orchestration metadata
- perfect autonomous feature identity matching

Those can be layered later if needed.

## Summary

`.devproject` is:

- the primary context layer loaded at the start of every new session
- the project's canonical feature map and navigation structure
- feature-oriented with semantic links down to `.devsession` summaries
- user-editable and auto-proposed from `.devsession` evidence and codebase scans
- canonical after user acceptance
- chronologically linked to many `.devsession` files
- structurally diffable

Together with `.devsession`, it forms a tri-layer memory architecture where temporal linking connects the conversation and summary layers, and semantic linking connects the summary layer to `.devproject`. This means summaries can recover exact prior reasoning from the original conversation, and `.devproject` provides the LLM agent with a structured path for understanding and navigating any aspect of the project the user works on.
