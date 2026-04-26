# Agent Harness

**Status:** Product/design document with an initial MCP implementation.

This document describes a RecCli-native harness for launching scoped coding agents against project memory. The initial executable slice is the `audit_feature` MCP tool, which creates a read-only feature-scoped run package under `devsession/agent-audits/` and dispatches through subscription-auth CLI adapters for Claude or Codex.

## Overview

The agent harness uses RecCli's existing project memory to run focused, parallel agent work. Each v1 agent receives a bounded work package for one feature or risk area, performs a read-only audit, and returns structured findings for a human to review.

The key product bet is that agent quality improves more from context quality than raw concurrency. A small number of agents with feature-scoped context packs should produce better findings than a large fleet pointed at a repository with generic instructions.

## Why This Belongs In RecCli

The harness is a natural extension of RecCli's memory model:

- `.devproject` provides feature IDs, descriptions, file boundaries, and project-level intent.
- `.devsession` provides prior decisions, solved problems, failed approaches, and open issues.
- Retrieval can hydrate each agent with adjacent context without dumping the whole repo.
- Summarization and span references can support deduplication, provenance, and follow-up work.

Project-specific repositories should define what to audit. RecCli should own how context packs are built, how agents are dispatched, how findings are structured, and how results are persisted.

## First Workflow: Feature Audit

Audit is the first useful workflow because it is low-risk and read-only. The broader harness should not assume all agents are auditors.

Possible future modes after the read-only audit workflow proves useful:

- `audit`: inspect feature-scoped code and return findings.
- `verify`: run checks or reproduce a finding.
- `research`: trace prior decisions, docs, and sessions for a feature.
- `triage`: classify issues or pull requests against project memory.

Diff proposal lives as a separate tool, not a mode of `audit_feature`. See "Patch Proposal" below.

## Non-Goals

- Do not start with around-the-clock autonomous maintenance.
- Do not auto-close issues or pull requests in the first version.
- Do not auto-comment on GitHub until finding quality is proven.
- Do not let agents patch broad areas of the codebase in parallel.
- Do not replace human review for security, billing, auth, or data-loss-sensitive changes.

## Core Workflow

1. Select a feature or audit target from `.devproject`.
2. Build a feature-scoped context pack and per-agent instruction files.
3. Write the harness run package under `devsession/agent-audits/`.
4. Collect one JSON file and one Markdown report per agent.
5. Review the reports manually.
6. Add deduplication or promotion to issues only after repeated runs show the need.

## Current MCP Surface

The first MCP tool is:

```python
audit_feature(
    working_directory="/path/to/project",
    feature_id="feat_checkout",
    agents=6,
    provider="auto",
    mode="report",
    focus="optional narrower instruction",
    max_files=8,
    max_file_chars=12000,
    max_concurrency=1,
    files=None,
    globs=None,
)
```

`files` and `globs` are scope overrides. The feature is always resolved from `.devproject` for description, docs, and session linkage, but audit *scope* defaults to `feature.files_touched`. When a feature map is stale or under-clustered (one common failure mode: a "feature" that maps to a single script while the real product capability touches onboarding, APIs, helpers, and tests), pass explicit scope:

```python
audit_feature(
    feature_id="Email Digest & Weekly Rollup",
    focus="onboarding -> preferences -> delivery -> replay -> unsubscribe",
    globs=[
        "src/app/onboarding/**",
        "src/app/api/user/**",
        "src/app/api/unsubscribe/**",
        "src/app/api/digest/**",
        "src/lib/**/*unsubscribe*",
        "scripts/regwatch-*digest*.ts",
        "tests/**/*digest*",
    ],
    max_files=20,
)
```

`files` are taken as-is (relative to `project_root`); `globs` are expanded against `project_root` with native `**` recursion. Results are deduped (files first, then glob-matched files in result order), filtered to existing files inside the project, and capped at `max_files`. The bundle reports `scope.source` as `"feature"` (default) or `"override"` (when files/globs produced any matches). The original `feature.files_touched` is also reported for comparison.

