# RecCli Deep Dive

## The problem, in one observation

You've been coding with an AI assistant for six hours. Around hour two, you had a good discussion about the auth middleware, made a clean architectural decision, wrote the implementation. Now it's day three, and you're asking: "wait — did we settle on JWT rotation or refresh tokens?"

The assistant doesn't know. Because the assistant never really knew. The compaction summary said something like "discussed auth approach" and moved on.

AI coding assistants keep getting smarter on a per-session basis. But their memory across sessions — and within long sessions — is a mess. Most tools either stuff raw history into context until performance degrades, summarize into something opaque and irreversible, or retrieve loosely-related chunks from a vector store that lose the thread of what was actually decided.

RecCli is built on the conviction that this is a structural problem, not a context-window problem. You don't need a bigger prompt. You need a better memory model.

## The thesis: memory as a linked system

RecCli treats memory as three connected layers, each with a different temporal horizon.

**The full conversation.** Append-only. Every message gets a stable identifier. This is the source of truth; it's never discarded, never "compacted away." A span layer sits on top as a first-class semantic primitive — reasoning regions that can be referenced by ID across the whole system.

**The session summary.** Compacted working memory — usually 500 to 1,000 tokens. Five universal categories: decisions, code changes, problems solved, open issues, next steps. Every item carries a `message_range` pointing back to the exact slice of conversation that produced it.

**The project outline.** A cross-session view: features, file ownership boundaries, hub files, open proposals. Generated from a Tree-sitter plus LLM scan of the codebase on first init, then updated by proposals that sessions emit as evidence accumulates.

The layers are connected by two kinds of link. **Temporal links** between summary and conversation: a decision like "chose JWT rotation with 15-minute refresh window" carries a `message_range` pointing to the four messages where you actually argued it out. O(1) array slice — not a search, not a guess. **Semantic links** between projects and sessions: `feature_id` is the cross-layer identifier, so a session that touches `auth/middleware.py` automatically proposes updates to the feature that owns that file.

This is what makes the pitch "recovering exact prior reasoning" real rather than aspirational. You don't search for what the agent might have been thinking; you follow a pointer to the messages where it actually thought it.

## Why this is different from the usual answers

"Memory" for AI coding tools usually means one of four things:

- **Long-context stuffing** — keep adding raw history until the model's recall degrades under its own weight.
- **Lossy summarization** — one-shot compaction into a blob with no way back.
- **Flat RAG** — embed every chunk, retrieve by cosine similarity, pray the chunks have enough context to be useful out of order.
- **Semantic notes** — a search layer over Obsidian-style pages you have to maintain.

RecCli combines what each of those gets right and drops what they get wrong. Conversation is never lossy, because it's preserved in full. Active context is never bloated, because what's loaded is the compact summary plus a few recent messages. Recovery is exact, because every summary item carries a pointer to source. Cross-session continuity is real, because the project outline is a persistent identity layer, not an ad-hoc set of tags.

The moat is not any single one of these — it's the linking between them.

## How it runs

RecCli ships as an MCP server plus a set of Claude Code hooks. The hooks do the recording: `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop`, `SessionEnd`, and `PostCompact` write to a crash-safe write-ahead log and flush to a `.devsession` file at the end. The MCP server exposes structured operations on top of everything the hooks captured — for the agent to call, and occasionally for you to call directly.

Most of the time, you don't touch the tools. You name a project, Claude calls `load_project_context`, the feature map and the last session's resume-brief get injected into context, and you start working. When Claude needs to recall prior reasoning, it searches. When you wrap up, it saves. The plumbing is invisible.

But the plumbing is the product. Here's what's actually behind it.

## The tool surface

### Context and onboarding

**`load_project_context`** — The session opener. Loads the project feature map, the compact folder tree, the last session's summary, and a "Resume From" brief built from open issues and next steps — so the agent starts every conversation already oriented to where you left off.

**`project_init`** — The bootstrap. Scans the codebase with Tree-sitter, hands the file inventory and README to an LLM for feature clustering, and writes the `.devproject` file that becomes the stable cross-session identity of the project.

**`project_apply_clustering`** — The no-API-key fallback. When no Anthropic key is configured, `project_init` hands the scan plus a clustering prompt to Claude in-conversation; `project_apply_clustering` persists the result. Bootstrap works even without an LLM key in config.

### Search

