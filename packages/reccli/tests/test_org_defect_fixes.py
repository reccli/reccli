"""Regression tests for five organization-runtime defects.

Sourced from a forensic audit of nine recorded runs that produced zero promoted
candidates, zero experiment contracts and zero trials between them. Each test
below encodes a failure that actually happened, with the measurement that made
it visible, so a future change cannot quietly restore it.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from reccli.organization import (  # noqa: E402
    Governance,
    OrganizationRunner,
    get_topology,
    prepare_context_packs,
    verify_context_packs,
    _supervisor_of,
)
from reccli.organization_control import TERMINAL_STATUSES  # noqa: E402
from reccli.organization_project_launch import (  # noqa: E402
    _continuation_mission,
    _scrub_rejected_candidate,
)
from test_organization import _init_project  # noqa: E402


class DelegationDegradationTests(unittest.TestCase):
    """Defect 1. A recorded run died at round 2 with no worker ever executing.

    Two managers had failed earlier in that round, so no worker assignment
    survived, and the barrier raised RuntimeError and took the run with it. The
    coordination mechanism, not the work, was the failure.
    """

    def _runner(self, root, topology="google-rotating"):
        return OrganizationRunner(
            root, "Advance the mission.", "claude", topology,
            "degrade-run", root / "run", max_experiments=3,
        )

    def test_missing_worker_assignments_no_longer_end_the_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root)
            runner._assert_delegation_barrier(2)  # must not raise
            self.assertEqual(
                {item["agent_id"] for item in runner.degraded_delegations},
                set(runner.topology.worker_ids),
            )

    def test_degraded_workers_are_actually_scheduled(self):
        """Not aborting is worthless if the workers still never run."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root)
            runner._assert_delegation_barrier(2)
            scheduled = {agent.agent_id for agent in runner._select_agents(3)}
            self.assertTrue(
                set(runner.topology.worker_ids) <= scheduled,
                "every degraded worker must be woken, not merely unblocked",
            )

    def test_degraded_workers_receive_a_goal(self):
        """Worker instructions say to solve the one goal RecCli shows them.

        Scheduling a worker with no bound goal wakes it with nothing to do.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root)
            runner._assert_delegation_barrier(2)
            for worker_id in runner.topology.worker_ids:
                goal = runner.worker_goals.get(worker_id)
                self.assertIsNotNone(goal, f"{worker_id} has no goal")
                self.assertEqual(goal.get("source"), "lead-fallback")

    def test_degradation_is_recorded_not_silent(self):
        """A run that only progressed via fallbacks must not read as healthy."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root)
            runner._assert_delegation_barrier(1)
            self.assertTrue(runner.degraded_delegations)
            for item in runner.degraded_delegations:
                self.assertIn("round", item)
                self.assertIn("level", item)

    def test_barrier_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root)
            runner._assert_delegation_barrier(2)
            first = len(runner.degraded_delegations)
            runner._assert_delegation_barrier(2)
            self.assertEqual(len(runner.degraded_delegations), first)

    def test_no_worker_is_ever_scheduled_without_a_goal(self):
        """The failure the first fix actually caused.

        Binding with no predicate selectors is unevaluable on any project
        declaring more than one predicate, so every fallback was rejected and
        every worker was still woken, told "do not perform substantive work",
        and had each message it emitted dropped.
        """
        policy = {
            "promotion_requires_goal_progress": True, "source_sha256": "x",
            "evaluators": {"eval-v1": {
                "id": "eval-v1", "profile_sha256": "a" * 64,
                "immutable_ground_truth_sha256": "b" * 64,
                "predicates": {
                    f"p{i}": {"id": f"pred-{i}-v1",
                              "goal_class": "production_pipeline",
                              "comparison_rule_id": "gte"}
                    for i in range(6)
                }}},
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root)
            runner.experiment_policy = policy
            runner._assert_delegation_barrier(2)
            scheduled = [
                agent.agent_id for agent in runner._select_agents(3)
                if agent.agent_id in runner.topology.worker_ids
            ]
            for worker_id in scheduled:
                self.assertTrue(
                    runner._goal_is_active(runner.worker_goals.get(worker_id)),
                    f"{worker_id} was scheduled with no active goal",
                )

    def test_recorded_degradations_match_workers_that_got_work(self):
        """The terminal record must not overstate what happened."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root)
            runner._assert_delegation_barrier(2)
            recorded = {
                item["agent_id"] for item in runner.degraded_delegations
                if item["agent_id"] in runner.topology.worker_ids
            }
            with_goals = {
                worker_id for worker_id in runner.topology.worker_ids
                if runner._goal_is_active(runner.worker_goals.get(worker_id))
            }
            self.assertEqual(recorded, with_goals)

    def test_each_degraded_worker_gets_a_distinct_work_item(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root)
            runner._assert_delegation_barrier(2)
            items = [
                runner.worker_goals[worker_id]["work_item"]
                for worker_id in runner.topology.worker_ids
                if runner.worker_goals.get(worker_id)
            ]
            self.assertEqual(len(items), len(set(items)))

    def test_the_barrier_never_raises_even_when_binding_explodes(self):
        """The barrier exists to stop one failure ending the run."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root)

            def boom(**kwargs):
                raise RuntimeError("evaluator subprocess exploded")

            runner._bind_worker_goal = boom
            runner._assert_delegation_barrier(2)  # must not raise

    def test_a_manager_can_reclaim_a_degraded_worker(self):
        """The fallback goal must be provisional, not equal to a real one.

        Binding it with force=True made it indistinguishable from a manager
        assignment, so the manager's later correct delegation was refused for the
        rest of the run and the worker stayed on a generic fallback objective.
        The barrier exists to stop the coordination layer killing a run; it must
        not stop the coordination layer recovering.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root)
            runner._assert_delegation_barrier(2)
            self.assertEqual(
                runner.worker_goals["worker-a"]["source"], "lead-fallback",
            )
            accepted, reason = runner._bind_worker_goal(
                worker_id="worker-a", manager_id="manager-a",
                work_item="real/worker-a/gate",
                objective="Fix the tolerance comparison and pass its focused test.",
                risk="routine", round_number=3,
            )
            self.assertTrue(accepted, reason)
            self.assertEqual(
                runner.worker_goals["worker-a"]["work_item"], "real/worker-a/gate",
            )

    def test_a_raising_bind_is_not_reported_as_success(self):
        """Goals are recorded before their baseline is captured.

        Reading the resulting state as success turned a goal with no baseline
        into a reported delegation, and every candidate it produced then died on
        "goal baseline is missing".
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root)

            def bind_then_raise(**kwargs):
                runner.worker_goals[kwargs["worker_id"]] = {
                    "work_item": kwargs["work_item"], "status": "active",
                    "source": kwargs["source"],
                }
                raise RuntimeError("evaluator could not execute")

            runner._bind_worker_goal = bind_then_raise
            runner._assert_delegation_barrier(2)

            self.assertEqual(runner.degraded_delegations, [])
            self.assertEqual(
                [w for w in runner.topology.worker_ids
                 if runner.worker_goals.get(w)], [],
                "a partially bound goal must not survive",
            )
            self.assertEqual(
                [a.agent_id for a in runner._select_agents(3)
                 if a.agent_id in runner.topology.worker_ids], [],
            )

    def test_a_properly_delegated_round_records_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root, topology="scientific")
            for manager_id in ("manager-a", "manager-b"):
                runner.inboxes[manager_id] = [{
                    "from": "lead", "to": manager_id, "tag": "plan",
                    "content": "Refine the lane.", "candidate": None,
                    "workItem": f"map-{manager_id}", "risk": "routine",
                }]
            runner._assert_delegation_barrier(1)
            self.assertEqual(runner.degraded_delegations, [])