Override scope is the safety net for stale feature maps. The primary fix for chronically misclustered features is updating `.devproject`, since every other tool that consumes the map (context loading, cross-feature search) keeps producing degraded output otherwise.

`provider` defaults to `"auto"`: the harness inspects the host CLI environment (Claude Code → `claude`, Codex CLI → `codex`) and dispatches the audit child on the same auth/quota surface the caller is already using. The bundle reports both `provider` (resolved) and `provider_requested` (raw input). Pass an explicit `"codex"`, `"claude"`, or `"none"` to override.

Auto-detection order:

1. `RECCLI_HOST` env var (`"claude"` or `"codex"`) — explicit override.
2. `CLAUDECODE` / `CLAUDE_CODE_SESSION_ID` env vars set by Claude Code → `"claude"`.
3. `CODEX_SESSION_ID` / `CODEX_HOME` env vars → `"codex"`.
4. Parent-process inspection (`ps`) for `codex` or `claude` in the caller's process chain.
5. Fallback: `"claude"`.

**Codex MCP setup note.** Codex CLI does not reliably pass its session env vars through to MCP subprocesses, so step 3 alone is unreliable. For deterministic detection from a Codex host, declare `RECCLI_HOST` in the MCP server's env block in `~/.codex/config.toml`:

```toml
[mcp_servers.reccli]
command = "/path/to/reccli/venv/bin/python3"
args = ["-m", "reccli.mcp_server"]
env = { PYTHONPATH = "/path/to/reccli/packages", RECCLI_HOST = "codex" }
```

Claude Code sets `CLAUDECODE=1` automatically and does not need this override.

`model` defaults to `"auto"`: the harness tries to match the model the host CLI is configured to use, so the audit child runs on the same model class the caller is paying for. Detection is asymmetric:

- **Codex** stores `model = "..."` in `~/.codex/config.toml`; the harness parses this top-level key.
- **Claude Code** sets the active model per session via `/model` and does not persist it to a settings file or env var. There is no reliable env-based detection — `model="auto"` falls through to the claude CLI's compiled default. Pass an explicit alias (`"opus"`, `"sonnet"`) or full ID (`"claude-opus-4-7"`) to force a specific model.

The bundle reports both `model` (resolved, may be `null` when not detected) and `model_requested` (raw input). Pass `model="none"` (or `""` / `"default"`) to skip detection and use the CLI default.

`max_concurrency` defaults to `1` (sequential dispatch). On the first quota error from a provider, the remaining agents in the batch are marked `status="skipped"` with a quota `skip_reason` so the rest of the quota is preserved. Pass `max_concurrency > 1` to opt into parallel dispatch.

Supported v1 mode:

- `report`

Possible future modes are documented above, but the executable MCP tool should reject them until their contracts are intentionally designed.

Supported v1 providers:

- `auto`: default. Detects the host CLI from environment (`CLAUDECODE` / `CLAUDE_CODE_SESSION_ID` → `claude`; `CODEX_HOME` / `CODEX_SESSION_ID` → `codex`) and dispatches on the same auth/quota surface. Falls back to `claude` when no host fingerprint is present.
- `codex`: runs `codex exec --sandbox read-only` through the local Codex CLI. Read-only is enforced by the Codex sandbox outside the model process.
- `claude`: runs `claude -p --tools ""` through the local Claude Code CLI. Read-only is enforced by stripping all tools from the spawned subagent.
- `none`: prepares all artifacts without dispatching agents.

Bundle statuses:

- `prepared`: artifact preparation completed with `provider="none"`; no audit agent ran.
- `completed`: all real provider agents completed and returned parseable JSON.
- `partial`: one or more real provider agents failed, timed out, returned empty output, returned unparseable output, or were skipped after a quota abort.

