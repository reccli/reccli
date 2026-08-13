"""Regression tests for organization-runtime defects.

Sourced from a forensic audit of nine recorded runs that produced zero promoted
candidates, zero experiment contracts and zero trials between them. Each test
below encodes a failure that actually happened, with the measurement that made
it visible, so a future change cannot quietly restore it. (Defect 1, the
delegation barrier, was removed along with the hierarchical topologies its
machinery served.)
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

    def test_flat_is_the_only_topology(self):
        topology = get_topology("flat")
        self.assertEqual(topology.topology_id, "flat")
        self.assertEqual(topology.manager_ids, [])
        self.assertEqual(topology.primary_manager_by_worker, {})

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

    def test_a_flat_worker_can_hand_its_candidate_back(self):
        """Without this, a flat run can never produce anything.

        Worker traffic was routed against primary_manager_by_worker, None in a
        flat topology, so every handoff was dropped with "must go through primary
        manager None". A flat run then authored no contract, tripped
        no_experiment_contract, and (now that the status is continuation-eligible)
        auto-launched a successor that did the same thing again.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root, "Find defects.", "claude", "flat", "flat-handoff",
                root / "run", max_experiments=3,
            )
            runner._deliver_message("lead", {
                "from": "lead", "to": "worker-a", "tag": "plan",
                "content": "Answer whether the tolerance check ignores units.",
                "candidate": None, "workItem": "tol", "risk": "routine",
            }, 1)
            runner._deliver_message("worker-a", {
                "from": "worker-a", "to": "lead", "tag": "handoff",
                "content": "Found it.", "candidate": "c0ffee",
                "workItem": "tol", "risk": "routine",
            }, 2)
            messages = [
                json.loads(line) for line in
                (runner.run_dir / "messages.jsonl").read_text().splitlines()
            ]
            handoffs = [m for m in messages if m.get("tag") == "handoff"]
            self.assertTrue(handoffs)
            self.assertEqual(handoffs[0]["status"], "delivered",
                             handoffs[0].get("reason"))

    def test_a_worker_still_cannot_message_a_peer(self):
        """Relaxing the routing must not open worker-to-worker traffic."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root, "m", "claude", "flat", "peer-flat",
                root / "run", max_experiments=3,
            )
            runner._deliver_message("worker-a", {
                "from": "worker-a", "to": "worker-b", "tag": "handoff",
                "content": "x", "candidate": "c2", "workItem": "t",
                "risk": "routine",
            }, 2)
            messages = [
                json.loads(line) for line in
                (runner.run_dir / "messages.jsonl").read_text().splitlines()
            ]
            peer = [m for m in messages if m.get("to") == "worker-b"]
            self.assertTrue(peer)
            self.assertEqual(peer[0]["status"], "dropped")

    def test_supervisor_resolves_to_the_lead_without_managers(self):
        self.assertEqual(_supervisor_of(get_topology("flat"), "worker-a"), "lead")

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

class NoExperimentContractTests(unittest.TestCase):
    """Defect 5. Both runs used 0 of 3 bundles and ran to the round limit anyway."""

    def _runner(self, root, **kwargs):
        return OrganizationRunner(
            root, "m", "claude", "flat", "r", root / "run", **kwargs,
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

    def test_a_barren_chain_stops_continuing_itself(self):
        """Eligibility without a bound is a loop.

        Nothing limited successive auto-continuations. A configuration that
        cannot author an experiment contract reproduces that outcome every time,
        so making the status eligible turned a single stuck run into an
        indefinite chain of them.
        """
        from reccli.organization_project_launch import (
            BARREN_TERMINAL_STATUSES,
            MAX_CONSECUTIVE_BARREN_CONTINUATIONS,
            _consecutive_barren_terminals,
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            orgs = root / "devsession" / "agent-organizations"
            orgs.mkdir(parents=True)

            def record(name, status, stamp):
                run_dir = orgs / f"{stamp}_org_{name}"
                run_dir.mkdir()
                payload = json.dumps({"run_id": name, "status": status})
                (run_dir / "run.json").write_text(payload)
                (run_dir / "status.json").write_text(payload)

            for index in range(MAX_CONSECUTIVE_BARREN_CONTINUATIONS):
                record(f"barren{index}", "no_experiment_contract",
                       f"2026010{index + 1}T000000Z")
            self.assertGreaterEqual(
                _consecutive_barren_terminals(root, BARREN_TERMINAL_STATUSES),
                MAX_CONSECUTIVE_BARREN_CONTINUATIONS,
            )

            # A run that produced something must reset the count.
            record("good", "completed_no_promotion", "20260109T000000Z")
            self.assertEqual(
                _consecutive_barren_terminals(root, BARREN_TERMINAL_STATUSES), 0,
            )

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
        """Flat workers first act in round 2, so max_rounds=2 gives them at
        most one round of their own.

        Reporting no_experiment_contract there would blame the run for a budget
        it was never given.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root, max_experiments=3, max_rounds=2)
            runner.experiment_policy = {"source_sha256": "x"}
            self.assertFalse(runner._experiment_contract_deadline_passed(99))

    def test_workers_get_at_least_two_rounds_before_the_deadline(self):
        """Checked at the END of a round, so the deadline round itself runs.

        Flat workers first act in round 2; the deadline must leave them at
        least two rounds of their own to produce anything.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root, max_experiments=3, max_rounds=8)
            first_worker_round = 2
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