class RejectedCandidateSeedingTests(unittest.TestCase):
    """Defect 4. Two successors spent every turn re-adjudicating a dead artifact.

    The recorded operator decision said the candidate "must not seed or satisfy
    a successor mission". A prose warning was appended to the successor mission,
    but the parent conclusion was still handed over intact.
    """

    CANDIDATE = "b8e2934ab9ca2e18d578a088c0fdb23fc8f89a8f"

    def _decision(self, decision="rejected"):
        return {"decision": decision, "candidate": self.CANDIDATE}

    def _view(self):
        return {
            "summary": f"Adjudicated {self.CANDIDATE} across twelve rounds.",
            "accomplishments": [f"Review dossier for {self.CANDIDATE}"],
            "conclusive_findings": ["The evaluator metric sits at its floor"],
            "unresolved": [
                f"Whether {self.CANDIDATE[:12]} should be repackaged",
                "Whether the metric has headroom",
            ],
            "next_action": f"Complete the review of {self.CANDIDATE}.",
            "limitations": ["No geometry touched"],
        }

    def test_next_action_no_longer_points_at_the_rejected_candidate(self):
        scrubbed = _scrub_rejected_candidate(self._view(), self._decision())
        self.assertNotIn(self.CANDIDATE, scrubbed["next_action"])
        self.assertIn("Superseded", scrubbed["next_action"])

    def test_no_candidate_identifier_survives_anywhere(self):
        scrubbed = _scrub_rejected_candidate(self._view(), self._decision())
        blob = json.dumps(scrubbed)
        self.assertNotIn(self.CANDIDATE, blob)
        self.assertNotIn(self.CANDIDATE[:12], blob)

    def test_what_was_learned_is_preserved(self):
        """Carrying forward the lesson is fine; carrying the artifact is not."""
        scrubbed = _scrub_rejected_candidate(self._view(), self._decision())
        self.assertEqual(
            scrubbed["conclusive_findings"],
            ["The evaluator metric sits at its floor"],
        )
        self.assertEqual(scrubbed["limitations"], ["No geometry touched"])
        self.assertIn("Whether the metric has headroom", scrubbed["unresolved"])

    def test_accomplishments_are_dropped(self):
        scrubbed = _scrub_rejected_candidate(self._view(), self._decision())
        self.assertEqual(scrubbed["accomplishments"], [])

    def test_a_rejection_survives_a_generation_with_no_decision_of_its_own(self):
        """A rejection is permanent, but the scrub only saw the latest decision.

        That made it last exactly one generation. It matters more now that
        no_experiment_contract is continuation-eligible: an auto-terminated run
        is never adjudicated by a human, so it has no decision file, and its
        successor would have inherited the dead artifact again.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "s.txt").write_text("x")
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
            subprocess.run(
                ["git", "-C", str(root), "-c", "user.email=t@t", "-c",
                 "user.name=t", "commit", "-qm", "c"], check=True,
            )
            gen1 = root / "devsession" / "agent-organizations" / "20260101T000000Z_org_a"
            gen1.mkdir(parents=True)
            (gen1 / "operator-decision.json").write_text(json.dumps({
                "decision": "rejected", "candidate": self.CANDIDATE,
            }))
            (gen1 / "run.json").write_text(json.dumps({"run_id": "gen1"}))

            # gen-2 auto-terminated: no operator decision of its own.
            terminal = {
                "run_id": "gen2", "status": "no_experiment_contract",
                "conclusion_sha256": "0" * 64,
                "conclusion": {
                    "summary": f"Continued work on {self.CANDIDATE}.",
                    "accomplishments": [f"Re-reviewed {self.CANDIDATE}"],
                    "next_action": f"Finish reviewing {self.CANDIDATE}.",
                    "promotion_readiness": "not_ready",
                },
            }
            mission = _continuation_mission(root, terminal, {"mission_id": "m"})
            carried = mission.split("## Parent terminal conclusion", 1)[1]
            carried = carried.split("```", 2)[1]
            self.assertNotIn(self.CANDIDATE, carried)
            self.assertIn("Superseded", carried)

    def test_a_non_rejected_decision_changes_nothing(self):
        view = self._view()
        self.assertEqual(
            _scrub_rejected_candidate(view, self._decision("approved")), view,
        )
        self.assertEqual(_scrub_rejected_candidate(view, None), view)

    def test_the_scrub_is_actually_applied_to_the_successor_mission(self):
        """The function being correct is worthless if nothing calls it.

        Testing the scrub in isolation passed even with its call site deleted,
        so this exercises the mission builder end to end instead.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "seed.txt").write_text("x")
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
            subprocess.run(
                ["git", "-C", str(root), "-c", "user.email=t@t", "-c",
                 "user.name=t", "commit", "-qm", "c"], check=True,
            )
            terminal = {
                "run_id": "parent-run",
                "status": "round_limit",
                "conclusion_sha256": "0" * 64,
                "conclusion": {
                    "summary": f"Adjudicated {self.CANDIDATE}.",
                    "accomplishments": [f"Dossier for {self.CANDIDATE}"],
                    "next_action": f"Finish reviewing {self.CANDIDATE}.",
                    "promotion_readiness": "not_ready",
                },
                "operator_decision": self._decision(),
            }
            mission = _continuation_mission(root, terminal, {"mission_id": "m"})

            # The rejection notice names the dead candidate on purpose, so the
            # successor knows which artifact is off limits. What must be clean is
            # the carried conclusion: that is the part read as work to continue.
            carried = mission.split("## Parent terminal conclusion", 1)[1]
            carried = carried.split("```", 2)[1]
            self.assertNotIn(self.CANDIDATE, carried)
            self.assertIn("Superseded", carried)
            self.assertIn("Binding human rejection", mission)