The returned bundle also includes `status_reason` (e.g. "Dry run; no provider dispatched", "Provider quota hit. 1 of 6 agents completed; 5 skipped to preserve quota", or "2 of 6 agents failed, timed out, or returned empty/unparseable output") and `quota_hit: bool` for fast detection of provider exhaustion.

The tool resolves the feature from `.devproject`, reads selected feature files and linked docs, scans for basic risk signals, collects lightweight related session summaries (PII-redacted at the assembly seam — emails, API keys, JWTs, db-creds-in-URLs are stripped before they enter the context pack), ensures `devsession/agent-audits/` is in the project's `.gitignore`, dispatches independent read-only audit agents, and writes:

```text
devsession/agent-audits/<date>/<feature>/<run-id>/
  context_pack.json
  instructions.md
  report.md
  agent_01_instructions.md
  agent_01_findings.json
  agent_01_report.md
  agent_02_instructions.md
  agent_02_findings.json
  agent_02_report.md
```

This makes the harness executable from the MCP side without requiring the legacy RecCli CLI.

For inspection or dry-run packaging without dispatch, call `audit_feature` with `provider="none"`: it builds the same context pack and per-agent artifacts and returns the JSON bundle without firing a subprocess.

## Provider Adapters

Provider adapters keep subscription-auth execution separate from the task contract. The harness passes each adapter the same inputs:

- Context pack path.
- Agent instruction file.
- Project root.
- Output schema expectation.
- Optional model override.

The adapter owns the provider-specific invocation details:

- Claude adapter: invoke `claude -p --tools ""` (with `--model <model>` when set) from the project root and capture stdout/stderr.
- Codex adapter: invoke `codex exec --cd <project> --sandbox read-only [--model <model>] --output-last-message <file> -` and capture stdout/stderr.

Adapters write raw output plus parsed findings back into the agent's JSON and Markdown report files. This keeps prompt and schema design provider-neutral while containing CLI flag churn in one module.

`claude -p --tools ""` was verified locally with a `/tmp` write probe: Claude reported no write tool and did not create the requested file. If Claude CLI semantics change, replace this with an explicit read-only allowlist such as `--allowedTools Read,Grep,Glob`.

`codex exec --sandbox read-only` was verified locally with a `/tmp` write probe from a trusted repository: Codex returned `READ_ONLY_BLOCKED` and did not create the requested file.

Future hardening: cache provider probe results by CLI version in `~/.reccli/provider-probes.json`, and re-run probes only when the installed provider version changes.

V1 inlines the generated context pack into each provider prompt. That keeps execution predictable and avoids depending on provider-specific read tools, but it duplicates context across agents. If real audits are slow or quota-heavy, the first optimization should be a reference-mode prompt that lets read-only agents load files from the context pack paths.

## Replay

If one agent fails or returns unparseable output, rerun just that agent:

```python
replay_audit_agent(
    working_directory="/path/to/project",
    run_id="20260425T230000Z_audit_feat_checkout",
    agent_id="agent_03",
    provider="claude",
)
```

`run_id` may be either the audit run ID or the explicit `run_dir` path from the `audit_feature` bundle.

Replay uses the existing `context_pack.json` and `agent_XX_instructions.md` from the run directory, then overwrites that agent's findings and report files.

## Patch Proposal

Audit findings are prose. Turning a finding into a code change is a separate tool with a different shape: one agent, one finding, generous per-file budget, fresh file reads.

```python
propose_patch(
    working_directory="/path/to/project",
    run_id="20260425T230000Z_audit_feat_checkout",
    agent_id="agent_03",
    finding_index=0,
    provider="auto",
    file_budget=50_000,
)
```

Shape:

- **Single-finding scope.** Each call patches exactly one finding. Multi-finding patches require multi-tool composition by the caller.
- **Single-agent dispatch.** Audit needs breadth (many agents, many files, small per-file budget). Patch needs precision (one agent, one finding, generous per-file budget). They are different operations and should not share a prompt.
- **Fresh file reads.** Files are read from disk at call time, not from the audit's cached `context_pack.json`. Diff line numbers reflect current file state, not audit-time state.
- **Read-only.** The diff is generated and validated with `git apply --check` but never applied. The caller runs `git apply` against the returned `patch.diff` if they want it.
- **Bounded.** Diffs are hard-capped at 50 changed lines total. Larger fixes return `no_diff` with a reason instead of a guess.

