# Organization Console

The RecCli Organization Console is a localhost-only Next.js viewer for durable
multi-agent organization runs. It reads the same run artifacts as MCP and sends
operator actions through the same supervisor-owned control protocol.

Launch it from any project:

```bash
reccli organization console --project-root /path/to/project
```

The first launch installs and builds the bundled web package automatically.
The server binds to `127.0.0.1`, generates a per-launch access token, opens the
browser, and does not expose the project over the network.

Claude Code and Codex can also call `open_organization_console`. The launcher
reuses a matching running console and its valid token instead of starting a
second server on the same port.

For projects with a tracked launch contract, `start_project_organization`
performs preflight, dynamic mission selection, organization launch, and console
open/reuse in one MCP call.

Projects may opt into `latest-terminal-conclusion` continuation in that tracked
contract. After the first reviewed mission, the one-line launcher then derives
the next bounded mission from the newest lead-authored terminal conclusion
instead of replaying the project's initial mission. The successor must verify
the handoff, avoid repeating conclusive work, exhaust reversible
research/design/test/prototype work, and stage a specific human decision only
when an irreversible authority seam remains. Host-fallback conclusions and
promotion-ready or approval-pending runs cannot trigger this continuation.

## Layout

- The top rail contains the lead and eight team members. Selecting a person
  changes the operator target.
- The left pane is the operator conversation: human steering messages,
  acknowledgements, and public organization traffic involving the selected
  agent.
- The right pane contains eight independently scrolling work streams. Each
  stream shows the primary-manager assignment, live operational activity,
  durable turn summaries, routed messages, provider, state, errors, and
  artifacts.
- Hidden model reasoning is never exposed. Only durable operational records are
  displayed.

## Approval staging

When the only remaining dependency is human authority, the release manager
writes a reviewed dossier and closes the run as `completed_pending_human`.
The console renders a single approval page containing:

- the exact request hash, Git checkpoint, and report or promotion candidate;
- the lead conclusion, evidence and tests, blockers, and authorization limits;
- text previews plus immutable Git blob identities for staged dossiers; and
- the exact local effect of the approval button, including whether remote push
  is disabled.

`Approve & start successor` records an immutable human decision and launches a
fresh organization with that decision in its read-only evidence. It never
revives the stopped supervisor. `Approve & apply locally` revalidates a fully
verified promotion request and fast-forwards only a clean local branch. RecCli
does not push a remote from either action.

The panel can enable browser notifications. Once permission is granted, the
console sends one local notification per request hash so a waiting decision is
visible without constant polling of the page.

## Terminal lead conclusion

Every run that reaches a terminal state publishes:

```text
devsession/agent-organizations/<run-id>/run-conclusion.json
devsession/agent-organizations/<run-id>/run-conclusion.md
```

For normal completion, reviewed `completed_no_promotion`,
`completed_pending_human`, round-limit, and stalled outcomes, RecCli gives the
organization lead one final read-only
synthesis pass outside the configured working-round and closeout budgets. The
report separates accomplishments, conclusive findings, evidence, scientific or
product blockers, infrastructure failures, unresolved work, exact candidates,
the exact no-promotion dossier when present, promotion readiness, and the
smallest recommended next action.

Round counts and agent-turn counts are separate durable fields. A scheduling
round may execute several agent turns in parallel.

Cancellation never starts another model turn after the stop request. A
supervisor crash may also prevent the lead pass. In either case RecCli writes a
conservative `host-fallback` conclusion from durable mechanics and the last
available lead summary without inferring scientific or product truth.

`organization_status` returns the document as its top-level `conclusion`, and
the console renders it as the lead after-action report. MCP is pull-based:
launching agents must poll `organization_status` until the run is terminal;
RecCli cannot inject an unsolicited reply into an agent turn that has already
ended.

## Live activity telemetry

Native Claude Code and Codex JSON events are consumed while a turn is running.
RecCli classifies and appends a provider-neutral record to the run-local:

```text
devsession/agent-organizations/<run-id>/activity.jsonl
```

The console can therefore show an agent reading project documentation,
searching files or symbols, running tests, inspecting Git history, editing
workspace files, using another tool, or waiting for a routed response before
the final turn reply exists.

When a project opts into the autonomous experiment loop, the console also
renders its host-owned baseline and trial ledger: the single mutable file,
immutable evaluator, kept and discarded challengers, gates or metrics,
duration, bounded patch shape, same-host resource fingerprint, SHA-256 chain
status, and manager-wake reason. This is distinct from agent chat and does not
depend on a model summarizing its own progress.

