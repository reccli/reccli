# RecCli One-Pager

## What RecCli Is

RecCli is a temporal memory engine for coding agents.

It preserves full conversation history, compacts active context into a small working summary, and maintains a project-level outline that persists across sessions. The key difference is that these layers are linked: the compacted summary and project outline can point back to exact spans in the original conversation, so context can be reduced aggressively without becoming lossy.

In practical terms, RecCli is designed to give AI coding systems long-horizon continuity without forcing them to carry the entire conversation in the prompt window.

## The Problem

AI coding workflows break down over time.

As sessions get longer, most tools either:

- keep stuffing more raw history into context until performance degrades,
- compact history into an opaque summary that loses important details, or
- retrieve loosely related chunks from vector search without preserving the real decision trail.

This creates a recurring failure mode:

- the assistant forgets why a decision was made,
- prior work becomes harder to recover precisely,
- compaction is effectively irreversible,
- cross-session continuity becomes noisy or brittle.

Developers are left re-explaining work they already did.

## The RecCli Thesis

RecCli treats memory as a structured, time-linked system instead of a bag of retrieved text.

It uses three connected layers:

1. Project outline
   A compact cross-session view of the project: purpose, architecture, important decisions, open issues, current direction.
2. Compacted session summary
   A session-scale working memory layer containing decisions, code changes, problems solved, open issues, and next steps.
3. Full conversation
   The preserved source of truth, including the original message sequence and detailed implementation discussion.

The critical innovation is temporal linking between these layers.

Summary items are not dead text. They carry references into the original conversation, so an agent can move from:

- high-level project orientation,
- to session-level working memory,
- to exact source discussion,

without losing provenance or coherence.

## Why This Is Different

Most "memory" systems for AI coding are one of the following:

- long-context stuffing,
- lossy summarization,
- flat RAG over chunks,
- semantic search over notes.

RecCli is different because it combines:

- lossless preservation of the original conversation,
- compact active context for day-to-day use,
- exact drill-down from summary to source spans,
- temporal structure across sessions,
- project-level continuity above any single conversation.

That means RecCli is not just a search layer. It is a memory model.

## Core Product Promise

RecCli makes it possible for an AI coding system to operate with bounded active context and effectively unbounded historical recall.

The promise is not "infinite prompt length."

The promise is:

- keep the live context small,
- preserve the full history,
- recover the exact prior reasoning when needed,
- carry project continuity across many sessions.

## Who It Is For

RecCli is for developers and agent-driven coding workflows that need continuity across long or repeated sessions.

Primary users:

- developers using AI coding assistants daily,
- teams working across multi-day implementation threads,
- agents that need to resume work after compaction or interruption,
- products like OpenClaw that want a stronger memory layer than generic semantic recall.

## What RecCli Is Not

RecCli is not primarily:

- a chat UI,
- a general-purpose vector database,
- a note-taking app,
- a standalone product whose value depends on users adopting a brand new terminal workflow.

Those can exist around it, but they are not the moat.

The moat is the tri-layer temporal memory system.

## Strategic Positioning

The best near-term surface for RecCli is likely as an integration layer inside an existing agent environment such as OpenClaw.

That path is attractive because:

- distribution is easier than launching a new CLI from scratch,
- the memory model becomes the obvious differentiator,
- users can experience the benefit without changing their whole workflow,
- RecCli can remain the canonical engine behind the scenes.

In this model:

- OpenClaw provides the host UX, session surface, and plugin runtime,
- RecCli provides memory ingestion, compaction, retrieval, indexing, and temporal linking,
- `.devsession` and `.devproject` remain the source of truth.

## Why It Could Spread

RecCli has a chance to spread if it becomes known as the memory engine that preserves reasoning history instead of approximating it.

The most compelling story is simple:

"Most AI memory systems help agents remember topics. RecCli helps them recover exact prior reasoning."

That story is demoable, technically defensible, and useful to both end users and platform integrators.

## Current Product Surface

At the product-definition level, RecCli should currently be described as:

"A temporal memory engine for coding agents that links project outline, compacted session memory, and full conversation history into a recoverable system."

The exact primary interface is still open.

Possible surfaces include:

- OpenClaw plugin and context engine,
- standalone CLI and reference client,
- SDK or local service for agent platforms.

Those are packaging decisions. The product identity should remain stable across them.

## Near-Term Direction

The immediate goal is not to finalize every interface.

It is to prove the thesis in the strongest possible environment:

- integrate RecCli into OpenClaw as a memory or context engine,
- expose search, retrieval, compaction, and project-outline loading,
- demonstrate bounded active context with exact temporal recovery,
- keep the standalone RecCli CLI as the reference implementation and operator tool.

## One-Sentence Version

RecCli is a temporal memory engine for coding agents that preserves full reasoning history while keeping active context small through linked project, summary, and source layers.

## Short Pitch

RecCli gives coding agents long-horizon memory without lossy compaction. It stores the full conversation, maintains a compact working summary, tracks project-level continuity, and links each layer back to exact prior discussion so agents can recover the real reasoning when they need it.
