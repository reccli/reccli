import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reccli.organization import OrganizationRunner, get_topology
from reccli.organization_console_bridge import dispatch
from reccli.organization_control import (
    acknowledge_control_request,
    approve_organization_request,
    list_organization_runs,
    organization_snapshot,
    pending_control_requests,
    process_group_activity,
    queue_control_request,
    reject_organization_candidate,
)


def _init_project(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=root,
        check=True,
    )
    (root / "app.py").write_text("print('test')\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)


def _make_run(root: Path, run_id: str = "control-run", protocol: bool = True) -> Path:
    run_dir = root / "devsession" / "agent-organizations" / run_id
    run_dir.mkdir(parents=True)
    topology = get_topology("scientific")
    assignments = {
        agent.agent_id: ("claude" if index % 2 else "codex")
        for index, agent in enumerate(topology.agents)
    }
    run = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "project_root": str(root),
        "mission": "Qualify the system.",
        "provider": "mixed",
        "host_provider": "codex",
        "provider_assignments": assignments,
        "topology": "scientific",
        "max_rounds": 8,
        "human_promotion_required": True,
    }
    if protocol:
        run["control_protocol"] = "reccli.organization-control.v1"
    (run_dir / "request.json").write_text(
        json.dumps(run, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "run.json").write_text(
        json.dumps(run, indent=2) + "\n",
        encoding="utf-8",
    )
    status = {
        "run_id": run_id,
        "status": "running",
        "round": 2,
        "max_rounds": 8,
        "completed_turns": 4,
        "attempted_turns": 4,
        "provider": "mixed",
        "topology": "scientific",
        "agent_states": {"worker-a": "working"},
    }
    if protocol:
        status["control_protocol"] = "reccli.organization-control.v1"
    (run_dir / "status.json").write_text(
        json.dumps(status, indent=2) + "\n",
        encoding="utf-8",
    )
    return run_dir


class OrganizationCliBootstrapTests(unittest.TestCase):
    def test_organization_cli_runs_without_optional_python_dependencies(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _make_run(root)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
            completed = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    "-m",
                    "reccli.cli_bootstrap",
                    "organization",
                    "list",
                    "--project-root",
                    str(root),
                    "--json",
                ],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["runs"][0]["run_id"], "control-run")

            steering = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    "-m",
                    "reccli.cli_bootstrap",
                    "organization",
                    "message",
                    "control-run",
                    "Recheck the bounded acceptance evidence.",
                    "--target",
                    "auditor-a",
                    "--tag",
                    "review",
                    "--idempotency-key",
                    "cli-bootstrap-steering",
                    "--project-root",
                    str(root),
                ],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(steering.returncode, 0, steering.stderr)
            queued = json.loads(steering.stdout)
            self.assertEqual(queued["status"], "queued")
            request_path = (
                Path(queued["run_dir"])
                / "control"
                / "requests"
                / f"{queued['id']}.json"
            )
            request = json.loads(request_path.read_text(encoding="utf-8"))
            self.assertEqual(request["target"], "auditor-a")
            self.assertEqual(request["tag"], "review")


