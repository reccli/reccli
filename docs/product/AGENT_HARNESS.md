# Agent Harness

**Status:** Product/design document with implemented audit, patch-proposal, and organization MCP workflows.

This document describes a RecCli-native harness for launching scoped coding agents against project memory. `audit_feature` and `propose_patch` cover bounded read-only analysis. The asynchronous organization tools cover opt-in delivery work in isolated Git worktrees. All three paths dispatch through the installed subscription-auth Claude Code or Codex CLI rather than model APIs.

## Overview

The agent harness uses RecCli's existing project memory to run focused, parallel agent work. Each v1 agent receives a bounded work package for one feature or risk area, performs a read-only audit, and returns structured findings for a human to review.

The key product bet is that agent quality improves more from context quality than raw concurrency. A small number of agents with feature-scoped context packs should produce better findings than a large fleet pointed at a repository with generic instructions.

The delivery workflow adds a second bet: hierarchy is useful for attention
management, but seams need independent review. Its default organization keeps
workers focused behind managers, lets managers coordinate laterally, rotates an
alternate manager into each worker handoff, and gives the exact integrated
candidate to a fresh final verifier.

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

## Organization Delivery Workflow

Organization runs are explicit, write-capable jobs for a concrete mission. They
are not an always-on daemon and do not poll for work. Start one from any
MCP-connected Claude Code or Codex session:

```python
start_organization(
    working_directory="/path/to/project",
)
```

This preferred one-line path requires a tracked project contract. With no
caller-supplied mission, `start_organization` runs the contract's shell-free
preflights, verifies the emitter's dynamic tracked mission against current
HEAD, refuses duplicate or pending-approval runs, opens the console, and
launches the emitted payload unchanged unless that same tracked contract
explicitly opts into terminal-conclusion continuation. In that mode, the latest
eligible lead-authored conclusion replaces the initial mission with a
hash-bound successor handoff. Experiment budgets renew to the project's
configured per-run cap unless the contract explicitly makes the cap
chain-wide.

```json
{
  "continuation_policy": {
    "mode": "latest-terminal-conclusion",
    "eligible_statuses": [
      "completed_no_promotion",
      "round_limit",
      "stalled"
    ],
    "eligible_promotion_readiness": ["not_ready", "no_candidate"],
    "carry_experiment_budget": false
  }
}
```

Continuation budgets are per-run by default: the emitted
`max_experiments` cap is renewed for each successor while every parent
conclusion retains its prior usage. Set `carry_experiment_budget` to `true`
only when the cap must apply across the complete continuation chain.

```python
start_organization(
    working_directory="/path/to/project",
    mission="Implement the feature and satisfy these acceptance criteria: ...",
    launch_mode="custom",
    provider="auto",
    topology="google-rotating",
    max_rounds=8,
    max_concurrency=5,
    evidence_paths=["out/project-intelligence", "/path/to/reference-assets"],
)
```

`launch_mode="custom"` is required when a project launch contract exists. This
makes bypassing the project's readiness checks and dynamic mission an explicit
choice. On repositories without a project contract, a supplied mission uses
the custom path automatically.

The call returns immediately. Poll with:

```python
organization_status(
    working_directory="/path/to/project",
    run_id="<returned-run-id>",
)
```

Stop it with `cancel_organization`. The status path is durable, so polling still
works after the MCP host reconnects. Cancellation reconciles status with the
actual process group; a stale `cancelled` status cannot suppress termination of
a still-live supervisor or native-agent child.

The default work cap is eight synchronized rounds. A round is a barrier, not a
single model call: every scheduled organization member runs one agent turn in
parallel during that round. Status therefore reports `round/max_rounds`, the
number of agent turns scheduled in the current round, and cumulative completed
turns separately. If an immutable candidate is still traversing review when the
work cap arrives, RecCli permits at most four additional closeout boundaries.
Closeout never wakes workers or starts implementation/experiments; it can only
finish existing review, manager routing, host integration, release review, and
finalization. Routine candidate-less chatter does not schedule closeout. RecCli
also fingerprints release-relevant governance, candidate, integration,
artifact, and inbox state; an unchanged fingerprint ends closeout instead of
buying another round of reworded review. Increase `max_rounds` explicitly for
unusually large missions rather than treating eight as a total-agent-call
budget.