class FlatTopologyTests(unittest.TestCase):
    """Defect 2. Management took 50 of 64 turns; one worker took a single turn."""

    def test_flat_is_a_first_class_topology(self):
        topology = get_topology("flat")
        self.assertEqual(topology.topology_id, "flat")
        self.assertEqual(topology.manager_ids, [])
        self.assertEqual(topology.primary_manager_by_worker, {})
        self.assertFalse(topology.delegation_gate)

    def test_flat_has_independent_auditors_that_cannot_promote(self):
        topology = get_topology("flat")
        self.assertTrue(topology.final_reviewer_pool)
        self.assertEqual(topology.review_policy, "veto")
        self.assertTrue(topology.human_promotion_required)

    def test_workers_outnumber_coordination(self):
        topology = get_topology("flat")
        coordination = {topology.leader_id}
        self.assertGreater(len(topology.worker_ids), 2 * len(coordination))

    def test_workers_execute_from_round_two(self):
        """Hierarchical spends round 2 on managers; flat must not waste it."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root, "Find defects.", "claude", "flat", "flat-run",
                root / "run", max_experiments=3,
            )
            for worker_id in ("worker-a", "worker-b"):
                runner.inboxes[worker_id].append({
                    "runId": runner.run_id, "round": 1, "from": "lead",
                    "to": worker_id, "tag": "plan", "content": "A question.",
                    "candidate": None, "workItem": f"q-{worker_id}",
                    "risk": "routine", "deliveredAt": "x",
                })
            scheduled = {agent.agent_id for agent in runner._select_agents(2)}
            self.assertEqual(scheduled, {"worker-a", "worker-b"})

    def test_supervisor_resolves_to_the_lead_without_managers(self):
        self.assertEqual(_supervisor_of(get_topology("flat"), "worker-a"), "lead")
        self.assertEqual(
            _supervisor_of(get_topology("scientific"), "worker-a"), "manager-a",
        )

    def test_flat_handoffs_route_to_an_auditor(self):
        """Auditors exist to review; without routing they would never work."""
        governance = Governance(get_topology("flat"), "run")
        accepted, reason, _ = governance.process_message("worker-a", {
            "from": "worker-a", "to": "lead", "tag": "handoff",
            "content": "done", "candidate": "cand1",
            "workItem": "q1", "risk": "routine",
        }, 2)
        self.assertTrue(accepted, reason)
        assignment = governance.assignments.get("cand1")
        self.assertIsNotNone(assignment)
        self.assertIn(
            assignment["reviewerId"], get_topology("flat").final_reviewer_pool,
        )

    def test_flat_still_validates_handoffs(self):
        """The old manager-only guard skipped validation entirely for flat."""
        governance = Governance(get_topology("flat"), "run")
        accepted, reason, _ = governance.process_message("worker-a", {
            "from": "worker-a", "to": "worker-b", "tag": "handoff",
            "content": "x", "candidate": "c", "workItem": "q", "risk": "routine",
        }, 2)
        self.assertFalse(accepted)
        self.assertIn("lead", reason)

    def test_hierarchical_topologies_are_unchanged(self):
        for name in ("google-rotating", "scientific"):
            topology = get_topology(name)
            self.assertTrue(topology.manager_ids, name)
            self.assertTrue(topology.delegation_gate, name)


class NoExperimentContractTests(unittest.TestCase):
    """Defect 5. Both runs used 0 of 3 bundles and ran to the round limit anyway."""

    def _runner(self, root, **kwargs):
        return OrganizationRunner(
            root, "m", "claude", "scientific", "r", root / "run", **kwargs,
        )

    def test_status_is_terminal_and_distinct(self):
        self.assertIn("no_experiment_contract", TERMINAL_STATUSES)
        self.assertNotEqual("no_experiment_contract", "round_limit")

    def test_an_explicit_contract_is_not_bricked_by_the_new_status(self):
        """Adding it to the defaults was not enough.

        The defaults are consulted only when eligible_statuses is omitted, and an
        ineligible latest status makes the launch RAISE rather than skip, so
        every project declaring its own list stayed unable to launch.
        """
        from reccli.organization_project_launch import (
            _validated_continuation_policy,
        )
        policy = _validated_continuation_policy({"continuation_policy": {
            "mode": "latest-terminal-conclusion",
            "eligible_statuses": ["completed_no_promotion", "round_limit", "stalled"],
            "eligible_promotion_readiness": ["not_ready", "no_candidate"],
        }})
        self.assertIn("no_experiment_contract", policy["eligible_statuses"])

    def test_trips_only_after_the_deadline(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root, max_experiments=3, max_rounds=8)
            runner.experiment_policy = {"source_sha256": "x"}
            deadline = runner._experiment_contract_deadline
            self.assertFalse(runner._experiment_contract_deadline_passed(deadline - 1))
            self.assertTrue(runner._experiment_contract_deadline_passed(deadline))

    def test_an_authored_contract_prevents_termination(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root, max_experiments=3, max_rounds=8)
            runner.experiment_policy = {"source_sha256": "x"}
            runner.experiment_contracts["c1"] = {"id": "c1"}
            self.assertFalse(runner._experiment_contract_deadline_passed(99))

    def test_a_run_with_no_experiment_loop_never_trips(self):
        """A mission without experiments is not failing by not using them."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root, max_experiments=3, max_rounds=8)
            self.assertIsNone(runner.experiment_policy)
            self.assertFalse(runner._experiment_contract_deadline_passed(99))

    def test_a_zero_budget_run_never_trips(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root, max_experiments=0, max_rounds=8)
            runner.experiment_policy = {"source_sha256": "x"}
            self.assertFalse(runner._experiment_contract_deadline_passed(99))

    def test_a_run_too_short_for_workers_never_trips(self):
        """max_rounds=2 never reaches round 3, when gated workers first act.

        Reporting no_experiment_contract there would blame the run for a budget
        it was never given.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root, max_experiments=3, max_rounds=2)
            runner.experiment_policy = {"source_sha256": "x"}
            self.assertTrue(runner.topology.delegation_gate)
            self.assertFalse(runner._experiment_contract_deadline_passed(99))

    def test_workers_get_at_least_two_rounds_before_the_deadline(self):
        """Checked at the END of a round, so the deadline round itself runs.

        Gated workers first act in round 3; a deadline of 4 evaluated at the top
        of the round gave them exactly one round to produce anything.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root, max_experiments=3, max_rounds=8)
            first_worker_round = 3 if runner.topology.delegation_gate else 2
            self.assertGreaterEqual(
                runner._experiment_contract_deadline - first_worker_round + 1, 2,
            )

    def test_the_new_status_does_not_block_future_launches(self):
        """An ineligible latest status makes the launch path RAISE, not skip.

        Excluding this status from the defaults did not merely stop a successor
        auto-launching; it stopped the project launching at all.
        """
        from reccli.organization_project_launch import (
            DEFAULT_CONTINUATION_STATUSES,
        )
        self.assertIn("no_experiment_contract", DEFAULT_CONTINUATION_STATUSES)


