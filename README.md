# RecCli

RecCli is a temporal memory engine for coding agents.

Its core idea is a tri-layer memory system:

- project outline for cross-session context,
- compacted session summary for active working memory,
- full conversation history as the source of truth,

with temporal links between the layers so an agent can recover exact prior reasoning instead of relying on lossy compaction or flat retrieval.

## What Exists Today

The current repo contains a working implementation of the core memory stack:

- pure Python PTY terminal recording
- `.devsession` session storage
- conversation parsing and token counting
- summary generation and reference verification
- unified vector indexing and hybrid retrieval
- memory middleware and streaming retrieval
- preemptive compaction, checkpoints, and episodes
- a TypeScript + Ink terminal UI layered over the Python core

## Current Repo Status

This repository has evolved significantly and the packaging surface is still being normalized.

The canonical code now lives under [packages/reccli-core](/Users/will/coding-projects/RecCli/packages/reccli-core), and the documentation has been reorganized under [docs](/Users/will/coding-projects/RecCli/docs).

If you are evaluating the project, start with the docs rather than older install scripts or marketing pages.

## Quick Start

If your environment already has the needed Python dependencies installed, you can invoke the CLI directly:

```bash
PYTHONPATH=packages/reccli-core python3 -m reccli.cli --help
PYTHONPATH=packages/reccli-core python3 -m reccli.cli chat --help
```

The TypeScript terminal UI lives in `packages/reccli-core/ui`:

```bash
cd packages/reccli-core/ui
npm install
npm run build
```

Then launch chat through the Python entry point:

```bash
cd /path/to/RecCli
PYTHONPATH=packages/reccli-core python3 -m reccli.cli chat
```

## Repo Layout

```text
RecCli/
├── packages/
│   └── reccli-core/
│       ├── reccli/              # Python core
│       ├── tests/               # Python tests and benchmarks
│       └── ui/                  # TypeScript + Ink terminal UI
├── docs/                        # Architecture, specs, product, reference, history
├── apps/                        # Ancillary app surfaces
├── examples/
├── PROJECT_PLAN.md
└── MVP.md
```

## Documentation

Start here:

- [Docs Index](docs/README.md)
- [One-Pager](docs/product/RECCLI_ONE_PAGER.md)
- [Architecture](docs/architecture/ARCHITECTURE.md)
- [`.devsession` Format](docs/specs/DEVSESSION_FORMAT.md)
- [Unified Vector Index](docs/specs/UNIFIED_VECTOR_INDEX.md)
- [Project Plan](PROJECT_PLAN.md)

## Positioning

RecCli should be thought of as memory infrastructure, not primarily as a standalone chat UI.

The strongest product direction is likely:

- RecCli as the canonical memory engine
- `.devsession` and `.devproject` as the source-of-truth formats
- host integrations, such as an OpenClaw plugin/context engine, as the main distribution surface

## License

MIT. See [LICENSE](LICENSE).