Output contract for the diff agent — exactly one of:

```diff
--- a/path/to/file
+++ b/path/to/file
@@ -42,7 +42,7 @@
 unchanged
-old line
+new line
 unchanged
```

Or:

```json
{"no_diff": true, "reason": "Fix requires creating a new file and judgment about doc structure."}
```

Patch artifacts live under the audit run:

```text
devsession/agent-audits/<date>/<feature>/<run-id>/patches/<agent_id>_finding_<index>_<stamp>/
  prompt.md
  raw_response.txt
  patch.diff           # only present when a diff was returned
  result.json          # status, applies_cleanly, target_files, parse_status
  stdout.txt
  stderr.txt
```

`result.json` carries `applies_cleanly: bool` from `git apply --check`. A `false` here usually means the file drifted between audit and patch — re-running propose_patch picks up the new file state. `applies_cleanly: true` only proves the diff applies, not that the fix is correct; review the patch before running `git apply`.

Files larger than `file_budget` are tail-truncated to a line boundary with the starting line annotated in the prompt, so diff `@@` headers stay aligned with the file on disk. Most source files fit within the default 50K budget; the truncation path matters mainly for large generated files or vendored code.

Why this is a separate tool, not a mode of `audit_feature`:

- Audit and patch have different optimal per-file budgets. Bundling them forces a compromise that hurts both.
- Audits should be cheap to re-run; patches should be opt-in per finding. Decoupling lets the caller spend tokens only where they want diffs.
- Audit findings drift in usefulness over time. Generating diffs at audit time wastes work on findings the user later decides not to patch.
- The diff agent's contract — "given this finding and these files, produce a unified diff" — is its own surface. Mixing it into the audit prompt muddies both.

Write-capable patch parallelism (multiple agents editing files concurrently) is intentionally not implemented. The merge-conflict and silent-divergence failure modes outweigh the throughput gains for actively-developed codebases. Read-only audit + opt-in single-agent diff proposal is the safe shape.

## Context Pack Contract

Each launched agent should receive a compact, explicit work package:

- Project summary and current repository goal.
- Target feature ID, description, and relevant boundaries.
- Core files for the feature, usually full contents for a small set of files.
- Adjacent file excerpts where cross-feature contracts matter.
- Relevant tests, fixtures, migrations, schemas, and configuration.
- Retrieved prior decisions and recent session summaries tied to the feature.
- Known risk signals such as TODOs, ignored lint, disabled tests, broad exception handlers, sensitive API routes, and fragile integration points.
- Allowed verification commands and environment constraints.
- A strict output schema.

The pack should be small enough that the agent can reason over it directly, but rich enough to avoid context-poor guesswork.

## Finding Schema

Agents should return findings in a machine-mergeable format:

```json
{
  "feature_id": "checkout",
  "severity": "high",
  "title": "Webhook handler accepts unverified events",
  "description": "Webhook fulfillment can run before the event is authenticated.",
  "files": [
    {
      "path": "app/api/webhooks/stripe/route.ts",
      "line": 42
    }
  ],
  "repro_path": "Send a forged event payload to the webhook route and trace whether fulfillment logic runs before signature verification.",
  "code_reference": "The handler parses and dispatches the request body before calling Stripe signature verification.",
  "suggested_fix": "Verify the Stripe signature against the raw request body before parsing or dispatching the event.",
  "confidence": "medium",
  "verification": ["npm test -- stripe-webhook"]
}
```

Required fields:

- `feature_id`
- `severity`: one of `info`, `low`, `medium`, `high`, `critical`
- `title`
- `description`
- `files`
- `repro_path`
- `code_reference`
- `suggested_fix`
- `confidence`
- `verification`