The release manager has three reviewed terminal dispositions. `promote`
produces the normal reversible promotion package. `no_promotion` binds an exact durable
run-artifact dossier, requires the same lead and rotating-review approvals, and
ends the run as `completed_no_promotion` without exporting or promoting code.
`pending_human` binds an exact reviewed approval dossier and ends as
`completed_pending_human` when reversible work is complete but sponsor
authority is still required. Approval records the exact request and starts a
fresh successor rather than reviving a terminal process. These dispositions
stop conclusive or authority-blocked work before exhausting closeout rounds;
ordinary unfinished work still ends `round_limit` or `stalled`.

### Native provider assignment

`provider="auto"` is diversity-aware for organization runs. RecCli checks the
installed Claude Code and Codex CLI authentication status without retaining
account output. When both subscriptions are usable, auto resolves to `mixed`.
When only one is usable, auto resolves to a homogeneous run on that provider.

The default mixed assignment is relative to the MCP host provider:

| Role | Provider |
|------|----------|
| Mission lead | Host |
| Manager/worker lanes A and C | Opposite |
| Manager/worker lanes B and D | Host |
| Research scout | Research-director provider |
| Mathematical auditor | Opposite research-scout provider |
| Release manager (`manager-d`) | Host |
| Rotating release reviewer | Prefer opposite |
| Fresh final verifier | Opposite |

Keeping each worker and primary manager on the same provider preserves shared
working assumptions. Governance then prefers an eligible alternate manager on
the other provider, adding independent model-family judgment at the review
seam. Provider assignments are fixed for the run, recorded in `request.json`,
`run.json`, status, per-turn traces, and `result.json`; agents do not switch
provider between rounds. Token usage is reported both in aggregate and by
provider. Claude invocation usage is incremental. Codex resumed-thread usage is
cumulative, so RecCli stores the raw provider record but adds only the
session-to-session delta to run totals. Per-turn traces expose both `usage` and
`accounted_usage`.

Pass `provider="claude"` or `provider="codex"` to force a homogeneous run.
Pass `provider="mixed"` to require both authenticated CLIs and fail early if
either is unavailable. Mixed runs currently require `model="auto"`, allowing
each native CLI to use its own configured model rather than passing one
provider-specific model name to both.

Leads and managers have native external research available. In the scientific
topology, Manager B additionally directs two event-driven specialists:
`research-scout` and `math-auditor`. A materially unsettled model, method,
standard, identifiability, uncertainty, or numerical claim wakes both on the
same neutral work item. Each specialist uses a fresh native session; the math
auditor has no edge to the scout and derives the claim before seeing the
scout's conclusion. Their validated fragments are synthesized into a
`reccli.organization-research-decision.v1` packet. RecCli rejects dependent
implementation on the commissioned work item until that packet explicitly
authorizes a bounded change.

The report separates literature findings, required assumptions, project-held
evidence, and human policy choices. The lead enforces the trigger and searches
directly only for macro reconnaissance, conflicting sourced conclusions,
scope-changing standards, or terminal adjudication; it does not repeat a
completed specialist pass. External literature informs alternatives but never
overrides project authority, immutable evidence, reproduced tests, or human
acceptance. The specialists remain dormant when repository authority already
settles the question.

The default topology is deliberately close to Org-Bench's successful Google
shape while adding two bounded controls:

- Workers communicate through a primary manager and have no direct lead or
  peer edge. Managers form a lateral mesh for routine coordination and selective
  escalation.
- Each immutable worker candidate is assigned to an alternate manager for review.
  The assignment rotates deterministically and excludes the worker's primary
  manager and, when possible, the release manager.

`manager-d` owns the integration decision while RecCli owns Git mutation. A
rotating non-release manager and the mission lead must approve the exact
integration commit before finalization. A
new read-only Claude/Codex session then verifies that exact commit without team
messages or prior agent-session state. This final agent is intentionally fresh;
the rotating managers are intentionally context-aware. The two checks cover
different failure modes.

