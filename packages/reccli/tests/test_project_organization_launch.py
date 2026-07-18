import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from reccli.organization_project_launch import (
    PROJECT_LAUNCH_FILENAME,
    ProjectOrganizationLaunchError,
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


def _write_dynamic_contract(root: Path, *, stale_head: bool = False) -> None:
    (root / "mission.md").write_text(
        "Audit the current project state and ship only a verified candidate.\n",
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
                    "max_experiments": 1,
                }},
            }}))
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (root / PROJECT_LAUNCH_FILENAME).write_text(
        json.dumps(
            {
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
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _git(root, "add", "mission.md", "emit.py", PROJECT_LAUNCH_FILENAME)
    _git(root, "commit", "-qm", "add dynamic organization launch")


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