A future parent pass should prefer findings with concrete file references, reproducible failure paths, and verification commands. Vague architecture commentary should be rejected or rewritten as notes rather than promoted to issues.

## Modes

### Audit Mode

Audit mode is read-only. It writes a report and does not mutate the repository or remote services.

Current MCP tool:

```python
audit_feature(
    working_directory="/path/to/project",
    feature_id="checkout",
    agents=6,
    provider="claude",
    mode="report"
)
```

### Patch Mode

Earlier drafts of this document framed patch as a write-capable mode of `audit_feature`. The implementation chose a different shape: a separate read-only tool, `propose_patch`, that consumes one audit finding and emits a unified diff without applying it. See **Patch Proposal** above.

Write-capable patch mode (parallel agents editing files in place) remains intentionally unimplemented. Single-agent opt-in diff proposal covers the value (parallelize the find-and-fix loop) without the failure modes (merge conflicts, silent divergence on shared files, partial-fix commits).

## Result Storage

Reports should live with project session artifacts, not inside RecCli's own docs:

```text
devsession/agent-audits/2026-04-25/feat-checkout/<run-id>/
  context_pack.json
  instructions.md
  report.md
  agent_01_instructions.md
  agent_01_findings.json
  agent_01_report.md
```

The report should include:

- Run metadata: project, commit, feature IDs, agent count, model/provider, and timestamp.
- Context pack manifests, including file paths and retrieved session references.
- Raw agent outputs.
- Per-agent findings.
- Rejected findings with reasons.
- Suggested next actions.

## Deduplication And Review

V1 intentionally skips an automated parent deduplication pass. The first version should make duplicate rates visible before adding another LLM call and another prompt contract.

The `reccli.audit_analysis` module provides this measurement. Run `python3 -m reccli.audit_analysis <run_dir>` (or import `measure_audit_overlap`) to cluster cross-agent findings by file overlap + title-token Jaccard and emit per-cluster agreement statistics. Initial empirical observation on a real audit: surface-form similarity under-merges semantically equivalent findings (same issue described with different vocabulary across agents), and high-severity findings do not necessarily correlate with high cross-agent agreement. These observations should shape the parent-pass design — likely an embedding- or LLM-judge-based similarity rather than token overlap.

When deduplication becomes useful, the parent pass should:

- Merge duplicate findings across agents.
- Reject findings without concrete evidence.
- Normalize severity and confidence.
- Check whether a finding contradicts known project decisions.
- Group findings by feature and blast radius.
- Decide whether each finding should become a note, issue, or patch task.

Longer term, RecCli can use summary spans and retrieval references to connect findings back to the sessions or decisions that explain why the code looks the way it does.

## GitHub Integration

GitHub integration should come after local report quality is proven.

Potential stages:

1. Local reports only.
2. Draft GitHub issues for approved findings.
3. Draft PRs for approved patch tasks.
4. Comment on existing issues or PRs when there is high-confidence evidence.
5. Close remote issues only with explicit human approval or a clearly configured allowlist.

Automatic issue or PR closure is intentionally last because false positives are costly and trust-damaging.

## Project-Specific Usage

Individual projects can keep their own audit target list in project planning docs or future RecCli config. For example, a commerce-heavy app might prioritize checkout, auth, upload handling, storage, webhooks, and 3D parsing. Those targets should remain project data; the harness should only require feature IDs and optional risk hints.

This split keeps RecCli reusable across projects while still allowing each project to define the areas where parallel audit agents are most valuable.

## Open Questions

- How should RecCli choose the default agent count for a feature?
- Should context packs be stored verbatim, summarized, or both?
- What is the minimum `.devproject` structure required for useful audit targets?
- How should the harness handle projects without tests or reproducible verification commands?
- Which providers and local execution modes are acceptable for proprietary code?
- Should audit runs update `.devproject` with recurring risk areas or keep that signal only in `.devsession`?