Workers do receive documentation. On their first turn they receive the mission,
the `.devproject` feature map (including linked documentation paths), the team
charter, and their role. Their worktree contains source, tests, and repository
documentation, and the prompt explicitly tells them to inspect task-relevant
docs and published interface decisions. They do not receive raw manager
deliberation or unrelated inbox traffic. The scientific adversarial auditor is
the deliberate exception: it receives the full relevant durable decision
record, candidate bundle references, prior-attempt evidence, and primary
evidence so independence does not come from context starvation.

Static mission, charter, context-pack inventory, protected-path inventory,
artifact protocol, and research rules are fully hydrated only on an agent's
bootstrap turn. Resumed turns receive an incremental prompt containing current
inbox, governance, budget, workspace, and release state plus durable pointers
to the retained bootstrap material. Each trace records `prompt_mode` and
`prompt_chars`, making prompt amplification measurable.

Projects may additionally pass a tracked `context_manifest` using schema
`reccli.organization-context-packs.v1`. It declares ordered `common.paths`,
per-agent `agents.<id>.paths`, optional `library_paths` on either pack, and
`full_context_agents`. RecCli expands tracked directories, copies the selected
files into per-agent run-owned boxes, preserves project-relative paths under
`canonical/`, removes write bits, records hashes, and verifies both boxes and
canonical sources throughout the run. Required `paths` are the ordered first-
turn reading set. `library_paths` are an indexed, lane-scoped reference set that
agents consult when relevant rather than ingesting wholesale. The assignment is
educational routing, not a deny-read ACL: canonical cross-lane documents remain
available when an interface or contradiction requires them.

```json
{
  "schema": "reccli.organization-context-packs.v1",
  "common": {
    "purpose": "Authority every agent must know.",
    "paths": ["AGENTS.md", "docs/Core/Critical"]
  },
  "agents": {
    "worker-a": {
      "purpose": "Reproduction and metrology.",
      "paths": ["research/metrology.md"],
      "library_paths": ["docs/attempts/metrology"]
    }
  },
  "full_context_agents": ["lead", "manager-a", "manager-b", "manager-c", "manager-d"]
}
```

Paths and directories must be project-relative, tracked, and free of symlinks.
Workers read required common-plus-lane `paths` before substantive work and
consult relevant `library_paths` before acting on a matching hypothesis or
failure mode. Full-context agents read the common authority first and use the
lane union as an indexed library, avoiding needless up-front context ingestion.

`topology="scientific"` is a single reversibility-based organization for
evidence-heavy research and engineering. RecCli supplies generic role slots:
reproduction and receipt integrity, hypothesis/model evaluation, structural
and integration validation, uncertainty/alternative explanations, and an
on-demand source-scout/mathematical-auditor research cell. The
project's tracked context manifest specializes those slots with domain-specific
lane purposes and documents. Its four workers may choose hypotheses, modify
disposable branches, and run sandbox experiments immediately after the
lead/manager delegation gate opens. `max_experiments` is one atomic scientific
work-bundle budget across both persistence channels: Git-backed probes,
fixtures, measurements, result data, or other non-report evidence beneath the
run artifact prefix, and explicitly reported ignored/generated outputs. A
worker using both channels in one turn consumes one slot. Concurrent turns
cannot collectively pass the cap. Markdown/text reports that only summarize
existing evidence are free, while placing executable probes or result data
inside a report directory does not evade accounting. Every claim and rejection
is recorded in `experiments.jsonl`. The counter limits resources; it does not
pretend to decide novelty or scientific merit. Manager A coordinates evidence;
Manager B coordinates hypotheses and owns research synthesis; manager C is
fully sighted and veto-only; and manager D may
integrate only patch-identical candidates whose adversarial review completed
without a veto. No agent can apply the resulting proposal to the canonical
branch or archive.

Candidate-level adversarial review rotates across eligible managers A and B by
lane, excluding the candidate's primary manager, release manager, and final
veto reviewer while preferring a different provider. Manager C remains the
dedicated final veto auditor. This separates broad peer falsification from the
stable release-level audit role.

Lead and manager roles are inbox-driven after delegation. A prior
`state=working` response does not wake them by itself. Manager C receives
`review`/`decision` traffic only when it names an exact candidate or
release-dossier identity; candidate-less coordination uses `plan`, `question`,
`answer`, or `blocker`. Manager D wakes for an exact candidate or an explicit
release-risk dossier instruction. Workers remain able to continue a bounded
assigned implementation lane.