class OrganizationControlTests(unittest.TestCase):
    def test_terminal_candidate_rejection_is_durable_and_disables_approval(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = _make_run(root, run_id="rejection-run")
            candidate = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            status_path = run_dir / "status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["status"] = "round_limit"
            status_path.write_text(
                json.dumps(status, indent=2) + "\n",
                encoding="utf-8",
            )
            conclusion = {
                "schema": "reccli.organization-run-conclusion.v1",
                "run_id": "rejection-run",
                "terminal_status": "round_limit",
                "candidates": [{
                    "candidate": candidate,
                    "kind": "implementation",
                    "paths": ["app.py"],
                }],
            }
            (run_dir / "run-conclusion.json").write_text(
                json.dumps(conclusion, indent=2) + "\n",
                encoding="utf-8",
            )

            rejected = reject_organization_candidate(
                str(root),
                "rejection-run",
                candidate=candidate,
                reason="No evaluator-measured progress on the stated goal.",
                idempotency_key="reject-once",
                requested_by="test-human",
            )
            self.assertEqual(rejected["status"], "rejected")
            self.assertFalse(rejected["canonical_effects_applied"])
            replay = reject_organization_candidate(
                str(root),
                "rejection-run",
                candidate=candidate,
                reason="No evaluator-measured progress on the stated goal.",
                idempotency_key="reject-once",
                requested_by="test-human",
            )
            self.assertTrue(replay["idempotent_replay"])
            snapshot = organization_snapshot(str(root), "rejection-run")
            self.assertEqual(
                snapshot["operator_decision"]["decision"],
                "rejected",
            )
            self.assertFalse(snapshot["approval_capabilities"]["approve"])
            self.assertFalse(snapshot["approval_capabilities"]["reject"])
            from reccli.organization_outcomes import outcome_ledger_path

            events = [
                json.loads(line)
                for line in outcome_ledger_path(root).read_text(
                    encoding="utf-8",
                ).splitlines()
            ]
            rejections = [
                event for event in events
                if event["event"] == "promotion_rejected"
            ]
            self.assertEqual(
                len(rejections), 1,
                "idempotent replay must not double-log the rejection",
            )
            self.assertEqual(rejections[0]["candidate"], candidate)

    def test_verified_promotion_approval_fast_forwards_only_local_branch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            _init_project(root)
            run_dir = _make_run(root, run_id="promotion-approval")
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            candidate_worktree = Path(td) / "candidate"
            subprocess.run(
                [
                    "git", "worktree", "add", "-q", "-b",
                    "reccli-test-proposal", str(candidate_worktree), base,
                ],
                cwd=root,
                check=True,
            )
            (candidate_worktree / "app.py").write_text(
                "print('approved')\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "add", "app.py"],
                cwd=candidate_worktree,
                check=True,
            )
            subprocess.run(
                [
                    "git", "-c", "user.name=Test",
                    "-c", "user.email=test@example.com",
                    "commit", "-qm", "verified candidate",
                ],
                cwd=candidate_worktree,
                check=True,
            )
            candidate = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=candidate_worktree,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            status_path = run_dir / "status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["status"] = "completed"
            status_path.write_text(
                json.dumps(status, indent=2) + "\n",
                encoding="utf-8",
            )
            request = {
                "schema": "reccli.organization-approval-request.v1",
                "version": 1,
                "created_at": "2026-07-18T00:00:00Z",
                "run_id": "promotion-approval",
                "request_kind": "candidate_promotion",
                "title": "Verified candidate",
                "question": "Apply it locally?",
                "status": "awaiting_human_authorization",
                "canonical_effects_applied": False,
                "base_commit": base,
                "verified_candidate": candidate,
                "proposed_promotion_candidate": candidate,
                "proposed_promotion_branch": "reccli-test-proposal",
                "changed_paths": ["app.py"],
                "action": {
                    "type": "fast_forward_local",
                    "remote_push": False,
                },
                "authorization_required_for": ["local fast-forward"],
            }
            canonical = json.dumps(
                request,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            request["request_sha256"] = hashlib.sha256(canonical).hexdigest()
            (run_dir / "promotion-request.json").write_text(
                json.dumps(request, indent=2) + "\n",
                encoding="utf-8",
            )

            approved = approve_organization_request(
                str(root),
                "promotion-approval",
                request_sha256=request["request_sha256"],
                idempotency_key="promotion-click-1",
                requested_by="test-human",
            )

            self.assertEqual(approved["status"], "applied")
            self.assertEqual(approved["action"], "fast_forward_local")
            self.assertFalse(approved["remote_push"])
            self.assertEqual(approved["applied_commit"], candidate)
            self.assertEqual(
                subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip(),
                candidate,
            )
            from reccli.organization_outcomes import outcome_ledger_path

            events = [
                json.loads(line)
                for line in outcome_ledger_path(root).read_text(
                    encoding="utf-8",
                ).splitlines()
            ]
            applied = [
                event for event in events
                if event["event"] == "promotion_applied"
            ]
            self.assertEqual(len(applied), 1)
            self.assertEqual(applied[0]["candidate"], candidate)
            self.assertEqual(applied[0]["run_id"], "promotion-approval")

    def test_terminal_approval_starts_fresh_successor_from_exact_packet(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = _make_run(root, run_id="approval-run")
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            status_path = run_dir / "status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["status"] = "completed_pending_human"
            status_path.write_text(
                json.dumps(status, indent=2) + "\n",
                encoding="utf-8",
            )
            request = {
                "schema": "reccli.organization-approval-request.v1",
                "version": 1,
                "created_at": "2026-07-18T00:00:00Z",
                "run_id": "approval-run",
                "request_kind": "checkpoint_continuation",
                "title": "Checkpoint approval",
                "question": "Approve this exact checkpoint?",
                "status": "awaiting_human_authorization",
                "canonical_effects_applied": False,
                "base_commit": head,
                "report_candidate": head,
                "action": {
                    "type": "start_successor",
                    "remote_push": False,
                },
                "continuation": {
                    "provider": "claude",
                    "topology": "scientific",
                    "max_rounds": 8,
                    "max_concurrency": 5,
                    "turn_timeout_seconds": 1200,
                    "model": "auto",
                    "evidence_paths": [],
                    "protected_paths": [],
                    "context_manifest": None,
                    "max_experiments": 3,
                },
                "original_mission": "Qualify the system.",
                "authorization_limits": ["Exact request only."],
            }
            canonical = json.dumps(
                request,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            request["request_sha256"] = hashlib.sha256(canonical).hexdigest()
            (run_dir / "approval-request.json").write_text(
                json.dumps(request, indent=2) + "\n",
                encoding="utf-8",
            )
            staged = organization_snapshot(str(root), "approval-run")
            self.assertTrue(staged["approval_capabilities"]["approve"])
            self.assertEqual(
                staged["approval_request"]["request_sha256"],
                request["request_sha256"],
            )
            successor_dir = (
                root / "devsession" / "agent-organizations" / "successor"
            )
            captured = {}

            def fake_create_run_request(**kwargs):
                captured.update(kwargs)
                successor_dir.mkdir(parents=True)
                result = {
                    "run_id": "successor",
                    "run_dir": str(successor_dir),
                    "project_root": str(root),
                    "created_at": "2026-07-18T00:01:00Z",
                }
                (successor_dir / "status.json").write_text(
                    json.dumps({"status": "starting"}) + "\n",
                    encoding="utf-8",
                )
                return result

            with (
                patch(
                    "reccli.organization.create_run_request",
                    side_effect=fake_create_run_request,
                ),
                patch(
                    "reccli.organization_launch.launch_organization_worker",
                    return_value={"pid": 4321},
                ),
            ):
                approved = approve_organization_request(
                    str(root),
                    "approval-run",
                    request_sha256=request["request_sha256"],
                    idempotency_key="approval-click-1",
                    requested_by="test-human",
                )

            self.assertEqual(approved["status"], "applied")
            self.assertEqual(approved["action"], "start_successor")
            self.assertEqual(approved["successor_run_id"], "successor")
            self.assertIn(
                str((run_dir / "approval" / "decision.json").resolve()),
                captured["evidence_paths"],
            )
            self.assertIn("Human-approved continuation", captured["mission"])
            decision = json.loads(
                (run_dir / "approval" / "decision.json").read_text(
                    encoding="utf-8",
                ),
            )
            self.assertEqual(decision["decision"], "approved")
            self.assertEqual(
                decision["request_sha256"],
                request["request_sha256"],
            )
            successor_request = json.loads(
                (successor_dir / "request.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(successor_request["parent_run_id"], "approval-run")

    def test_approval_applies_a_staged_gate_proposal_before_the_successor(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = _make_run(root, run_id="gate-run")
            staging = root / ".reccli-org-artifacts" / "gate-run" / "gate-proposal"
            (staging / "files").mkdir(parents=True)
            (staging / "files" / "predicate.json").write_text(
                '{"id": "shell-detection-v1", "tolerance": 0.001}\n',
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "add", ".reccli-org-artifacts"], cwd=root, check=True,
            )
            subprocess.run(
                ["git", "commit", "-qm", "stage gate proposal"],
                cwd=root, check=True,
            )
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            status_path = run_dir / "status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["status"] = "completed_pending_human"
            status_path.write_text(
                json.dumps(status, indent=2) + "\n", encoding="utf-8",
            )
            request = {
                "schema": "reccli.organization-approval-request.v1",
                "version": 1,
                "created_at": "2026-08-14T00:00:00Z",
                "run_id": "gate-run",
                "request_kind": "checkpoint_continuation",
                "title": "Gate ratification",
                "question": "Ratify the proposed gate and continue?",
                "status": "awaiting_human_authorization",
                "canonical_effects_applied": False,
                "base_commit": head,
                "report_candidate": head,
                "gate_proposal": {
                    "schema": "reccli.organization-gate-proposal.v1",
                    "predicate_id": "shell-detection-v1",
                    "evaluator_id": "geometry-eval-v1",
                    "rationale": "Real-scan shells need a declared gate.",
                    "baseline_command": "score.py",
                    "measured_baseline": 0.42,
                    "proposed_tolerance": 0.001,
                    "files": [{
                        "path": (
                            ".reccli-org-artifacts/gate-run/gate-proposal/"
                            "files/predicate.json"
                        ),
                        "target": "benchmarks/gates/shell-detection-v1.json",
                    }],
                    "manifest_path": (
                        ".reccli-org-artifacts/gate-run/gate-proposal/"
                        "gate-proposal.json"
                    ),
                },
                "successor_admission": {
                    "consumer": {
                        "name": "will", "type": "human",
                        "intended_use": (
                            "review and merge the envelope-coverage promotion"
                        ),
                    },
                    "work_class": "deployable_artifact",
                    "done_condition": (
                        "the ratified envelope-coverage predicate passes from "
                        "a clean checkout"
                    ),
                    "stop_conditions": [
                        "no improvement over baseline after two contracts",
                    ],
                },
                "action": {"type": "start_successor", "remote_push": False},
                "continuation": {
                    "provider": "claude",
                    "topology": "flat",
                    "max_rounds": 6,
                    "max_concurrency": 5,
                    "turn_timeout_seconds": 2400,
                    "model": "auto",
                    "evidence_paths": [],
                    "protected_paths": [],
                    "context_manifest": None,
                    "max_experiments": 2,
                },
                "original_mission": "Author and close the next gate.",
                "authorization_limits": ["Exact request only."],
            }
            canonical = json.dumps(
                request, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            request["request_sha256"] = hashlib.sha256(canonical).hexdigest()
            (run_dir / "approval-request.json").write_text(
                json.dumps(request, indent=2) + "\n", encoding="utf-8",
            )
            successor_dir = (
                root / "devsession" / "agent-organizations" / "gate-successor"
            )
            captured = {}

            def fake_create_run_request(**kwargs):
                captured.update(kwargs)
                successor_dir.mkdir(parents=True)
                result = {
                    "run_id": "gate-successor",
                    "run_dir": str(successor_dir),
                    "project_root": str(root),
                    "created_at": "2026-08-14T00:01:00Z",
                }
                (successor_dir / "status.json").write_text(
                    json.dumps({"status": "starting"}) + "\n",
                    encoding="utf-8",
                )
                return result

            with (
                mock.patch(
                    "reccli.organization.create_run_request",
                    side_effect=fake_create_run_request,
                ),
                mock.patch(
                    "reccli.organization_launch.launch_organization_worker",
                    return_value={"pid": 5150},
                ),
            ):
                approved = approve_organization_request(
                    str(root),
                    "gate-run",
                    request_sha256=request["request_sha256"],
                    idempotency_key="gate-click-1",
                    requested_by="test-human",
                )

            self.assertEqual(approved["status"], "applied")
            self.assertTrue(approved["gate_applied"])
            target = root / "benchmarks" / "gates" / "shell-detection-v1.json"
            self.assertTrue(target.is_file())
            self.assertIn("shell-detection-v1", target.read_text())
            log = subprocess.run(
                ["git", "log", "-1", "--format=%s"], cwd=root,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertIn("ratify gate shell-detection-v1", log)
            self.assertEqual(
                approved["gate_applied_commit"],
                subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=root,
                    capture_output=True, text=True, check=True,
                ).stdout.strip(),
            )
            self.assertIn("ratified and locally applied", captured["mission"])
            self.assertEqual(
                captured["admission"]["work_class"], "deployable_artifact",
                "the click must launch the successor under the packet's "
                "implementation admission, not the governance parent's",
            )
            self.assertEqual(
                captured["admission"]["origin"], "approved-successor",
            )
            porcelain = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=root, capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertEqual(
                porcelain, "", "the gate apply must leave a clean tracked tree",
            )

    def test_successor_admission_resolution_order(self):
        from reccli.organization_control import _resolve_successor_admission

        proposal = {
            "consumer": {
                "name": "will", "type": "human",
                "intended_use": "review and merge the envelope promotion",
            },
            "work_class": "deployable_artifact",
            "done_condition": (
                "the ratified envelope predicate passes from a clean checkout"
            ),
            "stop_conditions": ["no improvement after two contracts"],
        }
        parent_contract = {
            "consumer": {
                "name": "will", "type": "human",
                "intended_use": "ratify the staged governance packet",
            },
            "work_class": "uncertainty_reduction",
            "done_condition": "a validated gate packet is staged for review",
            "stop_conditions": ["the proposal tree is unrecoverable"],
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            parent_dir = (
                root / "devsession" / "agent-organizations" / "gov-run"
            )
            parent_dir.mkdir(parents=True)
            (parent_dir / "admission.json").write_text(
                json.dumps(parent_contract) + "\n", encoding="utf-8",
            )
            # No packet field, no conclusion: parent contract carries.
            resolved = _resolve_successor_admission(
                root, {}, "gov-run", "will",
            )
            self.assertEqual(resolved["work_class"], "uncertainty_reduction")
            # Conclusion proposal outranks the parent carry (covers packets
            # staged by supervisors that predate the packet field).
            (parent_dir / "run-conclusion.json").write_text(
                json.dumps({
                    "proposed_successor_admission": proposal,
                }) + "\n", encoding="utf-8",
            )
            resolved = _resolve_successor_admission(
                root, {}, "gov-run", "will",
            )
            self.assertEqual(resolved["work_class"], "deployable_artifact")
            # The packet field outranks everything.
            packet_variant = dict(proposal)
            packet_variant["work_class"] = "hypothesis_test"
            resolved = _resolve_successor_admission(
                root, {"successor_admission": packet_variant}, "gov-run",
                "will",
            )
            self.assertEqual(resolved["work_class"], "hypothesis_test")
            self.assertEqual(resolved["origin"], "approved-successor")

    def test_process_activity_tracks_actual_native_agent_not_logical_state(self):
        run_dir = Path("/tmp/control-run")
        process_listing = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                " 100 S reccli.organization_worker /tmp/control-run/request.json\n"
                " 101 S codex exec --cd /tmp/control-run/manager-b --json\n"
                " 102 S claude -p --add-dir /tmp/control-run/context-packs/manager-c\n"
                " 103 Z codex exec --cd /tmp/control-run/lead --json\n"
            ),
            stderr="",
        )
        with patch(
            "reccli.organization_control.subprocess.run",
            return_value=process_listing,
        ):
            live, active = process_group_activity(
                100,
                run_dir,
                ["lead", "manager-a", "manager-b", "manager-c"],
            )
        self.assertTrue(live)
        self.assertEqual(active, ["manager-b", "manager-c"])

    def test_snapshot_builds_dashboard_graph_and_activity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = _make_run(root)
            (run_dir / "messages.jsonl").write_text(
                json.dumps({
                    "round": 2,
                    "from": "worker-a",
                    "to": "manager-a",
                    "tag": "status",
                    "content": "Fixture reproduced.",
                    "deliveredAt": "2026-07-17T00:00:00Z",
                }) + "\n",
                encoding="utf-8",
            )
            (run_dir / "activity.jsonl").write_text(
                json.dumps({
                    "schema": "reccli.organization-activity.v1",
                    "ts": "2026-07-17T00:00:01Z",
                    "agent_id": "worker-a",
                    "provider": "claude",
                    "turn": 1,
                    "type": "read",
                    "status": "started",
                    "content": "Reading docs/Core/Critical/contract.txt",
                    "paths": ["docs/Core/Critical/contract.txt"],
                }) + "\n",
                encoding="utf-8",
            )
            (run_dir / "goal-state.json").write_text(
                json.dumps({
                    "schema": "reccli.organization-goals.v1",
                    "worker_goals": {
                        "worker-a": {
                            "worker_id": "worker-a",
                            "manager_id": "manager-a",
                            "work_item": "primitive-qualifier",
                            "objective": (
                                "Fix the primitive qualifier and pass its test."
                            ),
                            "risk": "high",
                            "status": "active",
                        },
                    },
                    "off_goal_flags": [],
                }) + "\n",
                encoding="utf-8",
            )
            turns = run_dir / "turns"
            turns.mkdir()
            (turns / "worker-a.jsonl").write_text(
                json.dumps({
                    "round": 2,
                    "agent_id": "worker-a",
                    "status": "completed",
                    "duration_ms": 1200,
                    "reply": {"summary": "Reproduced the fixture."},
                }) + "\n",
                encoding="utf-8",
            )
            (run_dir / "run-conclusion.json").write_text(
                json.dumps({
                    "schema": "reccli.organization-run-conclusion.v1",
                    "summary": "The organization qualified the fixture.",
                    "promotion_readiness": "not_ready",
                    "accomplishments": ["Fixture reproduced."],
                    "conclusive_findings": [],
                    "evidence_and_tests": [],
                    "scientific_or_product_blockers": [],
                    "infrastructure_failures": [],
                    "unresolved": [],
                    "next_action": "Review the receipt.",
                    "limitations": [],
                }) + "\n",
                encoding="utf-8",
            )

            snapshot = organization_snapshot(str(root), "control-run")
            self.assertEqual(snapshot["status"], "running")
            # Flat fleet: lead + six workers + two auditors.
            self.assertEqual(len(snapshot["topology_graph"]["agents"]), 9)
            self.assertEqual(
                next(
                    agent for agent in snapshot["topology_graph"]["agents"]
                    if agent["id"] == "worker-a"
                )["last_turn"]["summary"],
                "Reproduced the fixture.",
            )
            self.assertEqual(
                next(
                    agent for agent in snapshot["topology_graph"]["agents"]
                    if agent["id"] == "worker-a"
                )["goal"]["work_item"],
                "primitive-qualifier",
            )
            self.assertEqual(len(snapshot["messages"]), 1)
            self.assertEqual(len(snapshot["activities"]), 3)
            self.assertEqual(snapshot["telemetry"][0]["activity_type"], "telemetry")
            self.assertEqual(snapshot["telemetry"][0]["agent_id"], "worker-a")
            self.assertEqual(
                snapshot["conclusion"]["summary"],
                "The organization qualified the fixture.",
            )

            listed = list_organization_runs(str(root))
            self.assertEqual(listed["runs"][0]["run_id"], "control-run")
            self.assertEqual(listed["runs"][0]["control_protocol"],
                             "reccli.organization-control.v1")

    def test_idempotent_message_is_applied_to_group_at_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = _make_run(root)
            runner = OrganizationRunner(
                root,
                "Qualify the system.",
                "claude",
                "scientific",
                "control-run",
                run_dir,
            )
            first = queue_control_request(
                str(root),
                "control-run",
                "message",
                target="workers",
                content="Prioritize the smallest failing layer.",
                tag="plan",
                idempotency_key="operator-1",
            )
            second = queue_control_request(
                str(root),
                "control-run",
                "message",
                target="workers",
                content="Prioritize the smallest failing layer.",
                tag="plan",
                idempotency_key="operator-1",
            )
            self.assertEqual(first["id"], second["id"])
            self.assertTrue(second["idempotent_replay"])

            self.assertIsNone(runner._apply_control_requests(2))
            for worker_id in runner.topology.worker_ids:
                self.assertEqual(len(runner.inboxes[worker_id]), 1)
                self.assertTrue(runner.inboxes[worker_id][0]["operator_message"])
                self.assertEqual(runner.states[worker_id], "working")
            self.assertEqual(pending_control_requests(run_dir), [])
            acknowledgement = json.loads(
                (run_dir / "control" / "acknowledgements" /
                 f"{first['id']}.json").read_text(),
            )
            self.assertEqual(acknowledgement["status"], "applied")
            self.assertEqual(
                acknowledgement["targets"],
                runner.topology.worker_ids,
            )

    def test_pause_and_resume_are_durable_boundary_commands(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = _make_run(root)
            runner = OrganizationRunner(
                root,
                "Qualify the system.",
                "claude",
                "scientific",
                "control-run",
                run_dir,
            )
            queue_control_request(str(root), "control-run", "pause")
            runner._apply_control_requests(3)
            self.assertTrue(runner.paused)
            queue_control_request(str(root), "control-run", "resume")
            runner._apply_control_requests(3)
            self.assertFalse(runner.paused)

    def test_worker_first_turn_requires_lead_work_package(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = _make_run(root)
            runner = OrganizationRunner(
                root,
                "Qualify the system.",
                "claude",
                "flat",
                "control-run",
                run_dir,
            )
            base = {
                "to": "worker-a",
                "tag": "plan",
                "content": "Run the bounded control.",
                "candidate": None,
                "workItem": "p0-control",
                "risk": "high",
            }
            runner._deliver_message("auditor-a", base, 2)
            self.assertEqual(len(runner.inboxes["worker-a"]), 0)
            self.assertNotIn(
                "worker-a",
                {agent.agent_id for agent in runner._select_agents(3)},
            )
            runner._deliver_message(
                "lead",
                {**base, "workItem": None},
                2,
            )
            self.assertEqual(len(runner.inboxes["worker-a"]), 0)
            runner._deliver_message("lead", base, 2)
            self.assertEqual(len(runner.inboxes["worker-a"]), 1)
            scheduled = {
                agent.agent_id for agent in runner._select_agents(3)
            }
            self.assertIn("worker-a", scheduled)
            records = [
                json.loads(line)
                for line in (run_dir / "messages.jsonl").read_text().splitlines()
            ]
            self.assertEqual(records[0]["status"], "dropped")
            self.assertEqual(records[1]["status"], "dropped")
            self.assertIn("workItem", records[1]["reason"])
            self.assertEqual(records[2]["status"], "delivered")

    def test_legacy_run_rejects_steering_but_remains_observable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _make_run(root, protocol=False)
            result = queue_control_request(
                str(root),
                "control-run",
                "message",
                target="lead",
                content="Change direction.",
            )
            self.assertEqual(result["status"], "unsupported")
            snapshot = organization_snapshot(str(root), "control-run")
            self.assertFalse(snapshot["control_capabilities"]["message"])

    def test_console_bridge_uses_same_control_layer(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _make_run(root)
            listed = dispatch({
                "command": "list",
                "working_directory": str(root),
            })
            self.assertEqual(listed["runs"][0]["run_id"], "control-run")
            queued = dispatch({
                "command": "control",
                "working_directory": str(root),
                "run_id": "control-run",
                "action": "message",
                "target": "lead",
                "content": "Report the earliest failing layer.",
                "idempotency_key": "bridge-1",
            })
            self.assertEqual(queued["status"], "queued")

    def test_acknowledgement_is_atomic_and_removes_pending_request(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = _make_run(root)
            request = queue_control_request(
                str(root),
                "control-run",
                "pause",
            )
            self.assertEqual(len(pending_control_requests(run_dir)), 1)
            acknowledge_control_request(
                run_dir,
                request,
                "applied",
                "Paused.",
                applied_round=2,
            )
            self.assertEqual(pending_control_requests(run_dir), [])


if __name__ == "__main__":
    unittest.main()
