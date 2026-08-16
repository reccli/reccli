import hashlib
import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from reccli.organization_project_launch import (
    PROJECT_LAUNCH_FILENAME,
    ProjectOrganizationLaunchError,
    _apply_terminal_continuation,
    _continuation_mission,
    start_project_organization,
)
from reccli.organization_launch import launch_organization_console


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


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


def _write_dynamic_contract(
    root: Path,
    *,
    stale_head: bool = False,
    continuation: bool = False,
    carry_experiment_budget: bool | None = True,
    max_experiments: int = 1,
    mission_text: str = (
        "Audit the current project state and ship only a verified candidate.\n"
    ),
) -> None:
    (root / "mission.md").write_text(
        mission_text,
        encoding="utf-8",
    )
    (root / "emit.py").write_text(
        textwrap.dedent(
            f"""
            import hashlib
            import json
            import os
            import subprocess

            mission = open("mission.md", encoding="utf-8").read().strip()
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            if {stale_head!r}:
                head = "0" * 40
            print(json.dumps({{
                "status": "ready",
                "mission_selection": {{
                    "mode": "dynamic",
                    "mission_id": "current-state-audit",
                    "mission_path": "mission.md",
                    "mission_sha256": hashlib.sha256(
                        mission.encode("utf-8"),
                    ).hexdigest(),
                    "checked_head": head,
                    "state_fingerprint": hashlib.sha256(
                        b"reviewed-state-v1",
                    ).hexdigest(),
                    "reason": "The reviewed project state selects this mission.",
                }},
                "start_organization": {{
                    "working_directory": os.getcwd(),
                    "mission": mission,
                    "provider": "codex",
                    "topology": "scientific",
                    "max_rounds": 4,
                    "max_concurrency": 3,
                    "max_experiments": {max_experiments!r},
                }},
            }}))
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    contract = {
        "schema": "reccli.project-organization-launch.v1",
        "preflight_commands": [
            {
                "id": "unit-preflight",
                "argv": ["python3", "-c", "print('preflight ok')"],
                "timeout_seconds": 10,
            }
        ],
        "emitter_command": {
            "id": "unit-emitter",
            "argv": ["python3", "emit.py"],
            "timeout_seconds": 10,
        },
        "require_dynamic_mission": True,
    }
    if continuation:
        contract["continuation_policy"] = {
            "mode": "latest-terminal-conclusion",
            "eligible_statuses": [
                "completed_no_promotion",
                "round_limit",
                "stalled",
            ],
            "eligible_promotion_readiness": ["not_ready", "no_candidate"],
        }
        if carry_experiment_budget is not None:
            contract["continuation_policy"]["carry_experiment_budget"] = (
                carry_experiment_budget
            )
    (root / PROJECT_LAUNCH_FILENAME).write_text(
        json.dumps(contract, indent=2)
        + "\n",
        encoding="utf-8",
    )
    _git(root, "add", "mission.md", "emit.py", PROJECT_LAUNCH_FILENAME)
    _git(root, "commit", "-qm", "add dynamic organization launch")


def _write_terminal_run(
    root: Path,
    *,
    run_id: str = "terminal-parent",
    status: str = "round_limit",
    readiness: str = "not_ready",
    generated_by: str = "lead",
    proposed_admission: dict | None = None,
    parent_admission: dict | None = None,
) -> Path:
    run_dir = root / "devsession" / "agent-organizations" / run_id
    run_dir.mkdir(parents=True)
    run = {
        "run_id": run_id,
        "project_root": str(root),
        "mission": (
            "Investigate the original defect. Do not modify production data "
            "or grant canonical acceptance."
        ),
        "status": status,
        "created_at": "2026-07-18T00:00:00Z",
        "topology": "scientific",
    }
    (run_dir / "run.json").write_text(
        json.dumps(run, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "status.json").write_text(
        json.dumps({
            **run,
            "round": 12,
            "max_rounds": 8,
            "completed_turns": 71,
            "failed_turns": 0,
            "updated_at": "2026-07-18T01:00:00Z",
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    conclusion = {
        "schema": "reccli.organization-run-conclusion.v1",
        "run_id": run_id,
        "terminal_status": status,
        "generated_at": "2026-07-18T01:00:00Z",
        "generated_by": generated_by,
        "lead_agent_id": "lead",
        "lead_provider": "codex",
        "summary": (
            "The old candidate was rejected after a deeper defect emerged. "
            "The run ended at its 12-turn limit: 8 working rounds plus 4 "
            "closeout rounds."
        ),
        "accomplishments": [
            "Rejected the obsolete candidate.",
            "Reproduced the deeper failure.",
        ],
        "conclusive_findings": [
            "Low residual does not prove parameter identifiability.",
        ],
        "evidence_and_tests": ["17 focused controls passed."],
        "scientific_or_product_blockers": [
            "The project lacks an identifiability contract.",
        ],
        "infrastructure_failures": [],
        "unresolved": ["Define ambiguity semantics before implementation."],
        "promotion_readiness": readiness,
        "next_action": (
            "Specify a falsifiable identifiability contract and stage the "
            "remaining authority choice."
        ),
        "limitations": ["No production change was authorized."],
        "candidates": [],
        "integrated_candidates": {},
        "verified_candidate": None,
        "promotion_candidate": None,
        "promotion_request": None,
        "no_promotion_report": None,
        "pending_human_report": None,
        "artifacts": [],
        "turn_counts": {"attempted": 71, "completed": 71, "failed": 0},
        "round_counts": {"total": 12, "working": 8, "closeout": 4},
        "experiment_budget": {"maximum": 3, "used": 1, "remaining": 2},
        "canonical_effects_applied": False,
        "proposed_successor_admission": proposed_admission,
    }
    if parent_admission is not None:
        (run_dir / "admission.json").write_text(
            json.dumps(parent_admission, indent=2) + "\n",
            encoding="utf-8",
        )
    (run_dir / "run-conclusion.json").write_text(
        json.dumps(conclusion, indent=2) + "\n",
        encoding="utf-8",
    )
    return run_dir


class ProjectOrganizationLaunchTests(unittest.TestCase):
    def test_console_reuses_matching_token_bearing_process(self):
        existing = {
            "pid": 4321,
            "token": "existing-token",
            "url": "http://127.0.0.1:8777/?token=existing-token",
        }
        with patch(
            "reccli.organization_launch._running_console",
            return_value=existing,
        ), patch(
            "reccli.organization_launch.webbrowser.open",
        ) as opened, patch(
            "reccli.organization_launch.subprocess.Popen",
        ) as popen:
            result = launch_organization_console(
                Path("/tmp/project"),
                open_browser=True,
            )
        self.assertEqual(result["status"], "running")
        self.assertTrue(result["reused"])
        self.assertEqual(result["url"], existing["url"])
        opened.assert_called_once_with(existing["url"])
        popen.assert_not_called()

    def test_dynamic_project_contract_launches_exact_emitted_arguments(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _write_dynamic_contract(root)
            captured = {}

            def fake_start(arguments):
                captured.update(arguments)
                return {
                    "status": "starting",
                    "run_id": "dynamic-run",
                    "run_dir": str(root / "devsession" / "dynamic-run"),
                    "pid": 1234,
                }

            with patch(
                "reccli.organization_launch.start_organization_from_arguments",
                side_effect=fake_start,
            ), patch(
                "reccli.organization_launch.launch_organization_console",
                return_value={
                    "status": "starting",
                    "url": "http://127.0.0.1:8777/?token=test",
                },
            ):
                result = start_project_organization(str(root))

            self.assertEqual(result["status"], "starting")
            self.assertEqual(result["run_id"], "dynamic-run")
            self.assertEqual(
                result["mission_selection"]["mission_id"],
                "current-state-audit",
            )
            self.assertTrue(
                result["launch_contract"]["dynamic_mission_required"],
            )
            self.assertEqual(result["preflights"][0]["stdout"].strip(), "preflight ok")
            self.assertEqual(
                Path(captured["working_directory"]).resolve(),
                root.resolve(),
            )
            self.assertEqual(captured["topology"], "scientific")
            self.assertEqual(captured["max_rounds"], 4)
            self.assertEqual(
                captured["mission"],
                (root / "mission.md").read_text(encoding="utf-8").strip(),
            )

    def test_stale_dynamic_selection_is_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _write_dynamic_contract(root, stale_head=True)
            with patch(
                "reccli.organization_launch.start_organization_from_arguments",
            ) as start:
                with self.assertRaises(ProjectOrganizationLaunchError) as raised:
                    start_project_organization(str(root), open_console=False)
            self.assertEqual(raised.exception.code, "stale_dynamic_mission")
            start.assert_not_called()

    def test_terminal_conclusion_drives_bounded_successor_mission(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _write_dynamic_contract(
                root,
                continuation=True,
                max_experiments=3,
            )
            parent_dir = _write_terminal_run(root)
            captured = {}

            def fake_start(arguments):
                captured.update(arguments)
                return {
                    "status": "starting",
                    "run_id": "successor-run",
                    "run_dir": str(
                        root / "devsession" / "agent-organizations"
                        / "successor-run"
                    ),
                    "pid": 1234,
                }

            with patch(
                "reccli.organization_launch.start_organization_from_arguments",
                side_effect=fake_start,
            ):
                result = start_project_organization(
                    str(root),
                    open_console=False,
                )

            conclusion_bytes = (
                parent_dir / "run-conclusion.json"
            ).read_bytes()
            expected_sha = hashlib.sha256(
                conclusion_bytes,
            ).hexdigest()
            self.assertEqual(
                result["mission_selection"]["mode"],
                "terminal_continuation",
            )
            self.assertEqual(
                result["mission_selection"]["parent_run_id"],
                "terminal-parent",
            )
            self.assertEqual(
                result["mission_selection"]["parent_conclusion_sha256"],
                expected_sha,
            )
            self.assertEqual(
                result["launch_contract"]["continuation_mode"],
                "latest-terminal-conclusion",
            )
            self.assertEqual(
                result["launch_contract"]["experiment_budget_scope"],
                "chain",
            )
            self.assertEqual(
                captured["continuation_from_run_id"],
                "terminal-parent",
            )
            self.assertEqual(
                captured["continuation_conclusion_sha256"],
                expected_sha,
            )
            self.assertEqual(captured["mission_origin"], "terminal-conclusion")
            self.assertEqual(captured["max_experiments"], 2)
            self.assertIn(
                "Do not merely restate blockers", captured["mission"],
            )
            self.assertIn(
                "complete all", captured["mission"],
            )
            self.assertIn(
                "reversible work first", captured["mission"],
            )
            self.assertIn(
                "delegate a bounded worker implementation",
                captured["mission"],
            )
            self.assertIn(
                "parent conclusion",
                captured["mission"],
            )
            self.assertIn(
                "is a handoff, not authority",
                captured["mission"],
            )
            self.assertIn("12-round limit", captured["mission"])
            self.assertNotIn("12-turn limit", captured["mission"])
            self.assertNotIn(
                "Investigate the original defect", captured["mission"],
            )
            self.assertIn(
                "not copied into this successor", captured["mission"],
            )
            self.assertNotEqual(
                captured["mission"],
                (root / "mission.md").read_text(encoding="utf-8").strip(),
            )

    def test_terminal_continuation_does_not_grow_by_copying_prior_missions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _write_dynamic_contract(root, continuation=True)
            terminal = {
                "run_id": "terminal-parent",
                "status": "round_limit",
                "conclusion_sha256": "a" * 64,
                "conclusion": {
                    "summary": "One bounded result.",
                    "accomplishments": [],
                    "conclusive_findings": [],
                    "evidence_and_tests": [],
                    "scientific_or_product_blockers": [],
                    "infrastructure_failures": [],
                    "unresolved": [],
                    "promotion_readiness": "not_ready",
                    "next_action": "Measure the current predicate.",
                    "limitations": [],
                },
                "run": {
                    "mission": (
                        "# Successor mission\n"
                        + ("historical recursive material\n" * 20_000)
                    ),
                },
            }
            selection = {"mission_id": "current-state-audit"}
            first = _continuation_mission(
                root,
                terminal,
                selection,
            )
            terminal["run"]["mission"] = first
            second = _continuation_mission(
                root,
                terminal,
                selection,
            )
            self.assertLess(len(first), 10_000)
            self.assertLessEqual(len(second), len(first) + 100)
            self.assertNotIn("historical recursive material", first)
            self.assertEqual(first.count("## Required final output"), 1)

    def test_continuation_prefers_the_conclusions_proposed_admission(self):
        parent = {
            "consumer": {
                "name": "will", "type": "human",
                "intended_use": "review and merge the promotion this week",
            },
            "work_class": "deployable_artifact",
            "done_condition": "the standing predicate passes from a clean checkout",
            "stop_conditions": ["no improvement after two contracts"],
        }
        proposed = {
            "consumer": {
                "name": "will", "type": "human",
                "intended_use": "ratify the proposed capability gate this week",
            },
            "work_class": "hypothesis_test",
            "done_condition": (
                "a ratifiable predicate exists with fixture, baseline, and "
                "tolerance"
            ),
            "stop_conditions": ["the fixture cannot be derived from real scans"],
        }
        policy = {
            "mode": "latest-terminal-conclusion",
            "eligible_statuses": {"round_limit"},
            "eligible_promotion_readiness": {"not_ready"},
            "carry_experiment_budget": False,
        }
        def _commit_seed(root: Path) -> None:
            (root / "seed.txt").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "add", "seed.txt"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "seed"], cwd=root, check=True,
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _commit_seed(root)
            _write_terminal_run(
                root,
                proposed_admission=proposed,
                parent_admission=parent,
            )
            updated, _ = _apply_terminal_continuation(
                root,
                {"working_directory": str(root)},
                {"mission_id": "m", "state_fingerprint": "f"},
                policy,
            )
            self.assertEqual(
                updated["admission"]["work_class"], "hypothesis_test",
                "a valid conclusion proposal must outrank the parent carry",
            )
            self.assertEqual(
                updated["admission"]["carried_from_run_id"], "terminal-parent",
            )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _commit_seed(root)
            _write_terminal_run(root, parent_admission=parent)
            updated, _ = _apply_terminal_continuation(
                root,
                {"working_directory": str(root)},
                {"mission_id": "m", "state_fingerprint": "f"},
                policy,
            )
            self.assertEqual(
                updated["admission"]["work_class"], "deployable_artifact",
                "without a proposal the parent contract carries",
            )

    def test_autonomous_chain_ends_at_the_link_cap(self):
        policy = {
            "mode": "latest-terminal-conclusion",
            "eligible_statuses": {"round_limit"},
            "eligible_promotion_readiness": {"not_ready"},
            "carry_experiment_budget": False,
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            (root / "seed.txt").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "add", "seed.txt"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "seed"], cwd=root, check=True,
            )
            # Three consecutive terminal-conclusion links: link-1 <- link-2
            # <- link-3, with link-3 the latest terminal.
            previous = None
            for index in (1, 2, 3):
                run_id = f"link-{index}"
                run_dir = _write_terminal_run(root, run_id=run_id)
                run_path = run_dir / "run.json"
                record = json.loads(run_path.read_text(encoding="utf-8"))
                record["mission_origin"] = "terminal-conclusion"
                record["continuation_from_run_id"] = previous
                run_path.write_text(
                    json.dumps(record, indent=2) + "\n", encoding="utf-8",
                )
                previous = run_id
                # Keep directory mtimes ordered so link-3 is the latest.
                os.utime(run_dir, (1000000000 + index, 1000000000 + index))
            updated, selection = _apply_terminal_continuation(
                root,
                {"working_directory": str(root)},
                {"mission_id": "m", "state_fingerprint": "f"},
                policy,
            )
            self.assertIn("continuation_declined", selection)
            self.assertIn("human relaunch", selection["continuation_declined"])
            self.assertNotIn("continuation_from_run_id", updated)

    def test_a_cancelled_run_never_wedges_project_launch(self):
        # Cancellation SIGTERMs the supervisor before it writes a
        # conclusion. Reading that file unconditionally meant every operator
        # cancellation permanently blocked project-mode launch: three
        # legitimate cancellations in one evening left the project unable to
        # launch at all.
        from reccli.organization_project_launch import _latest_terminal_record

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = (
                root / "devsession" / "agent-organizations" / "cancelled-run"
            )
            run_dir.mkdir(parents=True)
            record = {
                "run_id": "cancelled-run",
                "project_root": str(root),
                "mission": "Do the bounded work.",
                "topology": "flat",
            }
            (run_dir / "run.json").write_text(
                json.dumps(record, indent=2) + "\n", encoding="utf-8",
            )
            (run_dir / "status.json").write_text(
                json.dumps({
                    **record, "status": "cancelled",
                }, indent=2) + "\n",
                encoding="utf-8",
            )
            self.assertFalse((run_dir / "run-conclusion.json").exists())
            self.assertIsNone(
                _latest_terminal_record(root),
                "a terminal run with no conclusion is not a continuation "
                "source and must never block a launch",
            )

    def test_terminal_continuation_carries_binding_human_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _write_dynamic_contract(root, continuation=True)
            parent_dir = _write_terminal_run(root)
            rejected = "a" * 40
            (parent_dir / "operator-decision.json").write_text(
                json.dumps({
                    "schema": "reccli.organization-operator-decision.v1",
                    "run_id": "terminal-parent",
                    "candidate": rejected,
                    "decision": "rejected",
                    "reason": (
                        "No evaluator-measured progress on the stated goal."
                    ),
                }, indent=2) + "\n",
                encoding="utf-8",
            )
            captured = {}

            def fake_start(arguments):
                captured.update(arguments)
                return {
                    "status": "starting",
                    "run_id": "successor-run",
                    "run_dir": str(
                        root / "devsession" / "agent-organizations"
                        / "successor-run"
                    ),
                    "pid": 1234,
                }

            with patch(
                "reccli.organization_launch.start_organization_from_arguments",
                side_effect=fake_start,
            ):
                start_project_organization(str(root), open_console=False)

            self.assertIn("Binding human rejection", captured["mission"])
            self.assertIn(rejected, captured["mission"])
            self.assertIn(
                "Do not revive, re-review, repackage",
                captured["mission"],
            )
            self.assertIn(
                "No evaluator-measured progress",
                captured["mission"],
            )

    def test_terminal_continuation_uses_fresh_per_run_experiment_budget(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _write_dynamic_contract(
                root,
                continuation=True,
                carry_experiment_budget=False,
                max_experiments=3,
            )
            _write_terminal_run(root)
            captured = {}

            def fake_start(arguments):
                captured.update(arguments)
                return {
                    "status": "starting",
                    "run_id": "successor-run",
                    "run_dir": str(
                        root / "devsession" / "agent-organizations"
                        / "successor-run"
                    ),
                    "pid": 1234,
                }

            with patch(
                "reccli.organization_launch.start_organization_from_arguments",
                side_effect=fake_start,
            ):
                result = start_project_organization(
                    str(root),
                    open_console=False,
                )

            self.assertEqual(captured["max_experiments"], 3)
            self.assertEqual(
                result["launch_contract"]["experiment_budget_scope"],
                "per_run",
            )

    def test_terminal_continuation_defaults_to_per_run_experiment_budget(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _write_dynamic_contract(
                root,
                continuation=True,
                carry_experiment_budget=None,
                max_experiments=3,
            )
            _write_terminal_run(root)
            captured = {}

            def fake_start(arguments):
                captured.update(arguments)
                return {
                    "status": "starting",
                    "run_id": "successor-run",
                    "run_dir": str(
                        root / "devsession" / "agent-organizations"
                        / "successor-run"
                    ),
                    "pid": 1234,
                }

            with patch(
                "reccli.organization_launch.start_organization_from_arguments",
                side_effect=fake_start,
            ):
                result = start_project_organization(
                    str(root),
                    open_console=False,
                )

            self.assertEqual(captured["max_experiments"], 3)
            self.assertEqual(
                result["launch_contract"]["experiment_budget_scope"],
                "per_run",
            )

    def test_retryable_host_failure_does_not_block_prior_lead_continuation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _write_dynamic_contract(
                root,
                continuation=True,
                carry_experiment_budget=False,
                max_experiments=3,
            )
            parent_dir = _write_terminal_run(
                root,
                run_id="eligible-parent",
            )
            failed_dir = _write_terminal_run(
                root,
                run_id="failed-supervisor",
                status="failed",
                readiness="no_candidate",
                generated_by="host-fallback",
            )
            failed_conclusion_path = failed_dir / "run-conclusion.json"
            failed_conclusion = json.loads(
                failed_conclusion_path.read_text(encoding="utf-8"),
            )
            failed_conclusion["infrastructure_failures"] = [
                "delegation barrier failed after a rejected manager turn",
            ]
            failed_conclusion["verified_candidate"] = None
            failed_conclusion["promotion_candidate"] = None
            failed_conclusion["promotion_request"] = None
            failed_conclusion_path.write_text(
                json.dumps(failed_conclusion, indent=2) + "\n",
                encoding="utf-8",
            )
            os.utime(parent_dir, (1, 1))
            os.utime(failed_dir, (2, 2))
            captured = {}

            def fake_start(arguments):
                captured.update(arguments)
                return {
                    "status": "starting",
                    "run_id": "successor-run",
                    "run_dir": str(
                        root / "devsession" / "agent-organizations"
                        / "successor-run"
                    ),
                    "pid": 1234,
                }

            with patch(
                "reccli.organization_launch.start_organization_from_arguments",
                side_effect=fake_start,
            ):
                result = start_project_organization(
                    str(root),
                    open_console=False,
                )

            self.assertEqual(
                result["mission_selection"]["parent_run_id"],
                "eligible-parent",
            )
            self.assertEqual(
                result["mission_selection"]["skipped_retryable_run_ids"],
                ["failed-supervisor"],
            )
            self.assertIn("eligible-parent", captured["mission"])
            self.assertEqual(captured["max_experiments"], 3)

    def test_ineligible_terminal_result_blocks_instead_of_replaying_base_mission(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _write_dynamic_contract(root, continuation=True)
            _write_terminal_run(
                root,
                status="completed",
                readiness="ready_for_human_review",
            )
            with patch(
                "reccli.organization_launch.start_organization_from_arguments",
            ) as start:
                with self.assertRaises(ProjectOrganizationLaunchError) as raised:
                    start_project_organization(
                        str(root),
                        open_console=False,
                    )
            self.assertEqual(
                raised.exception.code,
                "continuation_not_authorized",
            )
            start.assert_not_called()

    def test_host_fallback_conclusion_cannot_drive_autonomous_successor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _write_dynamic_contract(root, continuation=True)
            _write_terminal_run(root, generated_by="host-fallback")
            with patch(
                "reccli.organization_launch.start_organization_from_arguments",
            ) as start:
                with self.assertRaises(ProjectOrganizationLaunchError) as raised:
                    start_project_organization(
                        str(root),
                        open_console=False,
                    )
            self.assertEqual(
                raised.exception.code,
                "continuation_not_authorized",
            )
            start.assert_not_called()

    def test_terminal_status_mismatch_cannot_drive_successor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _write_dynamic_contract(root, continuation=True)
            run_dir = _write_terminal_run(root)
            conclusion_path = run_dir / "run-conclusion.json"
            conclusion = json.loads(conclusion_path.read_text(encoding="utf-8"))
            conclusion["terminal_status"] = "completed_no_promotion"
            conclusion_path.write_text(
                json.dumps(conclusion, indent=2) + "\n",
                encoding="utf-8",
            )
            with patch(
                "reccli.organization_launch.start_organization_from_arguments",
            ) as start:
                with self.assertRaises(ProjectOrganizationLaunchError) as raised:
                    start_project_organization(
                        str(root),
                        open_console=False,
                    )
            self.assertEqual(
                raised.exception.code,
                "terminal_conclusion_invalid",
            )
            start.assert_not_called()

    def test_large_dynamic_mission_is_not_truncated_before_launch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            mission = "Inspect this bounded item.\n" * 800
            _write_dynamic_contract(root, mission_text=mission)
            captured = {}

            def fake_start(arguments):
                captured.update(arguments)
                return {
                    "status": "starting",
                    "run_id": "large-mission-run",
                    "run_dir": str(root / "large-mission-run"),
                    "pid": 1234,
                }

            with patch(
                "reccli.organization_launch.start_organization_from_arguments",
                side_effect=fake_start,
            ):
                result = start_project_organization(
                    str(root),
                    open_console=False,
                )

            self.assertEqual(result["status"], "starting")
            self.assertEqual(captured["mission"], mission.strip())

    def test_existing_live_run_is_returned_instead_of_duplicate_launch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _write_dynamic_contract(root)
            run_dir = (
                root
                / "devsession"
                / "agent-organizations"
                / "already-running"
            )
            run_dir.mkdir(parents=True)
            run = {
                "run_id": "already-running",
                "project_root": str(root),
                "status": "running",
                "topology": "scientific",
            }
            (run_dir / "run.json").write_text(
                json.dumps(run) + "\n",
                encoding="utf-8",
            )
            (run_dir / "status.json").write_text(
                json.dumps({**run, "round": 2}) + "\n",
                encoding="utf-8",
            )
            with patch(
                "reccli.organization_launch.start_organization_from_arguments",
            ) as start, patch(
                "reccli.organization_launch.launch_organization_console",
                return_value={
                    "status": "running",
                    "url": "http://127.0.0.1:8777/?token=existing",
                },
            ):
                result = start_project_organization(str(root))
            self.assertEqual(result["status"], "already_running")
            self.assertEqual(result["run_id"], "already-running")
            self.assertEqual(result["blocker"], "organization_already_active")
            start.assert_not_called()

    def test_untracked_contract_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            (root / "app.py").write_text("print('test')\n", encoding="utf-8")
            _git(root, "add", "app.py")
            _git(root, "commit", "-qm", "initial")
            (root / PROJECT_LAUNCH_FILENAME).write_text(
                json.dumps(
                    {
                        "schema": "reccli.project-organization-launch.v1",
                        "preflight_commands": [],
                        "emitter_command": {
                            "argv": ["python3", "-c", "print('{}')"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ProjectOrganizationLaunchError) as raised:
                start_project_organization(str(root), open_console=False)
            self.assertEqual(raised.exception.code, "untracked_contract_path")


if __name__ == "__main__":
    unittest.main()