Review intent and routing tags cannot disagree at the final gate. When a final
reviewer begins a `review` message with the topology's decision marker
(`NO_VETO`/`BLOCKED` for scientific veto review, `APPROVED`/`BLOCKED`
otherwise) and includes the complete exact candidate identity in both the
candidate field and content, RecCli normalizes that message to `decision`
before governance records it. A marker that omits the full identity is dropped
and the reviewer receives a blocker requesting a corrected exact-candidate
decision. This preserves the distinction between ordinary review discussion
and a binding decision without losing a valid decision to schema vocabulary.

```python
start_organization(
    working_directory="/path/to/scientific-project",
    mission="Autonomously investigate the objective and prepare a promotion proposal.",
    launch_mode="custom",
    provider="auto",
    topology="scientific",
    max_rounds=8,
    max_experiments=3,
    evidence_paths=["out/project-intelligence", "/path/to/reference-assets"],
    protected_paths=["docs/frozen-authority.md", "data/immutable-input.bin"],
    context_manifest="benchmarks/organization/context-packs-v1.json",
)
```

Each native organization member has a resumable subscription-backed CLI
session. RecCli invokes `claude -p` or `codex exec`; no API SDK or API key is
used. Claude sessions use native session IDs/resume, while Codex sessions use
the thread ID emitted by `codex exec --json`. The fresh Codex verifier is
ephemeral, and the fresh Claude verifier disables session persistence.

The project must have a clean tracked Git worktree before launch. Agents operate
on isolated branches/worktrees, and durable traces are written under:

```text
devsession/agent-organizations/<run-id>/
  request.json
  status.json
  run.json
  host-state.json              # host-owned Git/candidate/governance brief
  result.json                 # terminal runs
  promotion-request.json      # scientific proposal awaiting human authorization
  evidence-manifest.json      # selected ignored/external sources + hashes
  context-pack-manifest.json  # per-agent docs, canonical hashes, assigned scope
  context-packs/              # read-only common-plus-lane educational views
  evidence-snapshot/          # read-only view shared by every agent
  candidate-artifacts/        # sealed ignored/generated output bundles
  candidate-artifacts.jsonl   # durable bundle index
  artifact-manifest.json      # hashes and source candidate for final outputs
  deliverables/               # verified run-scoped reports/plans/artifacts
  events.jsonl
  messages.jsonl
  turns/<agent>.jsonl
  *_stdout.txt
  *_stderr.txt
```

Run-scoped deliverables do not use the project's ignored `devsession/` path as
an inter-agent Git surface, and temporary drafts are not placed in the product's
permanent `docs/` tree. Agents write those files under the run-specific
`.reccli-org-artifacts/<run-id>/` staging prefix in their isolated branches;
RecCli validates and commits them after the native turn.
That gives worker handoffs, alternate-manager review, and release integration
the same immutable-SHA semantics as source changes. Reviewers can inspect a
candidate artifact with `git show <sha>:.reccli-org-artifacts/<run-id>/<path>`.

After exact-candidate review, the orchestrator
reads the artifact blobs from that exact commit and exports them to
`<run-dir>/deliverables/`. `artifact-manifest.json` records their SHA-256 and
Git blob IDs. When staged artifacts exist, `result.json` exposes two commits:

- `verified_candidate`: the exact integration candidate that managers and the
  reviewers evaluated, including its temporary artifact staging tree.
- `promotion_candidate` / `promotion_branch`: an orchestrator-created child
  commit whose tree differs only by removing temporary RecCli staging,
  including any staging inherited from an exact candidate recovered from a
  prior organization run.
  This is a clean local proposal. For a scientific run it is not merged,
  pushed, imported, or canonically accepted; `promotion-request.json` binds the
  proposal and lists the actions requiring human authorization.

