# RecCli Subsystem Split

RecCli's `.devproject` should treat the core product as several independently workable subsystems rather than one broad "core" feature.

Recommended split:

- `DevSession Runtime`
  Session creation, recording, lifecycle management, `.devsession` persistence.
- `DevProject Runtime`
  `.devproject` loading, codebase inventory, feature clustering, sync proposals, document linking.
- `Compaction And Temporal Linking`
  Rolling compaction, summary mutation, span management, temporal linkage back to source conversation.
- `Retrieval And Embeddings`
  Retrieval, BM25/vector search, embedding generation, index maintenance.
- `Terminal UI (Ink)`
  Interactive terminal client and packaged Ink UI.
- `Python Backend Server`
  Backend bridge for the packaged UI and external clients.
- `Marketing Website`
  Public website, landing pages, licensing and checkout flows.

Why this split:

- RecCli is architecture-heavy and agent-facing.
- Broad labels like `Core Memory Engine` hide the actual work zones.
- New sessions and agents need subsystem-level context, not one umbrella feature.

This is a RecCli-specific manual split, not a universal rule for all projects.
