# reccli

reccli is a temporal memory engine for coding agents.

Its core idea is a tri-layer memory system:

- `.devproject` — project outline for cross-session context
- `.devsession` summary — compacted session working memory
- `.devsession` full conversation — source of truth

with temporal-semantic links between the layers so an agent can recover exact prior reasoning instead of relying on lossy compaction or flat retrieval.

## MCP Server

reccli runs as an MCP server, giving compatible coding agents persistent project memory.

### Codex / ChatGPT

```bash
git clone https://github.com/reccli/reccli.git
cd reccli
pip install -r requirements.txt
python3 -m reccli.runtime.cli setup --codex
```

This configures the RecCli MCP server in `~/.codex/config.toml` and installs Codex-visible startup instructions in `~/AGENTS.md` so new Codex sessions can ask which registered project to load.

### Claude Code

```bash
git clone https://github.com/reccli/reccli.git
cd reccli
pip install -r requirements.txt
python3 -m reccli.runtime.cli setup
```

Claude Code setup configures both the MCP server and lifecycle hooks for session start, prompt recording, tool recording, compaction, and session end.

**Tools exposed:**

| Tool | What it does |
|------|-------------|
| `load_project_context` | Load project features, folder tree, and last session summary at conversation start |
| `project_init` | Scan codebase with Tree-sitter + LLM to generate `.devproject` feature map |
| `search_history` | Hybrid search (dense + BM25 + RRF) across past `.devsession` files |
| `expand_search_result` | Drill into a search result to see full conversation context |
| `save_session_notes` | Persist decisions, problems solved, and next steps from current session |

## What it does

1. **First session**: `project_init` scans your codebase, clusters files into features, and creates a `.devproject` file
2. **Every session**: `load_project_context` loads the project map + folder tree + last session summary — the agent starts with full understanding
3. **During work**: `search_history` finds past decisions, problems, and code changes across sessions
4. **End of session**: `save_session_notes` persists what happened so the next session picks up where you left off

The result: session #10 on a project is dramatically better than session #1, because the agent accumulates structured memory instead of starting cold every time.

## Standalone CLI

reccli also works as a standalone CLI for direct session management:

```bash
PYTHONPATH=packages python3 -m reccli.runtime.cli --help
PYTHONPATH=packages python3 -m reccli.runtime.cli project init
PYTHONPATH=packages python3 -m reccli.runtime.cli project show
PYTHONPATH=packages python3 -m reccli.runtime.cli search "auth middleware decision"
```

## Repo layout

```
packages/reccli/
  session/          .devsession file format manager
  recording/        PTY terminal recording, WAL safety
  summarization/    LLM summarization, delta ops, compaction
  retrieval/        hybrid search, embeddings, memory middleware
  project/          .devproject manager, Tree-sitter init
  runtime/          CLI, LLM chat, config
  tests/            58 tests
  backend/          JSON-RPC bridge for TypeScript UI
  ui/               TypeScript + Ink terminal UI
  mcp_server.py     MCP server entry point
docs/
  specs/            .devsession and .devproject format specs
  architecture/     system architecture docs
```

## Format specs

- [`.devsession` format](docs/specs/DEVSESSION_FORMAT.md) — open session format (CC0 license)
- [`.devproject` format](docs/specs/DEVPROJECT_FORMAT.md) — project-level memory spec

## License

MIT. See [LICENSE](LICENSE).
