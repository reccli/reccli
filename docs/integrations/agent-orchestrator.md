# Agent Orchestrator (External)

**Status:** Pointer to an external system that composes with RecCli over MCP.

The Agent Orchestrator is a separate package that turns a tracker (initially GitHub Issues) into an issue-driven, atomicity-bounded autonomous loop. It wraps RecCli's bounded audit/patch tools without redefining them. RecCli's native organization runner is a different concern: a user-started, mission-bounded multi-agent delivery run exposed directly through MCP.

**Repository:** `~/coding-projects/agent-orchestrator/`

## Why It Lives Outside RecCli

RecCli's product surface is memory traceability and hooks-based zero-friction capture. Issue polling, git worktree management, GitHub label state machines, and `gh pr create` plumbing are an orchestration concern that doesn't add to that moat — it dilutes the product story. Keeping the orchestrator as a separate package preserves clarity of scope on both sides.

## How It Composes With RecCli

The orchestrator calls into RecCli over MCP for:

- `load_project_context` — project memory and feature map for prompt assembly.
- `audit_feature` — feature-scoped read-only findings.
- `propose_patch` — diff generation (no auto-apply, hard-capped diff size).

It writes audit artifacts under the project's `devsession/agent-audits/` tree, reusing the harness's existing artifact contract. It adds a single new file per run, `orchestrator_run.json`, recording the atomicity probe schema, pre/post-implementation test results, diff line count, PR URL, and label transitions.

No additional tracker-specific MCP surface is required from RecCli. The external
orchestrator continues to own issue polling, labels, and PR lifecycle; the
RecCli-native `start_organization` / `organization_status` /
`cancel_organization` tools own only the bounded team execution lifecycle.

## Core Design Bet

The orchestrator's distinguishing feature is **executable atomicity**: every issue passes through a two-stage protocol where stage 1 is a structured schema (claim + test + decision) machine-validated by the orchestrator, and stage 2 is an implementation run guarded by pre-impl test failure, post-impl test pass, diff size cap, and clean-apply check. The contract is enforced by code that runs, not prompts that ask nicely.

This shape was informed by the session-signal critique noting that soft contracts drift; see the orchestrator's `docs/spec.md` for the full protocol.

## Read More

- [Orchestrator README](../../../agent-orchestrator/README.md) — product framing.
- [Orchestrator spec](../../../agent-orchestrator/docs/spec.md) — architecture, atomicity contract, tool surface, open questions.
- [`AGENT_HARNESS.md`](../product/AGENT_HARNESS.md) — RecCli's harness contract that the orchestrator composes with.
