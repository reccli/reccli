# Autonomous experiment loop

RecCli scientific organizations can opt into a project-owned,
Karpathy-style experiment loop for a bounded implementation question. The
loop removes routine coordination turns without expanding scientific or
canonical authority.

## Hard invariants

Each campaign has:

1. one active worker and one mutable tracked file;
2. one project-declared immutable evaluator profile;
3. a host-run baseline before any challenger;
4. immutable shell-free commands and timeouts;
5. one structured hypothesis and one cohesive change per trial;
6. a compact append-only `experiment-loop/trials.jsonl` ledger;
7. host-owned retention of improvements and Git reversion of regressions; and
8. direct worker continuation without routine manager wakeups.

Only one campaign may be active in an organization at a time. A crash,
inconclusive comparison, invalid contract, plateau, exhausted budget,
cross-file need, or reviewable candidate halts the campaign and wakes the
primary manager.

## Project policy

Projects opt in by passing a tracked policy path as `experiment_policy`:

```json
{
  "schema": "reccli.organization-experiment-policy.v1",
  "enabled": true,
  "max_trials_per_contract": 3,
  "max_consecutive_non_improving": 3,
  "max_contract_wall_seconds": 3600,
  "evaluators": [
    {
      "id": "bounded-regression-v1",
      "commands": [
        {
          "argv": ["python", "-m", "pytest", "-q", "tests/test_target.py"],
          "timeout_seconds": 600
        }
      ],
      "immutable_paths": ["tests/test_target.py"],
      "mutable_roots": ["src"],
      "result_mode": "command_exit",
      "hard_gates": [],
      "metrics": []
    }
  ]
}
```

The policy and every evaluator input are automatically added to the run's
deny-write paths. `command_exit` evaluators are useful when the baseline fails
and a challenger can make the fixed checks pass. A baseline and challenger
that both pass are inconclusive, because a pass-only evaluator cannot rank
them.

For quantitative optimization, use `result_mode: "json_file"`. The evaluator
writes `RECCLI_EXPERIMENT_RESULT_PATH` with schema
`reccli.project-experiment-result.v1`, the policy's exact Boolean hard gates,
finite metric values, and optional notes. Metric definitions declare
`minimize` or `maximize` plus a tolerance. RecCli keeps a challenger only when
it passes all hard gates, improves at least one metric beyond tolerance, and
worsens none. Tradeoffs and ties are inconclusive and fail closed.

## Manager contract and worker intent

A primary manager binds one named work item, worker, mutable file, evaluator,
trial limit, non-improving stop, wall limit, and any required research decision
hash in a `reccli.organization-experiment-contract.v1` artifact. Delivery of
the matching plan activates the campaign and pins the worker's current HEAD as
the baseline.

For each challenger the worker writes one
`reccli.organization-experiment-trial.v1` intent containing its hypothesis,
single change, and expected result. RecCli validates the exact one-file diff,
materializes the commit, charges one global experiment slot, evaluates it, and
records both the challenger and resulting retained HEAD.

These records establish reproducibility and resource accounting. They do not
decide whether a scientific hypothesis, evaluator, or acceptance standard is
correct. Managers must not activate a loop when the declared evaluator cannot
adjudicate the intended claim.

## Console

The organization console renders the active contract, mutable file, evaluator,
trial cap, baseline, keep/discard decisions, gates or metrics, duration, and
halt reason from the durable ledger. Native read/search/test/edit telemetry
continues to appear in each agent's work stream.