Native agents receive read-only Git inspection (`status`, `diff`, `show`,
`rev-parse`) but do not stage, commit, merge, or cherry-pick. Codex deliberately
protects `.git` and resolved worktree metadata even when a workspace is
writable, so RecCli performs those mutations in the trusted supervisor after
validating deny-write and role scopes. This provider-neutral ownership also
prevents Claude and Codex lanes from having different Git capabilities. The
harness never grants or requires `git push`, hosting-provider credentials, or
GitHub access; remote publication remains a separate, explicit user action.

When the canonical project has `.venv/bin/python`, RecCli creates an ignored
worktree-local launcher at the same relative path. It executes the canonical
environment but prepends the candidate worktree's `src/` and root to
`PYTHONPATH`, so tests do not silently import an editable install from the
caller checkout. The bridge is runtime infrastructure, never candidate content.

### Ignored and external evidence

Git worktrees intentionally contain only the committed checkpoint. Pass
`evidence_paths` when agents also need ignored generated outputs, sealed
receipts, or assets from a related project. Relative paths resolve from the project root;
absolute paths may select external local evidence. RecCli rejects symlinks,
prefers APFS clone copies on macOS, falls back to ordinary copies elsewhere,
removes all write bits, and records SHA-256, size, mode, and timestamp for every
file. Every native session receives the same snapshot path, not the original
source path. Inventory is checked after every round and content is fully
re-hashed before release.

RecCli exports `RECCLI_EVIDENCE_MANIFEST` and
`RECCLI_EVIDENCE_SNAPSHOT_ROOT` to every native session so a project-owned
candidate validator can map canonical ignored paths to the exact selected
snapshot without assuming the full archive is present.

This does not make a dirty tracked worktree launchable. Source and tracked
documentation still need one clean checkpoint so every branch has an
unambiguous code base.

Pass project-relative `protected_paths` for tracked immutable evidence,
ledgers, authority documents, or frozen standards. RecCli removes write bits in every
worktree and rejects any turn whose Git delta touches a protected path. Agents
may draft proposed authority changes under the run-artifact staging prefix.

### Generated outputs outside Git

Large generated experiment outputs should not be force-added to Git. A writable agent
leaves each new ignored output in its isolated worktree and names only the
required path in the structured reply's `artifacts` array while handing off the
`RECCLI_HOST_CANDIDATE` marker. RecCli first resolves that marker to its
host-materialized exact commit. After every agent in the round stops, RecCli seals those paths
under `candidate-artifacts/`, hashes them, makes them read-only, binds the
manifest to the exact candidate commit, and notifies the primary manager.

The bundle is durable evidence, not an automatic write into the caller's
ignored archive. Projects with immutable archives should provide a validated,
no-overwrite ingest command after human authorization. This prevents disposable
worktree cleanup from losing experiment results without putting heavy binary
payloads into Git object storage.

Scientific workers use temporary run-local identifiers. Canonical attempt IDs
are assigned only by the project's authorized archive-import transaction.

### Autonomous experiment loop

Scientific projects may pass a tracked `experiment_policy` to bind a bounded
worker campaign to one mutable file and one immutable evaluator. RecCli runs an
uncharged baseline, enforces one host commit and bounded one-file patch per
experiment slot, fixes common numerical thread pools, checks the same-host
runtime fingerprint, keeps strict improvements, host-reverts regressions, and
appends a SHA-256-chained compact ledger with hashed command logs. One campaign
is active at a time. Routine keep/discard trials do not wake managers; crashes,
inconclusive results, plateaus, budget exhaustion, cross-file needs, and final
review do. Patch bounds do not prove semantic cohesion, and a same-host
fingerprint is not cross-hardware equivalence.

The evaluator policy and its inputs are automatically deny-write. A pass-only
evaluator cannot rank two passing candidates and therefore returns
inconclusive; quantitative projects can declare immutable hard gates and
minimize/maximize metrics through a JSON result contract. See
`docs/integrations/autonomous-experiment-loop.md`.

`request.json` includes `host_provider`, `provider_assignments`,
`blind_verifier_provider`, and authentication-state labels. It never contains
authentication output, tokens, or API keys.

Subscription quotas and elapsed time remain real constraints: the default team
can run up to nine concurrent/persistent agent sessions. Use `max_rounds`,
`max_concurrency`, `turn_timeout_seconds`, and—for scientific runs—
`max_experiments` as factual resource limits rather than semantic proof gates.

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
