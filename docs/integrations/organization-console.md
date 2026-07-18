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

Claude Code and Codex can also call `open_organization_console`.

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

Telemetry is deliberately operational rather than cognitive:

- model thinking and chain-of-thought are never copied;
- command output and document contents are not copied;
- known secret assignments and URL credentials are redacted;
- paths are reduced to workspace, context-pack, or run-relative display paths;
- the final schema-constrained reply remains the authoritative turn result.

A `read` event proves that a provider invoked an observed read operation. It
does not prove scientific comprehension. Decisions, citations, tests, and
review evidence remain responsible for establishing comprehension.

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