**`search_history`** — The workhorse. Hybrid retrieval across every `.devsession` in the project: dense embeddings (OpenAI `text-embedding-3-small`, 1536D, memory-mapped from sidecar `.npy` files) fused with BM25 via reciprocal rank fusion, adaptively weighted when query terms are domain-specific, then boosted by recency (intent-aware decay — errors decay in hours, decisions in weeks), section locality, and kind (decisions and problems outrank notes).

**`search_by_file`** — Answers "what did we do to `webhook/route.ts`?" by scanning raw conversation content for the file path or basename. Index-free — works the instant a session is recorded.

**`search_by_time`** — Range queries: "what happened on March 29?" or "what did we work on last Tuesday?", with an optional text filter to narrow within the window.

**`expand_search_result`** — After any search, expands a `result_id` into its surrounding conversation with tri-layer traversal baked in. A summary-item hit follows `message_range` to the exact decision discussion and marks key-evidence messages. A span hit returns the span's bounded region. A message hit returns a symmetric context window. One tool, three modes of drill-down.

### Session management

**`save_session_notes`** — The close-out. Writes a structured summary with the five categories, then runs BM25 against the live WAL conversation to compute tight `message_range` clusters for each item — so the notes aren't just notes, they're drill-down handles into the full discussion that produced them.

**`summarize_previous_session`** — Retroactive summarization. If a session ended without a proper summary (crashed exit, the agent forgot to call `save_session_notes`), `load_project_context` surfaces an action-required block, and this tool writes the summary onto the existing `.devsession` without losing the original conversation.

**`list_sessions`** — The catalog view. Every recorded session with message count, summary status, overview snippet, sorted newest-first. Lets you see what's there before searching into it.

**`recover_file`** — Time-machine for files. Scans artifact sidecars from prior sessions for snapshots of a given path, reconstructing content from Claude Code's Edit tool records when full snapshots aren't stored. The file you lost on Tuesday is probably still recoverable from the session where you edited it.

### Autonomy

**`evaluate_continuation`** — The self-direction tool. Given the agent's current goal and its open items, filters the open items against the goal by keyword overlap and returns `continue` with the next actionable item, `wait` if nothing is goal-aligned, or `done` if nothing remains. Lets the agent work through a multi-step task without the user having to say "continue" after each step.

### Reasoning scaffolds

**`toggle_auto_reason`** — Regex-based intent detection at `UserPromptSubmit`. When the prompt looks like debugging (errors, failures, traceback language) or planning (architecture, trade-offs, "how should we..."), RecCli injects a diverge-converge-validate scaffold: consider 5-7 options, narrow to 1-2, validate before implementing.

**`toggle_mmc`** — The supercharged variant. Three parallel agents, each running the same reasoning scaffold through a different analytical lens — for debugging: recent changes, data flow, assumptions; for planning: simplicity, robustness, performance. The main agent then extracts consensus from their independent conclusions. Self-consistency sampling applied to coding.

**`toggle_session_signal`** — Asks the agent to emit a hidden `<!--session-signal: goal=... | resolved=... | open=...-->` tag on every response. The Stop hook strips the tag and persists the parsed signal. This is what powers the "Resume From" brief on the next session and what `evaluate_continuation` reads from.

**`toggle_expanded_search`** — Synonym query expansion: "auth middleware" also hits "authentication layer" and "login handler" via multiple BM25 variants. Dense search runs once, since embeddings already capture synonymy. BM25 is where the gain is.

### Diagnostics and settings

**`configure`** — View or change the four feature flags (`auto_reason`, `mmc`, `session_signal`, `expanded_search`), persisted to `~/.reccli/config.json`.

**`list_issues`** — The silent-failure surface. Anywhere a caught exception would otherwise disappear — bad WAL flush, failed embedding, index update that hit a lock — a record gets appended to an issue log, and this tool is how you see them.

## The payoff

The concrete test of a memory engine is simple: can you close your laptop on Tuesday, open it on Friday, and pick up the thread?

With RecCli, `load_project_context` on Friday injects the last session's overview, the decisions you made, the issues you left open, and the next steps you'd planned — as a resume-brief the agent can act on, not a summary blob. When Claude says "we decided to rotate JWTs every 15 minutes," and you ask "why?" — `expand_search_result` on that decision gets you back to the exact four messages where you weighed the trade-offs.

That's the promise. Not infinite prompt length. Not approximate recall. A memory model that preserves reasoning instead of summarizing it away.

## One-sentence version

RecCli is a temporal memory engine for coding agents that preserves full reasoning history while keeping active context small, through linked project, summary, and source layers where every compact recall points back to exact prior discussion.