class ContextPackDeduplicationTests(unittest.TestCase):
    """Defect 3. 24,010,389 bytes across 11 packs for 3,027,496 unique bytes."""

    def _project(self, root):
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "research").mkdir()
        for i in range(12):
            (root / "research" / f"r{i:02d}.txt").write_text("x" * 8192)
        agents = ["lead", *[f"worker-{c}" for c in "abcdef"],
                  "auditor-a", "auditor-b"]
        (root / "packs.json").write_text(json.dumps({
            "schema": "reccli.organization-context-packs.v1",
            "description": "t",
            "common": {"purpose": "shared corpus", "paths": ["research"]},
            "agents": {a: {"purpose": f"lane {a}", "paths": ["research"]}
                       for a in agents},
            "full_context_agents": ["lead"],
        }))
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=t@t", "-c",
             "user.name=t", "commit", "-qm", "c"], check=True,
        )

    def test_shared_files_do_not_cost_a_copy_per_role(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            self._project(root)
            run = root / "run"
            run.mkdir()
            prepare_context_packs(root, run, "packs.json", get_topology("flat"))

            files = [p for p in (run / "context-packs").rglob("*") if p.is_file()]
            logical = sum(p.stat().st_size for p in files)
            by_inode = {p.stat().st_ino: p.stat().st_size for p in files}
            self.assertGreater(len(files), len(by_inode),
                               "expected shared inodes across packs")
            self.assertLess(
                sum(by_inode.values()), logical / 2,
                "deduplication must remove most of the byte cost",
            )

    def test_packs_still_verify_and_still_detect_tampering(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            self._project(root)
            run = root / "run"
            run.mkdir()
            manifest = prepare_context_packs(
                root, run, "packs.json", get_topology("flat"),
            )
            verify_context_packs(manifest, full=True)

            victim = next(
                p for p in (run / "context-packs").rglob("*.txt") if p.is_file()
            )
            victim.chmod(0o644)
            victim.write_text("tampered")
            with self.assertRaises(Exception):
                verify_context_packs(manifest, full=True)

    def test_every_role_still_sees_its_own_file_set(self):
        """Dedup must not merge packs: roles receive different sets."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            self._project(root)
            run = root / "run"
            run.mkdir()
            manifest = prepare_context_packs(
                root, run, "packs.json", get_topology("flat"),
            )
            packs = manifest["agent_packs"]
            self.assertIn("lead", packs)
            for pack in packs.values():
                self.assertTrue(Path(pack["root"]).is_dir())
                self.assertTrue(pack["files"])


if __name__ == "__main__":
    unittest.main()
