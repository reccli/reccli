# Organization Plane Audit and the Verified-Value Collapse

**Date:** 2026-08-12
**Status:** Executed. Each section names the commits that carried it.

## Why this audit exists

Thirteen organization runs were launched across scan2param and 3dcarparts
between 2026-07-16 and 2026-07-19. Eight were tombstoned, two cancelled, one
failed at turn 1, and the best run hit its round limit after 63 turns. Summed
over the runs with usage records, the system processed **1.3 billion input
tokens (91% cached) and 8.7 million output tokens, and merged nothing**. The
one implementation candidate that survived review (`b8e2934`) was never
approved and never used.

The forensic mechanism, verified from the run artifacts:

1. **The scheduler structurally guaranteed a management majority.** Round 1
   woke only the lead, round 2 only managers; managers messaging managers
   generated inbox events that scheduled more manager turns. In the surviving
   63-turn run, lead plus managers took 50 turns; all four workers combined
   took 13.
2. **The review lattice converted candidates into traffic instead of value.**
   120 of 174 messages were status/review/decision; 12 were handoffs of work.
3. **29 of 32 materialized candidates were artifact-only** (prose reports with
   git identities), and each one bought review traffic and closeout rounds.
4. **Six of nine runs re-adjudicated one candidate.** Run 2 produced the only
   original implementation on day one; successor conclusions carried the
   candidate and its `next_action`, so runs of 50-71 turns each re-reviewed
   the same diff.
5. **The one value-denominated gate was unreachable.** Both instrumented runs
   used 0 of 3 experiment bundles; contract authoring was gated on the
   primary-manager set and other conditions no agent ever satisfied.

One sentence: **every gate was denominated in process, and process is
satisfiable by prose.** The system was audited against an eight-plane
operating model (admission, context, execution/routing, artifact contract,
validation, authority/state, measurement, learning) with one question per
line: which plane does this serve, and is that plane denominated in value or
in procedure?

## Dispositions

### ADDED — the missing planes

| Plane | Mechanism | Where enforced |
|---|---|---|
| Task admission | `organization_admission.py`: every launch names a downstream consumer, one of six meaningful-work classes, a falsifiable done condition, and stop conditions. All defects reported in one actionable error. Continuations carry the parent contract. | Host code in `create_run_request`, before any filesystem effect. |
| Deliberate stopping | `no_op` disposition (lead-only, final, candidate-less) ending the run as `completed_no_op`, adjudicated **before** the delegation machinery so nothing can forge work for a run that just declared there is none. Counted in the consecutive-barren decline so no-op chains do not self-continue. | Reply validation + finalization pre-pass. |
| Outcome measurement | `organization_outcomes.py`: project-level append-only ledger. `run_terminal` at every terminal (including supervisor crashes), `promotion_applied`, `candidate_used`, `promotion_rejected`. A candidate is credited only when merged or explicitly consumed. `list_organization_runs` reports terminal/productive/no-op/waste runs, waste rate, and total vs wasted tokens. | Host hooks in the runner terminal path, the worker crash handler, and the approve/reject flows. |
| Honest progress accounting | Closeout progress fingerprint counts only implementation candidates; `candidate_counts` (implementation / artifact_only / identity_only) in status, result, and terminal digest. | `_closeout_progress_signature`, `_status`. |

### KEPT — the planes that were already right

- Worktree isolation, frozen bases, per-round caller-repo verification.
- Write scopes validated twice per turn; integration scope's patch-id
  allowlist (an integrator provably cannot smuggle unreviewed code).
- Evidence snapshots and context packs: hashed, read-only, re-verified.
- Blind final review by a fresh cross-provider session.
- Veto auditors; exact-candidate decision normalization.
- Human promotion staging: SHA-sealed packets, exact-hash approval, ff-only
  local merge, immutable rejection.
- The experiment loop: contracts, immutable evaluators, SHA-chained trial
  ledger, budgets, host-reverted regressions. Metering now follows the budget
  (`_metered_experiment_paths`), not a topology name.
- The control plane: append-only requests, round-boundary application.
- Provider-neutral `SubscriptionSession` (Claude Code / Codex CLI dispatch,
  streaming telemetry, redaction, no chain-of-thought archiving).

### DELETED — the sociology plane

The manager layer existed to allocate scarce attention and surface
privately-held context. Neither applies to agents, and every layer boundary
became another serialized document instead of work.

- Topologies `google`, `google-rotating`, `scientific` (~180 lines of role
  prose plus their routes). `flat` is the only structure: one coordinator
  (leader and finalizer, integration write scope), six workers, two
  independent veto auditors, blind final review, human promotion. Legacy
  topology names alias to flat so existing contracts and continuation records
  keep launching; the requested name is recorded next to the resolved one.
- The delegation barrier and degradation machinery
  (`_assert_delegation_barrier`, `_degrade_delegation`, fallback goals,
  `degraded_delegations`): five layers of compensation for a coordination
  ceremony flat does not perform.
- Rotating cross-manager review bookkeeping in `Governance` (reviewer
  rotation by lane survives only as: candidate review goes to the non-release
  auditor; the release auditor holds the final veto).
- The research cell (research-scout, math-auditor, commissions, fragments,
  decision packets).
- The off-goal-flag peer-consult protocol. Flags are now `raised` → `acted`:
  the supervisor's decision or goal rebind adjudicates directly; there is no
  peer to consult.

### REPAIRED — flat-path defects the collapse exposed

The flat promote path had never been completed by any run, and the audit
found out why. All three fixed:

1. `_sync_reviewed_candidates` required the reviewed handoff to arrive from
   the worker's primary manager; in flat the supervisor IS the finalizer, so
   host integration was unreachable. It now accepts the handoff directly from
   the worker when its supervisor is the finalizer.
2. The flat lead was read-only, but the host integrates reviewed candidates
   into the lead's integration worktree, so write-scope validation of the
   lead failed the moment integration succeeded. The lead now has integration
   scope: every commit in its worktree must carry an approved patch-id.
3. Delegation hygiene gates (`workItem`/`risk` required; goal-carrying
   traffic only from the supervisor) were gated on `manager_ids` and silently
   inert in flat. They now key on supervisor resolution, and non-supervisor
   plan/handoff traffic to a worker is dropped instead of waking it.

## The yardstick

The reference model behind this audit defines meaningful work as: a validated
deployable artifact for a named consumer, a resolved decision, a measured
reduction in uncertainty, a tested hypothesis, a prevented material risk, or
a reusable capability with a named recurring workflow. Its enforcement rule:
gates live in host code, never in prompt text agents are asked to honor —
the recorded runs are the proof that agent-facing procedure is satisfiable by
prose. Its measurement rule: value is credited on use, not on acceptance;
accepted-but-unused output is waste and is reported as such.

Deliberately NOT adopted: dollar-denominated expected-value scoring and
calibrated priors. With zero outcome history, those numbers would be
fabricated by the models trying to pass the gate, which is the metric-gaming
failure the model itself warns against. The structure of admission was
adopted; the arithmetic waits until the outcome ledger has enough history to
calibrate against.
