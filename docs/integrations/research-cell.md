# On-demand research cell

RecCli's `scientific` topology includes an event-driven research cell for
load-bearing technical questions that repository authority and reproduced
evidence do not settle.

## Structure

- `manager-b` is the research director.
- `research-scout` searches primary sources, standards, official
  documentation, and original papers.
- `math-auditor` independently derives and attacks the claim without seeing
  the scout's conclusion first.
- Workers remain responsible for truth-known computational falsification and
  bounded implementation.
- `manager-c` reviews the resulting decision and exact candidate with full
  context but veto-only authority.

The two specialist slots are part of the topology but use no model turn until
Manager B sends a named commission. Each specialist turn uses a fresh native
Claude Code or Codex session. In a mixed-provider run, the math auditor is
assigned the provider opposite the source scout.

## Durable protocol

Manager B commissions both specialists on the same neutral `workItem`. Each
specialist writes a validated
`reccli.organization-research-fragment.v1` JSON artifact. RecCli copies the
exact fragments into the run-local `research-cell/` registry.

Manager B then writes one
`reccli.organization-research-decision.v1` packet that binds:

- the decision being unlocked;
- exact claim, equation, units, and conventions;
- measurement or observation model when applicable;
- both independent fragment hashes;
- primary sources and supported claims;
- assumptions, validity domain, alternatives, and degeneracies;
- project evidence, external evidence, and policy choices as separate lists;
- the smallest falsifier;
- code implications and prohibited inferences; and
- exact downstream work items, if a bounded change is authorized.

RecCli rejects a dependent `plan` or `handoff` for the commissioned work item
until a validated packet records `authorized_bounded_change`. Research-only
and human-authority-required dispositions cannot authorize implementation.

## Authority

The packet is traceability evidence, not scientific acceptance. It cannot
change project authority, immutable evidence, acceptance standards, or human
promotion requirements. A fully sighted reviewer must still inspect the
packet, cited support, falsifier, tests, and exact candidate.

The organization console exposes commissions, fragment counts, decisions, and
authorized work items without waking dormant specialists.