The runner also publishes `host-state.json`, a host-owned mechanical brief for
launch HEAD, mission-mentioned commit existence and ancestry, candidate kinds,
integration identity, governance state, workspace heads, and experiment
budget. Agents are instructed to trust these mechanical facts and report a
concrete contradiction instead of repeatedly rebuilding the same Git census.
The brief does not decide scientific meaning.

Scientific runs also append `experiments.jsonl`. It is the durable shared
meter for Git-backed probes/data and sealed generated-output bundles. Each
worker turn can claim at most one slot even when it uses both channels, and the
claim is serialized before parallel candidates can commit. The console can use
these records to show which agent consumed each work bundle, its paths,
candidate identity, and any rejected over-budget attempt instead of inferring
experiments from the number of artifact manifests.

Final-review messages expose their binding state directly. If a reviewer sends
an exact-candidate `NO_VETO`, `APPROVED`, or blocking marker with the `review`
tag, the runner records a `message.decision_normalized` event and delivers it
as `decision`. A decision marker without the complete exact candidate identity
is rejected and routed back to the reviewer as a blocker, so the UI does not
display a nonbinding review as a completed gate.

Telemetry is deliberately operational rather than cognitive:

- model thinking and chain-of-thought are never copied;
- command output and document contents are not copied;
- known secret assignments and URL credentials are redacted;
- paths are reduced to workspace, context-pack, or run-relative display paths;
- the final schema-constrained reply remains the authoritative turn result.

A `read` event proves that a provider invoked an observed read operation. It
does not prove scientific comprehension. Decisions, citations, tests, and
review evidence remain responsible for establishing comprehension.

Turn records distinguish bootstrap from incremental prompts and include prompt
character counts. They also preserve raw provider token usage alongside
`accounted_usage`; Codex cumulative resumed-thread counters are converted to
per-turn deltas before the run and provider totals shown by the console are
updated.

## Delegation and execution

The hierarchy is an assignment dependency, not a ban on parallel execution:

1. The lead performs macro reconnaissance and delegates bounded outcomes to
   managers.
2. Managers refine those outcomes using their specialist context.
3. A worker's first turn requires a primary-manager `plan` or `handoff` with a
   named `workItem` and risk.
4. Once assigned, workers run in parallel and report through managers.
5. The lead wakes on manager research, manager-summarized worker progress,
   promotion decisions, or operator steering.

The runner verifies the complete lead-to-manager map after round one and the
complete manager-to-worker map after round two. A lane with no implementation
work still receives an explicit research, review, or standby work item. Missing
delegations fail the run before parallel execution instead of leaving agents to
invent overlapping scopes.

After that gate, lead and manager roles are event-driven: saying
`state=working` does not schedule another turn without a new inbox event.
Workers may continue their bounded assigned lane. Veto auditors wake only for
an exact candidate or release dossier, and release closeout stops early when
its structural progress fingerprint is unchanged.

Advisory traffic from alternate managers remains visible to workers, but it
cannot activate an unassigned worker.

## Control protocol

Controls are immutable JSON requests under:

```text
devsession/agent-organizations/<run-id>/control/requests/
```

The organization worker applies them between synchronized rounds and writes a
separate acknowledgement under:

```text
devsession/agent-organizations/<run-id>/control/acknowledgements/
```

Supported controls:

- `message`: add a steering message to an exact agent or role-group inbox;
- `pause`: finish the active round, then wait at the boundary;
- `resume`: leave the paused boundary;
- `cancel`: persist cancellation and terminate the supervisor process group.

The UI never edits `status.json`, `messages.jsonl`, or an agent inbox directly.
It distinguishes queued, applied, rejected, and signalled actions. Runs created
before `reccli.organization-control.v1` remain observable and cancellable but
cannot accept live inbox steering.

## CLI equivalents

```bash
reccli organization list --project-root /path/to/project
reccli organization status RUN_ID --project-root /path/to/project
reccli organization message RUN_ID "Check the serialized fixture envelope." \
  --target worker-a --tag question --project-root /path/to/project
reccli organization pause RUN_ID --project-root /path/to/project
reccli organization resume RUN_ID --project-root /path/to/project
reccli organization cancel RUN_ID --project-root /path/to/project
```
