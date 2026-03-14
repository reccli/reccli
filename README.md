# RecCli

RecCli is a temporal memory engine for coding agents.

Its core idea is a tri-layer memory system:

- project outline for cross-session context,
- compacted session summary for active working memory,
- full conversation history as the source of truth,

with temporal links between the layers so an agent can recover exact prior reasoning instead of relying on lossy compaction or flat retrieval.

Today, the repo fully implements the session-summary and full-conversation layers. The project-outline layer exists today as an optional augmentation path: the middleware can read a `.devproject` file if one exists, but the main CLI does not require it and can operate entirely from `.devsession`.

## What Exists Today

The current repo contains a working implementation of the core memory stack:

- pure Python PTY terminal recording
- `.devsession` session storage
- conversation parsing and token counting
- summary generation and reference verification
- unified vector indexing and hybrid retrieval
- memory middleware and streaming retrieval
- preemptive compaction, checkpoints, and episodes
- a TypeScript + Ink terminal UI layered over the Python core through a packaged JSON-RPC backend

## Current Repo Status

This repository has evolved significantly, but the current docs and plan now treat the live codebase rather than the historical phase notes as the source of truth.

The canonical code now lives under [packages/reccli-core](/Users/will/coding-projects/RecCli/packages/reccli-core), and the documentation has been reorganized under [docs](/Users/will/coding-projects/RecCli/docs).

If you are evaluating the project, start with [PROJECT_PLAN.md](/Users/will/coding-projects/RecCli/PROJECT_PLAN.md) and the docs index rather than older install scripts or historical progress notes.

## Quick Start

If your environment already has the needed Python dependencies installed, you can invoke the CLI directly:

```bash
pip3 install -r requirements.txt
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
│       ├── backend/             # Python backend for the TypeScript UI bridge
│       ├── tests/               # Python tests and benchmarks
│       └── ui/                  # TypeScript + Ink terminal UI
├── docs/                        # Architecture, specs, product, reference, history
├── apps/                        # Ancillary app surfaces
├── examples/
└── PROJECT_PLAN.md
```

## Documentation

Start here:

- [Docs Index](docs/README.md)
- [One-Pager](docs/product/RECCLI_ONE_PAGER.md)
- [Architecture](docs/architecture/ARCHITECTURE.md)
- [`.devsession` Format](docs/specs/DEVSESSION_FORMAT.md)
- [Unified Vector Index](docs/specs/UNIFIED_VECTOR_INDEX.md)
- [Project Plan](PROJECT_PLAN.md)
- [Terminal UI Architecture](docs/architecture/RECCLI_CLI_UI.md)

## Positioning

RecCli should be thought of as memory infrastructure, not primarily as a standalone chat UI.

The strongest product direction is likely:

- RecCli as the canonical memory engine
- `.devsession` as the required source-of-truth format, with `.devproject` as an optional project-outline companion that can be generated later
- host integrations, such as an OpenClaw plugin/context engine, as the main distribution surface

## License

MIT. See [LICENSE](LICENSE).
