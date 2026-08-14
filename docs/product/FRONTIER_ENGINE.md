# The Frontier Engine

**Status:** Implemented 2026-08-14. The machinery that lets organizations do
frontier work autonomously: manufacture the next falsifiable gate, chain
between human authority seams, and land protected-path changes through
one-click ratification.

## Why this exists

Six live runs measured where organizations produce value: exactly when a
falsifiable gate exists (runs four and five), and never when one doesn't
(the July record). Frontier work, by definition, lacks a pre-written gate,
because discovering the right success criterion is the work. So an autonomous
frontier driver must be a loop that authors its own next gate and puts a
human signature between proposal and law.

## The loop

```
explore run ──> gate proposal staged ──> pending_human
                                             │  one click: ratify
                                             ▼
                gate applied locally + successor launched
                                             │
close run  ──> implementation candidate ──> promotion staged
                                             │  one click: approve
                                             ▼
                merged + ledger credit ──> next explore run
```

Every arrow between clicks is autonomous. Every click is a staged,
hash-sealed decision packet, not a review session. The compounding is real
but bounded: admission validation on every link, the barren-decline counter
on unproductive chains, and the human seams exactly where authority changes
hands.

## The three mechanisms

### 1. Successor-admission proposals

A terminal lead conclusion may set `proposed_successor_admission` to a
complete admission block. The host validates it like any admission
(invalid proposals normalize to null and the parent contract carries), and
autonomous continuation uses it in place of the parent's contract. This is
how a chain re-scopes each link from what the previous link learned.

### 2. Gate proposals

An exploration run stages its proposed capability gate under artifact
staging, where its write scope already allows:

```
.reccli-org-artifacts/<run>/gate-proposal/
  gate-proposal.json          # the manifest
  files/...                   # predicate policy edits, fixtures, wiring
```

Manifest schema (`reccli.organization-gate-proposal.v1`):

```json
{
  "schema": "reccli.organization-gate-proposal.v1",
  "predicate_id": "shell-detection-v1",
  "evaluator_id": "geometry-eval-v1",
  "rationale": "why this gate, from this run's evidence",
  "baseline_command": "how the baseline was measured",
  "measured_baseline": 0.42,
  "proposed_tolerance": 0.001,
  "discrimination": {
    "truth_exact_command": "how the truth-exact case was scored",
    "truth_exact_score": 0.0000004,
    "corrupted_command": "how the corrupted case was scored",
    "corrupted_score": 0.31
  },
  "what_fools_this_gate": "the proposal's own blind-spot analysis, legible to the owner",
  "files": [
    {"path": "<staging path>", "target": "<repo-relative protected path>"}
  ]
}
```

### Executable ratification

The click is the only place law enters the system, so ratification must be
judgeable without domain expertise. Every proposal ships a discrimination
proof: a truth-exact case and a corrupted case with measured scores, and the
host checks the arithmetic mechanically: truth-exact must score within the
proposed tolerance, corrupted must not. A gate that fails its own proof is
an error in the packet, not a judgment call for the owner. The cylinder
scorer is the canonical example: a ratified-wrong gate that claimed to
measure segmentation and actually measured tessellation fidelity would have
failed this exact check (truth-exact input scored 61x its tolerance).
`what_fools_this_gate` is the mandatory blind-spot analysis: if a gate
cannot be explained to its owner in a page, that is a proposal defect, not
an owner defect.

The pending-human approval packet carries the extracted, validated manifest
(malformed proposals surface an `error` field to the reviewer instead of
silently vanishing). The org can never apply the files; the targets are
protected paths. The human approval click applies them byte-for-byte from
the exact approved candidate tree, commits locally, and launches the
successor against the ratified gate. Traversal targets are rejected on both
sides.

### 3. The existing spine

Everything else was already built: the admission gate, evaluator-gated
retention, the promotion plane, honest terminals (`no_promotion`,
`completed_no_op`), the outcome ledger, and continuation with barren
decline. The frontier engine is those pieces plus the two seams above.

## Mission recipe for a gate-authoring run

```
work_class: uncertainty_reduction (or hypothesis_test)
done_condition: "a ratifiable gate proposal is staged: predicate id,
  evaluator wiring, fixture files derived from <real source geometry>,
  a baseline measured by <command>, and a proposed tolerance justified
  in the dossier"
stop_conditions:
  - "the fixture cannot be derived from the named source geometry"
  - "the baseline cannot be measured reproducibly"
mission: recon the capability area; derive the fixture from real geometry
  (never synthetic-only); measure the baseline; stage the gate proposal;
  close pending_human. An honest "no gate is derivable" no_promotion
  dossier is a success.
```

Ledger rule: a gate-authoring run is credited (`candidate_used`) when its
gate is ratified; the ratifying click records it. The 2-of-3 adjudication
bar applies to frontier chains the same as everything else.

## Holds

Two sequencing holds, mechanical where possible:

1. **Attended first.** No overnight chains until one full loop (gate
   ratified, implementation, promotion plane end to end, human approval)
   has fired live and attended.
2. **Chain cap.** The successor-admission seam bounds barrenness but not
   drift: each link re-scopes the next, so a chain could walk away from the
   product goal one plausible proposal at a time. An unattended chain ends
   after three consecutive terminal-conclusion links and waits for a human
   relaunch (`MAX_AUTONOMOUS_CHAIN_LINKS`). The conclusion prompt binds
   proposals to the same standing product goal (a refinement, never a
   pivot), and the cap holds the line until the ledger has data on real
   chains.

## What this is not

Not a bypass of human authority: protected paths still cannot be touched by
any agent, ever; ratification applies them. Not unbounded autonomy: chains
stop on barren decline, the chain cap, stop conditions, round budgets, and
every `pending_human` seam. Not comprehension-free: ratification is
executable precisely so the owner can judge evidence packets without
knowing solver internals: the dossier layer is the comprehensible layer,
and the machinery is obligated to produce evidence legible to its owner.
And not yet proven: the promotion plane has never executed end to end live.
The first full loop closure is the next milestone, and until it happens,
unattended trust remains revoked per the recorded usage rule.
