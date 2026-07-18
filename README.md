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
| `start_organization` | Launch a durable Claude Code/Codex multi-agent delivery run on isolated Git worktrees, with optional immutable evidence snapshots |
| `organization_status` | Poll an organization run and inspect recent events/messages |
| `list_organizations` | List durable organization runs for a project |
| `steer_organization` | Queue a human message for an agent or role group at the next safe boundary |
| `pause_organization` / `resume_organization` | Stop or continue between synchronized rounds |
| `open_organization_console` | Launch the localhost Next.js viewer and steering console |
| `approve_organization` | Approve one exact staged decision packet; starts a fresh successor or applies a verified candidate locally without remote push |
| `cancel_organization` | Cancel the supervisor and its active native-agent subprocesses |

### Multi-agent organization runs

From a Claude Code or Codex session with the RecCli MCP server connected, ask
the agent to call `start_organization` with a project path and a concrete
mission. The call returns immediately with a `run_id`; use
`organization_status` to follow it and `cancel_organization` to stop it.
Runs default to eight synchronized work rounds. Each round may schedule several
agents in parallel, so status distinguishes the round countdown from cumulative
agent turns. A candidate already in flight at the cap may use up to four
review-only closeout boundaries; workers and new experiments cannot run there.

Event-driven organizations use a delegation barrier rather than unstructured
simultaneous starts. Round one belongs to the lead's macro reconnaissance;
round two belongs to managers refining that map. A worker's first turn requires
a specific primary-manager assignment with a named work item and risk. Every
manager must receive an explicit lead work item after round one, and every
worker must receive an explicit primary-manager work item after round two; the
run fails closed instead of entering unsynchronized execution when either
barrier is incomplete. Once assigned, workers execute concurrently. Managers consolidate routine
dependencies and progress upward, while the lead wakes on new macro information
instead of churning as another worker.

The default `google-rotating` structure has one mission lead, four engineering
managers, and four workers. Workers read the mission, code, tests, RecCli
project memory, and task-relevant repository documentation. Routine traffic
stays at the manager layer; immutable worker candidates receive rotating
alternate-manager review before the release manager can integrate them. A
fresh read-only agent independently verifies the exact final commit.

For evidence-heavy work, the single `scientific` topology gives workers broad
agency to choose and run reversible experiments in disposable branches. Pass
`evidence_paths` for a shared read-only hashed view, `protected_paths` for
tracked deny-write authority, and `max_experiments` for a hard generated-output
budget. A tracked `context_manifest` can route one shared documentation core
plus distinct worker lanes into hash-bound, read-only run context boxes.
Required `paths` are read before substantive work; larger `library_paths` are
boxed and verified but consulted only when relevant. Designated managers and
auditors can receive the union, while canonical files remain authoritative and
readable. A fully-sighted auditor can veto but cannot promote. Worker turns do
not consume experiment slots merely by running; the hard experiment budget is
charged only when RecCli seals an explicitly reported generated-output bundle.
Native agents edit and test while RecCli owns staging, commits, and reviewed
integration, avoiding provider-specific `.git` sandbox behavior. A
worktree-local `.venv/bin/python` bridge reuses the canonical environment with
candidate source first on `PYTHONPATH`. Generated outputs are sealed outside
Git and bound to exact candidates;
completion emits a human-authorized promotion request rather than changing the
caller's canonical branch or archive.

When human authority is the only remaining dependency, the release manager can
finish with `pending_human`. RecCli terminates the run as
`completed_pending_human` and stages a hash-bound approval packet instead of
burning rounds while waiting. The console shows the dossier, exact Git
identity, evidence, limits, and button effect on one page. Approval never wakes
the terminal supervisor: a checkpoint decision starts a fresh successor with
the signed decision in its immutable evidence, while an approved verified
promotion fast-forwards only the clean local branch. Neither action pushes a
remote.

Run-scoped reports and generated deliverables use a temporary tracked staging
prefix for immutable review, then RecCli exports the verified blobs to the
ignored run directory's `deliverables/` folder with a hash manifest. Completed
runs expose a clean `promotion_branch` that omits the temporary staging tree, so
it can be merged into either a local-only or remote-backed repository without
polluting the product's permanent `docs/` tree. Organization agents can inspect
local Git, while the trusted RecCli supervisor owns Git mutation; remote pushes
and hosting credentials are not part of the harness.

Run `reccli organization console --project-root /path/to/project` for the local
two-pane console: select a team member in the top rail, steer them from the left
operator chat, and watch all eight team work streams on the right. See
[`docs/integrations/organization-console.md`](docs/integrations/organization-console.md).
The approval staging area can enable browser notifications so a completed run
can call attention to a waiting decision while the console is open.

The runner calls the installed `claude` or `codex` executable and reuses that
CLI's subscription authentication. It does not require Anthropic/OpenAI API
keys. With `provider="auto"`, RecCli checks both native CLI logins and mixes
providers when both are usable: alternating worker/manager lanes use Claude and
Codex, reviews prefer the other provider, the release manager stays on the host
provider, and the fresh verifier uses the opposite provider. If only one CLI is
usable, auto falls back to that provider. Pass `claude` or `codex` explicitly
for a homogeneous team, or `mixed` to require both. Organization runs can
consume substantial subscription quota; start them for explicit missions
rather than as an always-on issue poller.

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
