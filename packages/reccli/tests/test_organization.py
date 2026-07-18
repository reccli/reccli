"""Offline tests for the subscription-backed organization MCP engine."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from reccli.organization import (
    AGENT_REPLY_SCHEMA,
    HOST_CANDIDATE,
    AgentSpec,
    Governance,
    OrganizationRunner,
    SubscriptionSession,
    Workspace,
    build_provider_assignments,
    build_project_context,
    create_run_request,
    get_topology,
    prepare_context_packs,
    prepare_evidence_snapshot,
    prepare_workspaces,
    resolve_provider_plan,
    validate_agent_reply,
    verify_context_packs,
    verify_context_sources_unchanged,
    verify_evidence_sources_unchanged,
    verify_evidence_snapshot,
)


def _reply(summary="ok"):
    return {
        "messages": [],
        "summary": summary,
        "state": "working",
        "artifacts": [],
        "candidate": None,
        "risk": None,
        "final": False,
    }


def _init_project(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
    (root / f"{root.name}.devproject").write_text(json.dumps({
        "format": "devproject",
        "version": "2.1.0",
        "project_root": str(root),
        "project": {"name": root.name, "description": "Organization test project", "status": "active"},
        "features": [{
            "feature_id": "feat_app",
            "title": "Application",
            "description": "Primary app behavior.",
            "status": "in-progress",
            "files_touched": ["app.py"],
            "docs": [{"path": "docs/contract.md"}],
        }],
    }, indent=2) + "\n", encoding="utf-8")
    subprocess.run(["git", "add", f"{root.name}.devproject"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "add project memory"], cwd=root, check=True)


def _add_context_manifest(root: Path) -> Path:
    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "common.md").write_text("shared authority\n", encoding="utf-8")
    for letter in "abcd":
        (docs / f"worker-{letter}.md").write_text(
            f"worker {letter} education\n", encoding="utf-8",
        )
        (docs / f"library-{letter}.md").write_text(
            f"worker {letter} indexed history\n", encoding="utf-8",
        )
    manifest = root / "context-packs.json"
    manifest.write_text(json.dumps({
        "schema": "reccli.organization-context-packs.v1",
        "description": "Test common-plus-lane routing.",
        "common": {
            "purpose": "Shared rules.",
            "paths": ["docs/common.md"],
        },
        "agents": {
            f"worker-{letter}": {
                "purpose": f"Worker {letter} lane.",
                "paths": [f"docs/worker-{letter}.md"],
                "library_paths": [f"docs/library-{letter}.md"],
            }
            for letter in "abcd"
        },
        "full_context_agents": [
            "lead", "manager-a", "manager-b", "manager-c", "manager-d",
        ],
    }, indent=2) + "\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "docs", "context-packs.json"], cwd=root, check=True,
    )
    subprocess.run(
        ["git", "commit", "-qm", "add context packs"], cwd=root, check=True,
    )
    return manifest


class OrganizationTopologyTests(unittest.TestCase):
    def test_google_rotating_enforces_selective_escalation(self):
        topology = get_topology("google-rotating")
        self.assertFalse(topology.can_route("worker-a", "lead", "question")[0])
        self.assertFalse(topology.can_route("worker-a", "worker-b", "question")[0])
        self.assertTrue(topology.can_route("manager-a", "manager-b", "question")[0])
        self.assertTrue(topology.can_route("worker-a", "manager-c", "question")[0])
        self.assertEqual(topology.finalizer_id, "manager-d")

    def test_alternate_manager_review_blocks_premature_forward(self):
        topology = get_topology("google-rotating")
        governance = Governance(topology, "stable-run")
        message = {
            "to": "manager-a", "tag": "handoff", "content": "Ready.",
            "candidate": "abc123", "workItem": "feature-a", "risk": "routine",
        }
        accepted, _, system_message = governance.process_message("worker-a", message, 1)
        self.assertTrue(accepted)
        self.assertIsNotNone(system_message)
        reviewer = governance.assignments["abc123"]["reviewerId"]
        self.assertNotIn(reviewer, {"manager-a", "manager-d"})

        accepted, reason, _ = governance.process_message(
            "manager-a", {**message, "to": "manager-d"}, 2,
        )
        self.assertFalse(accepted)
        self.assertIn("lacks approval", reason)

        governance.record_decision(reviewer, {
            "to": "manager-a", "tag": "decision", "content": "APPROVED: checks pass.",
            "candidate": "abc123", "workItem": "feature-a", "risk": "routine",
        })
        accepted, _, _ = governance.process_message(
            "manager-a", {**message, "to": "manager-d"}, 3,
        )
        self.assertTrue(accepted)

    def test_reply_validation_rejects_protocol_drift(self):
        self.assertEqual(validate_agent_reply(_reply())["summary"], "ok")
        invalid = _reply()
        invalid["extra"] = True
        with self.assertRaisesRegex(ValueError, "fields must be exactly"):
            validate_agent_reply(invalid)

    def test_mixed_governance_prefers_cross_provider_reviews(self):
        topology = get_topology("google-rotating")
        assignments = build_provider_assignments(topology, "claude", "codex")
        governance = Governance(topology, "mixed-run", assignments)
        accepted, _, system_message = governance.process_message("worker-a", {
            "to": "manager-a", "tag": "handoff", "content": "Ready.",
            "candidate": "abc123", "workItem": "feature-a", "risk": "routine",
        }, 1)
        self.assertTrue(accepted)
        reviewer = system_message["to"]
        self.assertNotEqual(assignments[reviewer], assignments["worker-a"])
        self.assertNotEqual(
            assignments[governance.release_reviewer_id],
            assignments[topology.release_manager_id],
        )

    def test_scientific_review_is_fully_sighted_veto_not_truth_approval(self):
        topology = get_topology("scientific")
        governance = Governance(topology, "scientific-run")
        handoff = {
            "to": "manager-a", "tag": "handoff", "content": "Sandbox result ready.",
            "candidate": "candidate-1", "workItem": "tmp-hypothesis", "risk": "high",
        }
        accepted, _, review = governance.process_message("worker-a", handoff, 1)
        self.assertTrue(accepted)
        self.assertEqual(review["to"], "manager-c")
        self.assertIn("NO_VETO", review["content"])

        governance.record_decision("manager-c", {
            "to": "manager-a", "tag": "decision",
            "content": "NO_VETO: no blocking falsification; visual meaning remains human judgment.",
            "candidate": "candidate-1", "workItem": "tmp-hypothesis", "risk": "high",
        })
        self.assertEqual(governance.assignments["candidate-1"]["status"], "reviewed")
        accepted, _, _ = governance.process_message(
            "manager-a", {**handoff, "to": "manager-d"}, 2,
        )
        self.assertTrue(accepted)

        governance.record_decision("manager-c", {
            "to": "manager-d", "tag": "decision",
            "content": "BLOCKED: primary receipt contradicts the claimed sign.",
            "candidate": "release-1", "workItem": "final-release", "risk": "release",
        })
        self.assertIn("manager-c", governance.missing_final_approvers("release-1"))

    def test_scientific_topology_grants_reversible_worker_agency(self):
        topology = get_topology("scientific")
        scopes = {agent.agent_id: agent.write_scope for agent in topology.agents}
        self.assertEqual(scopes["manager-d"], "integration")
        self.assertEqual(
            {agent_id for agent_id, scope in scopes.items() if scope == "workspace"},
            {"worker-a", "worker-b", "worker-c", "worker-d"},
        )
        self.assertFalse(topology.can_route("worker-a", "lead", "question")[0])
        self.assertTrue(topology.can_route("manager-a", "manager-c", "review")[0])
        self.assertEqual(topology.review_policy, "veto")
        self.assertTrue(topology.human_promotion_required)
        self.assertFalse(topology.blind_final_review)
        self.assertNotIn("manager-c", topology.primary_manager_by_worker.values())

    def test_scientific_role_slots_are_project_neutral(self):
        topology = get_topology("scientific")
        roles = {agent.agent_id: agent.role for agent in topology.agents}
        self.assertEqual(roles["worker-a"], "reproduction experimenter")
        self.assertEqual(roles["worker-b"], "hypothesis and model experimenter")
        self.assertEqual(roles["worker-c"], "structural and integration validator")
        self.assertEqual(
            roles["worker-d"],
            "uncertainty and alternative-explanation experimenter",
        )
        role_contract = "\n".join(
            f"{agent.role}\n{agent.instructions}" for agent in topology.agents
        ).lower()
        for project_term in (
            "cad", "geometry", "scan", "surface-family", "step readback",
            "rescan",
        ):
            self.assertNotIn(project_term, role_contract)

    def test_scientific_phase_binary_is_not_a_public_topology(self):
        with self.assertRaisesRegex(ValueError, "scientific"):
            get_topology("scientific-takeover")

    def test_scientific_mixed_plan_keeps_auditor_off_release_provider(self):
        topology = get_topology("scientific")
        assignments = build_provider_assignments(topology, "claude", "codex")
        self.assertEqual(assignments["manager-d"], "claude")
        self.assertEqual(assignments["manager-c"], "codex")
        for worker, primary in topology.primary_manager_by_worker.items():
            self.assertEqual(assignments[worker], assignments[primary])


class ProviderPlanTests(unittest.TestCase):
    @staticmethod
    def _which(name):
        return f"/fake/{name}" if name in {"claude", "codex"} else None

    def test_auto_mixes_two_authenticated_native_clis(self):
        topology = get_topology("google-rotating")
        with patch("reccli.organization.shutil.which", side_effect=self._which), patch(
            "reccli.organization._provider_authentication_status", return_value="authenticated",
        ), patch.dict(os.environ, {"RECCLI_HOST": "claude"}, clear=False):
            plan = resolve_provider_plan("auto", topology)
        self.assertEqual(plan.mode, "mixed")
        self.assertEqual(plan.host_provider, "claude")
        self.assertEqual(plan.provider_assignments["manager-d"], "claude")
        self.assertEqual(plan.provider_assignments["worker-a"], "codex")
        self.assertEqual(plan.provider_assignments["manager-a"], "codex")
        self.assertEqual(plan.blind_verifier_provider, "codex")

    def test_auto_falls_back_when_only_one_cli_is_usable(self):
        topology = get_topology("google-rotating")
        statuses = {"claude": "authenticated", "codex": "not_authenticated"}
        with patch("reccli.organization.shutil.which", side_effect=self._which), patch(
            "reccli.organization._provider_authentication_status",
            side_effect=lambda name: statuses[name],
        ), patch.dict(os.environ, {"RECCLI_HOST": "claude"}, clear=False):
            plan = resolve_provider_plan("auto", topology)
        self.assertEqual(plan.mode, "claude")
        self.assertEqual(set(plan.provider_assignments.values()), {"claude"})

    def test_explicit_mixed_rejects_missing_subscription_auth(self):
        topology = get_topology("google-rotating")
        statuses = {"claude": "authenticated", "codex": "not_authenticated"}
        with patch("reccli.organization.shutil.which", side_effect=self._which), patch(
            "reccli.organization._provider_authentication_status",
            side_effect=lambda name: statuses[name],
        ):
            with self.assertRaisesRegex(RuntimeError, "authenticated claude and codex"):
                resolve_provider_plan("mixed", topology)

    def test_explicit_provider_remains_homogeneous(self):
        topology = get_topology("google-rotating")
        with patch("reccli.organization.shutil.which", side_effect=self._which), patch(
            "reccli.organization._provider_authentication_status", return_value="authenticated",
        ):
            plan = resolve_provider_plan("codex", topology)
        self.assertEqual(plan.mode, "codex")
        self.assertEqual(set(plan.provider_assignments.values()), {"codex"})


class SubscriptionSessionTests(unittest.TestCase):
    def _workspace(self, root: Path) -> Workspace:
        return Workspace(root, "test", "test-main", root, [])

    def test_claude_session_resumes_with_structured_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            log_path = root / "claude_args.jsonl"
            executable = bin_dir / "claude"
            executable.write_text(f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path
args = sys.argv[1:]
sid = args[args.index('--resume') + 1] if '--resume' in args else args[args.index('--session-id') + 1]
Path({str(log_path)!r}).open('a').write(json.dumps(args) + '\\n')
print(json.dumps({{'is_error': False, 'session_id': sid, 'structured_output': {_reply()!r}, 'usage': {{'input_tokens': 2, 'cache_read_input_tokens': 3, 'output_tokens': 1}}}}))
""", encoding="utf-8")
            executable.chmod(0o755)
            env_path = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            session = SubscriptionSession("claude", self._workspace(root), True, "worker", root)
            with patch.dict(os.environ, {"PATH": env_path}):
                first = session.run("first", AGENT_REPLY_SCHEMA, 10)
                second = session.run("second", AGENT_REPLY_SCHEMA, 10)
            self.assertEqual(first["usage"]["input_tokens"], 5)
            self.assertEqual(second["session_id"], first["session_id"])
            invocations = [json.loads(line) for line in log_path.read_text().splitlines()]
            self.assertIn("--session-id", invocations[0])
            self.assertIn("--resume", invocations[1])
            self.assertNotIn("Bash(git -C * add*)", invocations[0])
            self.assertNotIn("Bash(git add*)", invocations[0])
            self.assertNotIn("Bash(git commit*)", invocations[0])

    def test_codex_session_resumes_with_output_schema(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            log_path = root / "codex_args.jsonl"
            executable = bin_dir / "codex"
            executable.write_text(f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path
args = sys.argv[1:]
Path({str(log_path)!r}).open('a').write(json.dumps(args) + '\\n')
out = Path(args[args.index('--output-last-message') + 1])
out.write_text(json.dumps({_reply()!r}))
print(json.dumps({{'type': 'thread.started', 'thread_id': 'thread-123'}}))
print(json.dumps({{'type': 'turn.completed', 'usage': {{'input_tokens': 4, 'cached_input_tokens': 1, 'output_tokens': 2}}}}))
""", encoding="utf-8")
            executable.chmod(0o755)
            env_path = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            session = SubscriptionSession("codex", self._workspace(root), True, "worker", root)
            with patch.dict(os.environ, {"PATH": env_path}):
                first = session.run("first", AGENT_REPLY_SCHEMA, 10)
                second = session.run("second", AGENT_REPLY_SCHEMA, 10)
            self.assertEqual(first["session_id"], "thread-123")
            self.assertEqual(second["usage"]["output_tokens"], 2)
            invocations = [json.loads(line) for line in log_path.read_text().splitlines()]
            self.assertNotIn("resume", invocations[0])
            self.assertIn("resume", invocations[1])
            self.assertIn("thread-123", invocations[1])

    def test_fresh_codex_session_is_ephemeral(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            log_path = root / "codex_args.json"
            executable = bin_dir / "codex"
            executable.write_text(f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path
args = sys.argv[1:]
Path({str(log_path)!r}).write_text(json.dumps(args))
out = Path(args[args.index('--output-last-message') + 1])
out.write_text(json.dumps({_reply()!r}))
print(json.dumps({{'type': 'thread.started', 'thread_id': 'fresh-thread'}}))
""", encoding="utf-8")
            executable.chmod(0o755)
            env_path = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            session = SubscriptionSession(
                "codex", self._workspace(root), False, "verifier", root, fresh=True,
            )
            with patch.dict(os.environ, {"PATH": env_path}):
                session.run("verify", AGENT_REPLY_SCHEMA, 10)
            self.assertIn("--ephemeral", json.loads(log_path.read_text()))


class OrganizationProjectTests(unittest.TestCase):
    def test_project_memory_and_request_are_built_without_api_keys(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            context = build_project_context(root)
            self.assertIn("Organization test project", context)
            self.assertIn("docs/contract.md", context)
            with patch("reccli.organization.shutil.which", return_value="/fake/claude"):
                request = create_run_request(
                    str(root), "Ship the tested application.", provider="claude", max_rounds=3,
                )
            self.assertEqual(request["provider"], "claude")
            self.assertNotIn("api_key", request)
            self.assertTrue(Path(request["run_dir"], "status.json").exists())

    def test_start_request_rejects_tracked_uncommitted_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            (root / "app.py").write_text("print('dirty')\n", encoding="utf-8")
            with patch("reccli.organization.shutil.which", return_value="/fake/codex"):
                with self.assertRaisesRegex(RuntimeError, "clean tracked Git worktree"):
                    create_run_request(str(root), "Ship it.", provider="codex")

    def test_auto_request_persists_mixed_provider_plan(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            with patch(
                "reccli.organization.shutil.which",
                side_effect=lambda name: f"/fake/{name}" if name in {"claude", "codex"} else None,
            ), patch(
                "reccli.organization._provider_authentication_status",
                return_value="authenticated",
            ), patch.dict(os.environ, {"RECCLI_HOST": "codex"}, clear=False):
                request = create_run_request(root, "Ship it.", provider="auto")
            self.assertEqual(request["provider"], "mixed")
            self.assertEqual(request["max_rounds"], 8)
            self.assertEqual(request["host_provider"], "codex")
            self.assertEqual(request["provider_assignments"]["manager-d"], "codex")
            self.assertEqual(request["blind_verifier_provider"], "claude")
            persisted = json.loads(Path(request["run_dir"], "request.json").read_text())
            self.assertEqual(persisted["provider_assignments"], request["provider_assignments"])
            status = json.loads(Path(request["run_dir"], "status.json").read_text())
            self.assertEqual(status["max_rounds"], 8)
            self.assertEqual(status["rounds_remaining"], 8)
            self.assertEqual(status["completed_turns"], 0)

    def test_request_persists_explicit_ignored_and_external_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            _init_project(root)
            ignored = root / "out" / "receipt.json"
            ignored.parent.mkdir()
            ignored.write_text('{"accepted": true}\n', encoding="utf-8")
            external = Path(td) / "reference.step"
            external.write_bytes(b"STEP-reference")
            with patch("reccli.organization.shutil.which", return_value="/fake/claude"):
                request = create_run_request(
                    str(root), "Audit the accepted geometry.", provider="claude",
                    topology="scientific",
                    evidence_paths=["out", str(external)],
                    protected_paths=["app.py"], max_experiments=2,
                )
            self.assertEqual(
                request["evidence_paths"],
                [str(ignored.parent.resolve()), str(external.resolve())],
            )
            persisted = json.loads(Path(request["run_dir"], "request.json").read_text())
            self.assertEqual(persisted["evidence_paths"], request["evidence_paths"])
            self.assertEqual(persisted["protected_paths"], ["app.py"])
            self.assertEqual(persisted["max_experiments"], 2)

    def test_request_persists_tracked_context_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            _init_project(root)
            _add_context_manifest(root)
            with patch("reccli.organization.shutil.which", return_value="/fake/claude"):
                request = create_run_request(
                    str(root), "Qualify the pipeline.", provider="claude",
                    topology="scientific",
                    context_manifest="context-packs.json",
                )
            self.assertEqual(request["context_manifest"], "context-packs.json")
            persisted = json.loads(
                Path(request["run_dir"], "request.json").read_text()
            )
            self.assertEqual(
                persisted["context_manifest"], "context-packs.json",
            )

    def test_context_packs_route_worker_lanes_and_full_manager_union(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            _init_project(root)
            _add_context_manifest(root)
            run_dir = root / "devsession" / "agent-organizations" / "context-run"
            run_dir.mkdir(parents=True)
            manifest = prepare_context_packs(
                root, run_dir, "context-packs.json", get_topology("scientific"),
            )
            self.assertIsNotNone(manifest)
            worker = manifest["agent_packs"]["worker-a"]
            manager = manifest["agent_packs"]["manager-c"]
            worker_root = Path(worker["root"]) / "canonical"
            manager_root = Path(manager["root"]) / "canonical"
            self.assertTrue((worker_root / "docs/common.md").is_file())
            self.assertTrue((worker_root / "docs/worker-a.md").is_file())
            self.assertTrue((worker_root / "docs/library-a.md").is_file())
            self.assertFalse((worker_root / "docs/worker-b.md").exists())
            self.assertFalse((worker_root / "docs/library-b.md").exists())
            self.assertEqual(worker["scope"], "common+lane")
            self.assertIn(
                "Your project-owned lane (worker-a): Worker a lane.",
                worker["description"],
            )
            self.assertIn(
                "Project lane for worker-d: Worker d lane.",
                manager["description"],
            )
            self.assertEqual(
                worker["declared_library_paths"], ["docs/library-a.md"],
            )
            for letter in "abcd":
                self.assertTrue(
                    (manager_root / f"docs/worker-{letter}.md").is_file()
                )
                self.assertTrue(
                    (manager_root / f"docs/library-{letter}.md").is_file()
                )
            self.assertEqual(manager["scope"], "full-union")
            self.assertIn(
                "docs/worker-a.md", manager["declared_library_paths"],
            )
            self.assertIn(
                "docs/library-d.md", manager["declared_library_paths"],
            )
            self.assertEqual(
                (worker_root / "docs/worker-a.md").stat().st_mode & 0o222, 0,
            )
            verify_context_packs(manifest, full=True)
            verify_context_sources_unchanged(root, manifest, full=True)

            source = root / "docs/common.md"
            source.write_text("changed authority\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "context source"):
                verify_context_sources_unchanged(root, manifest, full=False)
            copied = worker_root / "docs/worker-a.md"
            copied.chmod(copied.stat().st_mode | 0o200)
            copied.write_text("tampered lane\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "context pack"):
                verify_context_packs(manifest, full=False)

    def test_evidence_snapshot_is_hashed_read_only_and_tamper_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            _init_project(root)
            evidence = root / "out"
            evidence.mkdir()
            source_file = evidence / "ledger.csv"
            source_file.write_text("attempt,result\nA005,accepted\n", encoding="utf-8")
            run_dir = root / "devsession" / "agent-organizations" / "snapshot-run"
            run_dir.mkdir(parents=True)
            manifest = prepare_evidence_snapshot(root, run_dir, ["out"])
            self.assertIsNotNone(manifest)
            snapshot_file = Path(manifest["sources"][0]["snapshot"]) / "ledger.csv"
            self.assertEqual(snapshot_file.read_text(), source_file.read_text())
            self.assertEqual(snapshot_file.stat().st_mode & 0o222, 0)
            verify_evidence_snapshot(manifest, full=True)
            verify_evidence_sources_unchanged(manifest, full=True)

            source_file.write_text("attempt,result\nA005,changed-original\n", encoding="utf-8")
            verify_evidence_snapshot(manifest, full=True)
            with self.assertRaisesRegex(RuntimeError, "original evidence"):
                verify_evidence_sources_unchanged(manifest, full=False)
            snapshot_file.chmod(snapshot_file.stat().st_mode | 0o200)
            snapshot_file.write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "evidence snapshot"):
                verify_evidence_snapshot(manifest, full=False)

    def test_worktree_preparation_isolates_all_agents(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            topology = get_topology("google-rotating")
            shared_evidence = root / "shared-evidence"
            shared_evidence.mkdir()
            workspaces = prepare_workspaces(
                root, topology, "test-run",
                additional_directories=[shared_evidence],
                protected_paths=["app.py"],
            )
            self.assertEqual(len(workspaces), 9)
            self.assertEqual(
                workspaces["manager-d"].branch,
                workspaces["manager-d"].integration_branch,
            )
            self.assertNotEqual(workspaces["worker-a"].cwd, workspaces["worker-b"].cwd)
            self.assertIn(shared_evidence.resolve(), workspaces["worker-a"].additional_directories)
            self.assertEqual(
                (workspaces["worker-a"].cwd / "app.py").stat().st_mode & 0o222,
                0,
            )

    def test_worktree_protection_preserves_tracked_compatibility_symlinks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            critical = root / "docs" / "Core"
            critical.mkdir(parents=True)
            authority = critical / "mathematical-foundation-v2.txt"
            authority.write_text("normative foundation\n", encoding="utf-8")
            compatibility = critical / "MATHEMATICAL FOUNDATION.md"
            compatibility.symlink_to("mathematical-foundation-v2.txt")
            subprocess.run(["git", "add", "docs/Core"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "add authority compatibility link"],
                cwd=root, check=True,
            )

            workspaces = prepare_workspaces(
                root, get_topology("scientific"), "protected-symlink-run",
                protected_paths=["docs/Core"],
            )
            worker_root = workspaces["worker-a"].cwd / "docs" / "Core"
            worker_link = worker_root / compatibility.name
            worker_authority = worker_root / authority.name
            self.assertTrue(worker_link.is_symlink())
            self.assertEqual(
                worker_link.readlink(), Path("mathematical-foundation-v2.txt"),
            )
            self.assertEqual(
                worker_link.read_text(encoding="utf-8"),
                "normative foundation\n",
            )
            self.assertEqual(worker_root.stat().st_mode & 0o222, 0)
            self.assertEqual(worker_authority.stat().st_mode & 0o222, 0)

    def test_worktree_python_bridge_uses_candidate_source_with_canonical_environment(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            _init_project(root)
            source = root / "src"
            source.mkdir()
            (source / "candidate_probe.py").write_text(
                "LOCATION = __file__\n", encoding="utf-8",
            )
            subprocess.run(
                ["git", "add", "src/candidate_probe.py"],
                cwd=root, check=True,
            )
            subprocess.run(
                ["git", "commit", "-qm", "add candidate probe"],
                cwd=root, check=True,
            )
            canonical_bin = root / ".venv" / "bin"
            canonical_bin.mkdir(parents=True)
            (canonical_bin / "python").symlink_to(Path(sys.executable))

            workspaces = prepare_workspaces(
                root, get_topology("scientific"),
                f"runtime-bridge-{Path(td).name}",
            )
            worker = workspaces["worker-a"]
            self.assertEqual(worker.runtime_paths, {".venv"})
            bridge = worker.cwd / ".venv" / "bin" / "python"
            self.assertTrue(os.access(bridge, os.X_OK))
            proc = subprocess.run(
                [
                    str(bridge), "-c",
                    "import candidate_probe; print(candidate_probe.LOCATION)",
                ],
                cwd=worker.cwd, check=True, capture_output=True, text=True,
            )
            self.assertTrue(
                Path(proc.stdout.strip()).is_relative_to(worker.cwd),
                proc.stdout,
            )

    def test_host_materializes_codex_worker_candidate_without_agent_git(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            _init_project(root)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            runner = OrganizationRunner(
                root, "Create a reversible candidate.", "codex",
                "scientific", "host-candidate", Path(td) / "run",
            )
            runner.workspaces["worker-a"] = Workspace(
                root, "worker-a", "main", root, [], base,
            )
            session = Mock()

            def edit_without_git(*_args, **_kwargs):
                (root / "app.py").write_text(
                    "print('host materialized')\n", encoding="utf-8",
                )
                reply = _reply("candidate ready")
                reply["state"] = "done"
                reply["messages"] = [{
                    "to": "manager-a", "tag": "handoff",
                    "content": "Candidate is ready for adversarial review.",
                    "candidate": HOST_CANDIDATE,
                    "workItem": "host-owned-git", "risk": "high",
                }]
                return {
                    "value": reply, "session_id": "codex-worker",
                    "usage": {},
                }

            session.run.side_effect = edit_without_git
            session.provider = "codex"
            runner.sessions["worker-a"] = session
            result = runner._run_turn(
                runner.topology.agent("worker-a"), 3,
            )
            candidate = result["reply"]["messages"][0]["candidate"]
            self.assertRegex(candidate, r"^[0-9a-f]{40}$")
            self.assertNotEqual(candidate, base)
            self.assertEqual(
                subprocess.run(
                    ["git", "show", f"{candidate}:app.py"],
                    cwd=root, check=True, capture_output=True, text=True,
                ).stdout,
                "print('host materialized')\n",
            )

    def test_host_integrates_reviewed_candidate_without_provider_git(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            _init_project(root)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            initial_branch = subprocess.run(
                ["git", "branch", "--show-current"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "switch", "-qc", "worker-a"], cwd=root, check=True,
            )
            (root / "app.py").write_text(
                "print('reviewed candidate')\n", encoding="utf-8",
            )
            subprocess.run(["git", "add", "app.py"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "reviewed candidate"],
                cwd=root, check=True,
            )
            candidate = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "switch", "-q", initial_branch],
                cwd=root, check=True,
            )

            runner = OrganizationRunner(
                root, "Integrate a reviewed candidate.", "codex",
                "scientific", "host-integration", Path(td) / "run",
            )
            runner.workspaces["manager-d"] = Workspace(
                root, initial_branch, initial_branch, root, [], base,
            )
            handoff = {
                "to": "manager-a", "tag": "handoff",
                "content": "Candidate ready.",
                "candidate": candidate, "workItem": "integration-test",
                "risk": "high",
            }
            accepted, _, review = runner.governance.process_message(
                "worker-a", handoff, 3,
            )
            self.assertTrue(accepted)
            runner.governance.record_decision(review["to"], {
                "to": "manager-a", "tag": "decision",
                "content": "NO_VETO: exact diff and tests inspected.",
                "candidate": candidate, "workItem": "integration-test",
                "risk": "high",
            })
            runner.inboxes["manager-d"] = [{
                "runId": runner.run_id, "round": 4,
                "from": "manager-a", "to": "manager-d", "tag": "handoff",
                "content": "No-veto review complete; integrate.",
                "candidate": candidate, "workItem": "integration-test",
                "risk": "high", "deliveredAt": "test",
            }]

            runner._sync_reviewed_candidates(5)
            self.assertEqual(
                (root / "app.py").read_text(encoding="utf-8"),
                "print('reviewed candidate')\n",
            )
            self.assertIn(candidate, runner.integrated_candidates)
            runner._validate_agent_write_scope(
                runner.topology.agent("manager-d"),
            )

    def test_failed_provider_turn_does_not_consume_inbox(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "failed-turn"
            runner = OrganizationRunner(
                root, "Ship the feature.", "claude", "google-rotating",
                "failed-turn", run_dir, max_rounds=2,
            )
            runner.workspaces["lead"] = Workspace(root, "main", "main", root, [])
            message = {
                "from": "manager-a", "tag": "question", "content": "Need scope.",
                "candidate": None, "workItem": None, "risk": None,
            }
            runner.inboxes["lead"] = [message]
            failed_session = Mock()
            failed_session.run.side_effect = RuntimeError("temporary provider error")
            runner.sessions["lead"] = failed_session

            with self.assertRaisesRegex(RuntimeError, "temporary provider error"):
                runner._run_turn(runner.topology.agent("lead"), 2)

            self.assertEqual(runner.inboxes["lead"], [message])

    def test_prompt_redirects_ignored_run_artifacts_to_tracked_staging(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "artifact-run"
            runner = OrganizationRunner(
                root, "Write devsession/agent-organizations/artifact-run/report.md.",
                "claude", "google-rotating", "artifact-run", run_dir,
            )
            runner.workspaces["worker-a"] = Workspace(
                root, "worker-a", "integration", root, [],
            )
            prompt = runner._build_prompt(
                runner.topology.agent("worker-a"), [], 1, True,
            )
            self.assertIn(
                ".reccli-org-artifacts/artifact-run/<path-relative-to-the-run-directory>",
                prompt,
            )
            self.assertIn("RecCli force-stages that exact prefix", prompt)
            self.assertIn("RECCLI_HOST_CANDIDATE", prompt)
            self.assertIn("Do not stage or commit it", prompt)
            self.assertIn(str(run_dir / "deliverables"), prompt)

    def test_prompt_injects_assigned_context_without_hard_read_isolation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _add_context_manifest(root)
            run_dir = root / "devsession" / "agent-organizations" / "context-prompt"
            run_dir.mkdir(parents=True)
            runner = OrganizationRunner(
                root, "Qualify the pipeline.", "claude", "scientific",
                "context-prompt", run_dir, context_manifest="context-packs.json",
            )
            runner.context_pack_manifest = prepare_context_packs(
                root, run_dir, "context-packs.json", runner.topology,
            )
            runner.workspaces["worker-a"] = Workspace(
                root, "worker-a", "integration", root,
                [Path(runner.context_pack_manifest["agent_packs"]["worker-a"]["root"])],
            )
            prompt = runner._build_prompt(
                runner.topology.agent("worker-a"), [], 1, True,
            )
            self.assertIn("## Assigned documentation context", prompt)
            self.assertIn("docs/common.md", prompt)
            self.assertIn("docs/worker-a.md", prompt)
            self.assertIn("Indexed reference library", prompt)
            self.assertIn("docs/library-a.md", prompt)
            self.assertNotIn("docs/worker-b.md", prompt)
            self.assertNotIn("docs/library-b.md", prompt)
            self.assertIn("Do not ingest every indexed library record", prompt)
            self.assertIn("not a deny-read boundary", prompt)

    def test_artifact_only_scope_rejects_source_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            run_dir = root / "devsession" / "agent-organizations" / "scope-run"
            runner = OrganizationRunner(
                root, "Publish a sandbox report.", "claude",
                "scientific", "scope-run", run_dir,
            )
            artifact_agent = AgentSpec(
                "manager-d", "artifact-only test role", "Write only the report.",
                True, "medium", "artifacts",
            )
            runner.workspaces["manager-d"] = Workspace(
                root, "main", "main", root, [], base,
            )
            session = Mock()

            def mutate_source(*_args, **_kwargs):
                (root / "app.py").write_text("print('forbidden')\n", encoding="utf-8")
                return {"value": _reply(), "session_id": "scope", "usage": {}}

            session.run.side_effect = mutate_source
            session.provider = "claude"
            runner.sessions["manager-d"] = session
            with self.assertRaisesRegex(RuntimeError, "may write only"):
                runner._run_turn(artifact_agent, 1)

    def test_artifact_only_scope_accepts_run_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            run_dir = root / "devsession" / "agent-organizations" / "scope-run"
            runner = OrganizationRunner(
                root, "Publish a sandbox report.", "claude",
                "scientific", "scope-run", run_dir,
            )
            artifact_agent = AgentSpec(
                "manager-d", "artifact-only test role", "Write only the report.",
                True, "medium", "artifacts",
            )
            runner.workspaces["manager-d"] = Workspace(
                root, "main", "main", root, [], base,
            )
            session = Mock()

            def write_artifact(*_args, **_kwargs):
                report = root / runner.artifact_staging_prefix / "takeover.md"
                report.parent.mkdir(parents=True)
                report.write_text("# Takeover\n", encoding="utf-8")
                return {"value": _reply(), "session_id": "scope", "usage": {}}

            session.run.side_effect = write_artifact
            session.provider = "claude"
            runner.sessions["manager-d"] = session
            result = runner._run_turn(artifact_agent, 1)
            self.assertEqual(result["reply"]["summary"], "ok")

    def test_integration_scope_accepts_only_a_reviewed_non_vetoed_patch_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            initial_branch = subprocess.run(
                ["git", "branch", "--show-current"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            subprocess.run(["git", "switch", "-qc", "worker-d"], cwd=root, check=True)
            (root / "app.py").write_text("print('approved experiment')\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "bounded experiment"], cwd=root, check=True)
            candidate = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            subprocess.run(["git", "switch", "-q", initial_branch], cwd=root, check=True)

            run_dir = root / "devsession" / "agent-organizations" / "integration-run"
            runner = OrganizationRunner(
                root, "Execute one bounded experiment.", "claude",
                "scientific", "integration-run", run_dir,
            )
            runner.workspaces["manager-d"] = Workspace(
                root, initial_branch, initial_branch, root, [], base,
            )
            handoff = {
                "to": "manager-b", "tag": "handoff", "content": "Ready.",
                "candidate": candidate, "workItem": "experiment-a103", "risk": "high",
            }
            accepted, _, review = runner.governance.process_message("worker-d", handoff, 1)
            self.assertTrue(accepted)
            runner.governance.record_decision(review["to"], {
                "to": "manager-b", "tag": "decision", "content": "NO_VETO: no blocking falsification found.",
                "candidate": candidate, "workItem": "experiment-a103", "risk": "high",
            })
            subprocess.run(["git", "cherry-pick", candidate], cwd=root, check=True, capture_output=True)
            runner._validate_agent_write_scope(runner.topology.agent("manager-d"))

            (root / "app.py").write_text("print('manager-authored')\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "unapproved manager edit"], cwd=root, check=True)
            with self.assertRaisesRegex(RuntimeError, "was not eligible"):
                runner._validate_agent_write_scope(runner.topology.agent("manager-d"))

    def test_generated_output_bundle_is_sealed_without_putting_cad_in_git(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "bundle-run"
            run_dir.mkdir(parents=True)
            runner = OrganizationRunner(
                root, "Execute one bounded experiment.", "claude",
                "scientific", "bundle-run", run_dir, max_experiments=1,
            )
            runner.candidate_artifact_root.mkdir()
            runner.candidate_artifact_root.chmod(0o555)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            runner.workspaces["worker-d"] = Workspace(
                root, "worker-d", "main", root, [], base,
            )
            (root / "app.py").write_text("print('experiment driver')\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "experiment driver"], cwd=root, check=True)
            candidate = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            output = root / "out" / "experiments" / "tmp-worker-d-r2"
            output.mkdir(parents=True)
            (output / "result.step").write_bytes(b"sealed-step-output")
            reply = _reply()
            reply["artifacts"] = ["out/experiments/tmp-worker-d-r2"]
            reply["messages"] = [{
                "to": "manager-b", "tag": "handoff", "content": "Temporary experiment complete.",
                "candidate": candidate, "workItem": "bundle-run/worker-d/r2", "risk": "high",
            }]

            manifest = runner._seal_reported_artifacts(
                runner.topology.agent("worker-d"), reply, 2,
            )
            self.assertEqual(manifest["candidate"], candidate)
            self.assertEqual(manifest["file_count"], 1)
            self.assertFalse(subprocess.run(
                ["git", "ls-files", "--error-unmatch", "out/experiments/tmp-worker-d-r2/result.step"],
                cwd=root, capture_output=True,
            ).returncode == 0)
            sealed = Path(manifest["bundle_root"]) / "001_tmp-worker-d-r2" / "result.step"
            self.assertEqual(sealed.read_bytes(), b"sealed-step-output")
            self.assertEqual(sealed.stat().st_mode & 0o222, 0)
            runner._verify_candidate_artifacts(full=True)
            with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
                runner._seal_reported_artifacts(
                    runner.topology.agent("worker-d"), reply, 3,
                )

    def test_protected_tracked_path_rejects_a_writable_worker_turn(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            authority = root / "authority.md"
            authority.write_text("frozen standard\n", encoding="utf-8")
            subprocess.run(["git", "add", "authority.md"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "freeze authority"], cwd=root, check=True)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            run_dir = root / "devsession" / "agent-organizations" / "protected-run"
            runner = OrganizationRunner(
                root, "Explore without changing authority.", "claude",
                "scientific", "protected-run", run_dir,
                protected_paths=["authority.md"],
            )
            runner.workspaces["worker-a"] = Workspace(
                root, "worker-a", "main", root, [], base,
            )
            session = Mock()

            def change_authority(*_args, **_kwargs):
                authority.write_text("agent changed standard\n", encoding="utf-8")
                return {"value": _reply(), "session_id": "protected", "usage": {}}

            session.run.side_effect = change_authority
            session.provider = "claude"
            runner.sessions["worker-a"] = session
            with self.assertRaisesRegex(RuntimeError, "deny-write protected"):
                runner._run_turn(runner.topology.agent("worker-a"), 1)

    def test_caller_repository_cannot_be_changed_by_an_organization(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            runner = OrganizationRunner(
                root, "Explore only in disposable worktrees.", "claude",
                "scientific", "caller-guard", root / "run",
            )
            runner.caller_head = base
            (root / "app.py").write_text("print('canonical mutation')\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "caller repository changed"):
                runner._verify_caller_repository_unchanged()

    def test_scientific_completion_writes_human_promotion_request_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            run_dir = root / "devsession" / "agent-organizations" / "promotion-run"
            run_dir.mkdir(parents=True)
            runner = OrganizationRunner(
                root, "Prepare a reversible promotion proposal.", "claude",
                "scientific", "promotion-run", run_dir,
                protected_paths=["app.py"], max_experiments=2,
            )
            runner.workspaces["manager-d"] = Workspace(
                root, "main", "main", root, [], base,
            )
            artifact_manifest = {"manifest_sha256": "artifact-manifest-hash"}
            request = runner._write_promotion_request(
                base, base, "reccli-org/scientific/proposal", artifact_manifest,
            )
            self.assertEqual(request["status"], "awaiting_human_authorization")
            self.assertFalse(request["canonical_effects_applied"])
            self.assertEqual(request["protected_paths"], ["app.py"])
            self.assertTrue((run_dir / "promotion-request.json").is_file())

    def test_promotion_candidate_removes_staging_inherited_from_prior_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            _init_project(root)
            prior = (
                root / ".reccli-org-artifacts" / "prior-run" /
                "evidence.md"
            )
            prior.parent.mkdir(parents=True)
            prior.write_text("prior reversible evidence\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "-f", ".reccli-org-artifacts"],
                cwd=root, check=True,
            )
            subprocess.run(
                ["git", "commit", "-qm", "prior run candidate"],
                cwd=root, check=True,
            )
            candidate = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            run_dir = Path(td) / "new-run"
            run_dir.mkdir()
            runner = OrganizationRunner(
                root, "Review a prior exact candidate.", "claude",
                "scientific", "new-run", run_dir,
            )
            runner.workspaces["manager-d"] = Workspace(
                root, "main", "main", root, [], candidate,
            )
            promotion, _ = runner._create_promotion_candidate(
                candidate, [], "empty-current-manifest",
            )
            self.assertNotEqual(promotion, candidate)
            tree = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", promotion],
                cwd=root, check=True, capture_output=True, text=True,
            ).stdout
            self.assertNotIn(".reccli-org-artifacts/", tree)
            self.assertIn("app.py", tree)

    def test_scientific_scheduler_does_not_treat_worker_turns_as_experiments(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root, "Explore within one experiment slot.", "claude",
                "scientific", "budget-run", root / "run", max_experiments=1,
            )
            message = {
                "from": "manager-a", "tag": "plan", "content": "Run a sandbox experiment.",
                "candidate": None, "workItem": "bounded-experiment", "risk": "high",
            }
            runner.inboxes["worker-a"] = [message]
            runner.inboxes["worker-b"] = [{
                **message,
                "from": "manager-b",
            }]
            scheduled = runner._select_agents(3)
            writers = [agent for agent in scheduled if agent.write_scope == "workspace"]
            self.assertEqual(
                {agent.agent_id for agent in writers},
                {"worker-a", "worker-b"},
            )

    def test_event_scheduler_enforces_lead_then_manager_delegation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root, "Map, delegate, then execute.", "claude",
                "scientific", "delegation-run", root / "run",
            )
            self.assertEqual(
                [agent.agent_id for agent in runner._select_agents(1)],
                ["lead"],
            )
            lead_message = {
                "from": "lead", "tag": "plan", "content": "Refine this lane.",
                "candidate": None, "workItem": "lane-a", "risk": "routine",
            }
            runner.inboxes["manager-a"] = [lead_message]
            runner.inboxes["worker-a"] = [lead_message]
            runner.states["lead"] = "working"
            self.assertEqual(
                [agent.agent_id for agent in runner._select_agents(2)],
                ["manager-a"],
            )
            manager_message = {
                "from": "manager-a", "tag": "plan",
                "content": "Execute the bounded lane.",
                "candidate": None, "workItem": "lane-a", "risk": "routine",
            }
            runner.inboxes["manager-a"] = []
            runner.inboxes["worker-a"] = [manager_message]
            scheduled = {
                agent.agent_id for agent in runner._select_agents(3)
            }
            self.assertIn("worker-a", scheduled)
            self.assertNotIn("lead", scheduled)
            runner.inboxes["lead"] = [{
                **manager_message,
                "from": "manager-a",
                "content": "Macro result and worker progress.",
            }]
            self.assertIn(
                "lead",
                {agent.agent_id for agent in runner._select_agents(3)},
            )

    def test_delegation_barrier_requires_every_lane_before_parallel_work(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root, "Map every lane before execution.", "claude",
                "scientific", "barrier-run", root / "run",
                max_experiments=4,
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "lead-to-manager.*manager-a, manager-b, manager-c, manager-d",
            ):
                runner._assert_delegation_barrier(1)

            for manager_id in runner.topology.manager_ids:
                runner.inboxes[manager_id] = [{
                    "from": "lead",
                    "to": manager_id,
                    "tag": "review" if manager_id == "manager-c" else "plan",
                    "content": "Refine the assigned lane.",
                    "candidate": None,
                    "workItem": f"map-{manager_id}",
                    "risk": "routine",
                }]
            runner._assert_delegation_barrier(1)

            with self.assertRaisesRegex(
                RuntimeError,
                "manager-to-worker.*worker-a, worker-b, worker-c, worker-d",
            ):
                runner._assert_delegation_barrier(2)

            for worker_id, manager_id in (
                runner.topology.primary_manager_by_worker.items()
            ):
                runner.inboxes[worker_id] = [{
                    "from": manager_id,
                    "to": worker_id,
                    "tag": "review" if worker_id == "worker-d" else "handoff",
                    "content": "Execute or explicitly investigate this bounded lane.",
                    "candidate": None,
                    "workItem": f"execute-{worker_id}",
                    "risk": "high",
                }]
            runner._assert_delegation_barrier(2)
            self.assertEqual(
                {
                    agent.agent_id
                    for agent in runner._select_agents(3)
                    if agent.agent_id in runner.topology.worker_ids
                },
                set(runner.topology.worker_ids),
            )


class OrganizationRunnerTests(unittest.TestCase):
    def test_scientific_run_explores_then_emits_human_promotion_request(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            authority = root / "authority.md"
            authority.write_text("human-frozen standard\n", encoding="utf-8")
            subprocess.run(["git", "add", "authority.md"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "freeze authority"], cwd=root, check=True)
            run_dir = root / "devsession" / "agent-organizations" / "scientific-run"
            runner = OrganizationRunner(
                root, "Explore one reversible hypothesis and prepare promotion evidence.",
                "claude", "scientific", "scientific-run", run_dir,
                max_rounds=9, max_experiments=1,
                protected_paths=["authority.md"],
            )
            worker_candidate = {"sha": None}
            release_candidate = {"sha": None}

            def message(to, tag, content, candidate=None, work_item=None, risk=None):
                return {
                    "to": to, "tag": tag, "content": content,
                    "candidate": candidate, "workItem": work_item, "risk": risk,
                }

            def response(messages=None, state="idle", artifacts=None, candidate=None, risk=None, final=False):
                return {
                    "messages": messages or [], "summary": "scientific simulated turn",
                    "state": state, "artifacts": artifacts or [], "candidate": candidate,
                    "risk": risk, "final": final,
                }

            def fake_run(session, prompt, schema, timeout_seconds):
                session.turn += 1
                agent_id = session.session_key
                reply = response()
                if agent_id == "lead":
                    if session.turn == 1:
                        reply = response([
                            message(
                                manager_id, "plan",
                                "Refine this bounded scientific lane and delegate an explicit worker task where you own one.",
                                None, f"scientific-map/{manager_id}", "high",
                            )
                            for manager_id in (
                                "manager-a", "manager-b", "manager-c", "manager-d",
                            )
                        ])
                    elif release_candidate["sha"] and "final-release" in prompt:
                        reply = response([message(
                            "manager-d", "decision", "APPROVED: complete reversible promotion dossier.",
                            release_candidate["sha"], "final-release", "release",
                        )])
                elif agent_id == "manager-a":
                    if session.turn == 1:
                        reply = response([
                            message(
                                "worker-a", "plan",
                                "Choose a bounded experiment; preserve its generated receipt.",
                                None, "scientific-run/worker-a/r3", "high",
                            ),
                            message(
                                "worker-c", "plan",
                                "Audit the topology predicates for the selected experiment without opening a second experiment.",
                                None, "scientific-run/worker-c/audit", "high",
                            ),
                        ])
                    elif worker_candidate["sha"] and "NO_VETO:" in prompt:
                        reply = response([message(
                            "manager-d", "handoff", "Adversarial review completed without veto; integrate sandbox patch.",
                            worker_candidate["sha"], "scientific-run/worker-a/r3", "high",
                        )])
                elif agent_id == "manager-b" and session.turn == 1:
                    reply = response([
                        message(
                            "worker-b", "plan",
                            "Research the nearest model-selection control and report a bounded recommendation.",
                            None, "scientific-run/worker-b/research", "high",
                        ),
                        message(
                            "worker-d", "plan",
                            "Audit missing-information limits and prepare an explicit uncertainty finding.",
                            None, "scientific-run/worker-d/uncertainty", "high",
                        ),
                    ])
                elif agent_id == "worker-a" and worker_candidate["sha"] is None:
                    (session.workspace.cwd / "app.py").write_text(
                        "print('reversible hypothesis')\n", encoding="utf-8",
                    )
                    subprocess.run(["git", "add", "app.py"], cwd=session.workspace.cwd, check=True)
                    subprocess.run(["git", "commit", "-qm", "sandbox experiment"], cwd=session.workspace.cwd, check=True)
                    worker_candidate["sha"] = subprocess.run(
                        ["git", "rev-parse", "HEAD"], cwd=session.workspace.cwd,
                        check=True, capture_output=True, text=True,
                    ).stdout.strip()
                    output = session.workspace.cwd / "out" / "tmp-worker-a-r3"
                    output.mkdir(parents=True)
                    (output / "receipt.json").write_text('{"result":"provisional"}\n', encoding="utf-8")
                    reply = response(
                        [message(
                            "manager-a", "handoff", "Sandbox experiment and provisional receipt ready.",
                            worker_candidate["sha"], "scientific-run/worker-a/r3", "high",
                        )],
                        state="done", artifacts=["out/tmp-worker-a-r3"],
                    )
                elif agent_id == "manager-c":
                    if release_candidate["sha"] and "final-release" in prompt:
                        reply = response([message(
                            "manager-d", "decision", "NO_VETO: dossier exposes provisional status and primary receipt.",
                            release_candidate["sha"], "final-release", "release",
                        )])
                    elif "Adversarial review assignment" in prompt:
                        reply = response([message(
                            "manager-a", "decision", "NO_VETO: no blocking falsification; no truth approval implied.",
                            worker_candidate["sha"], "scientific-run/worker-a/r3", "high",
                        )])
                elif agent_id == "manager-d":
                    if worker_candidate["sha"] and release_candidate["sha"] is None and "integrate sandbox patch" in prompt:
                        report = session.workspace.cwd / runner.artifact_staging_prefix / "promotion-dossier.md"
                        report.parent.mkdir(parents=True)
                        report.write_text("# Provisional promotion dossier\n", encoding="utf-8")
                        subprocess.run(
                            ["git", "add", runner.artifact_staging_prefix],
                            cwd=session.workspace.cwd, check=True,
                        )
                        subprocess.run(
                            ["git", "commit", "-qm", "add promotion dossier"],
                            cwd=session.workspace.cwd, check=True,
                        )
                        release_candidate["sha"] = subprocess.run(
                            ["git", "rev-parse", "HEAD"], cwd=session.workspace.cwd,
                            check=True, capture_output=True, text=True,
                        ).stdout.strip()
                        reply = response([
                            message(
                                "lead", "review", "Review the complete reversible promotion dossier.",
                                release_candidate["sha"], "final-release", "release",
                            ),
                            message(
                                "manager-c", "review", "Veto or annotate the fully-sighted final dossier.",
                                release_candidate["sha"], "final-release", "release",
                            ),
                        ], state="working")
                    elif release_candidate["sha"] and "APPROVED:" in prompt and "NO_VETO:" in prompt:
                        reply = response(
                            state="done", candidate=release_candidate["sha"],
                            risk="release", final=True,
                        )
                return {"value": reply, "session_id": f"session-{agent_id}", "usage": {}}

            worktree_parent = None
            try:
                with patch.object(SubscriptionSession, "run", new=fake_run):
                    result = runner.run()
                worktree_parent = Path(result["integration_workspace"]).parent
                self.assertEqual(result["status"], "completed")
                self.assertTrue(result["human_promotion_required"])
                self.assertFalse(result["canonical_effects_applied"])
                self.assertEqual(result["blind_review"], None)
                self.assertEqual(result["experiment_budget"]["used"], 1)
                promotion = json.loads(Path(result["promotion_request"]).read_text())
                self.assertEqual(promotion["status"], "awaiting_human_authorization")
                self.assertFalse(promotion["canonical_effects_applied"])
                self.assertEqual(promotion["protected_paths"], ["authority.md"])
                self.assertEqual(len(promotion["candidate_artifact_bundles"]), 1)
                self.assertEqual(
                    (root / "authority.md").read_text(), "human-frozen standard\n",
                )
            finally:
                if worktree_parent is not None:
                    shutil.rmtree(worktree_parent, ignore_errors=True)

    def test_google_rotating_completes_exact_candidate_release(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "system-run"
            topology = get_topology("google-rotating")
            provider_assignments = build_provider_assignments(
                topology, "claude", "codex",
            )
            evidence = root / "out" / "baseline.txt"
            evidence.parent.mkdir()
            evidence.write_text("A005 accepted\n", encoding="utf-8")
            runner = OrganizationRunner(
                root, "Change app.py to print shipped and verify it.", "mixed",
                "google-rotating", "system-run", run_dir,
                max_rounds=4, max_concurrency=5,
                provider_assignments=provider_assignments,
                host_provider="claude", blind_verifier_provider="codex",
                evidence_paths=["out"],
            )
            worker_candidate = {"sha": None}
            release_candidate = {"sha": None}
            worker_reviewer = {"id": None}
            seen_providers = {}

            def message(to, tag, content, candidate=None, work_item=None, risk=None):
                return {
                    "to": to, "tag": tag, "content": content,
                    "candidate": candidate, "workItem": work_item, "risk": risk,
                }

            def response(messages=None, state="idle", candidate=None, risk=None, final=False):
                return {
                    "messages": messages or [], "summary": "simulated turn",
                    "state": state, "artifacts": [], "candidate": candidate,
                    "risk": risk, "final": final,
                }

            def fake_run(session, prompt, schema, timeout_seconds):
                session.turn += 1
                agent_id = session.session_key
                seen_providers[agent_id] = session.provider
                if agent_id.startswith("blind-verifier-"):
                    return {
                        "value": {
                            "candidate": release_candidate["sha"],
                            "verdict": "approved", "summary": "Checks passed.",
                            "evidence": ["Exact HEAD and app.py verified."], "blockers": [],
                        },
                        "session_id": "blind", "usage": {},
                    }

                reply = response()
                if agent_id == "lead":
                    if session.turn == 1:
                        reply = response([
                            message(
                                manager_id, "plan",
                                "Refine this delivery lane and give its worker a bounded assignment.",
                                None, f"delivery-map/{manager_id}", "routine",
                            )
                            for manager_id in (
                                "manager-a", "manager-b", "manager-c", "manager-d",
                            )
                        ])
                    elif release_candidate["sha"] and "Approve exact release candidate" in prompt:
                        reply = response([
                            message(
                                "manager-d", "decision", "APPROVED: exact scope matches mission.",
                                release_candidate["sha"], "final-release", "release",
                            ),
                        ])
                elif agent_id == "manager-a":
                    if release_candidate["sha"] and "Approve exact release candidate" in prompt:
                        reply = response([
                            message(
                                "manager-d", "decision", "APPROVED: independent integrated review passes.",
                                release_candidate["sha"], "final-release", "release",
                            ),
                        ])
                    elif session.turn == 1:
                        reply = response([
                            message(
                                "worker-a", "plan",
                                "Implement and commit the app.py change.",
                                None, "app-py-delivery", "routine",
                            ),
                        ])
                    elif worker_candidate["sha"] and "APPROVED:" in prompt:
                        reply = response([
                            message(
                                "manager-d", "handoff", "Alternate review passed; integrate.",
                                worker_candidate["sha"], "app-change", "routine",
                            ),
                        ])
                elif agent_id in {"manager-b", "manager-c"} and session.turn == 1:
                    worker_id = agent_id.replace("manager", "worker")
                    reply = response([
                        message(
                            worker_id, "plan",
                            "Inspect the assigned interface and report compatibility evidence.",
                            None, f"compatibility/{worker_id}", "routine",
                        ),
                    ])
                elif agent_id == "worker-a" and worker_candidate["sha"] is None:
                    (session.workspace.cwd / "app.py").write_text("print('shipped')\n", encoding="utf-8")
                    artifact = (
                        session.workspace.cwd / runner.artifact_staging_prefix /
                        "delivery.md"
                    )
                    artifact.parent.mkdir(parents=True, exist_ok=True)
                    artifact.write_text("# Verified delivery\n", encoding="utf-8")
                    subprocess.run(
                        ["git", "add", "app.py", runner.artifact_staging_prefix],
                        cwd=session.workspace.cwd, check=True,
                    )
                    subprocess.run(
                        ["git", "commit", "-qm", "ship app change"],
                        cwd=session.workspace.cwd, check=True,
                    )
                    worker_candidate["sha"] = subprocess.run(
                        ["git", "rev-parse", "HEAD"], cwd=session.workspace.cwd,
                        check=True, capture_output=True, text=True,
                    ).stdout.strip()
                    reply = response([
                        message(
                            "manager-a", "handoff", "Implementation and focused check complete.",
                            worker_candidate["sha"], "app-change", "routine",
                        ),
                    ], state="done")
                elif agent_id == "manager-d":
                    if session.turn == 1:
                        reply = response([
                            message(
                                "worker-d", "plan",
                                "Prepare the release validation checklist and remain available for integration evidence.",
                                None, "release/worker-d", "routine",
                            ),
                        ])
                    elif worker_candidate["sha"] and release_candidate["sha"] is None and "Alternate review passed" in prompt:
                        release_candidate["sha"] = subprocess.run(
                            ["git", "rev-parse", "HEAD"], cwd=session.workspace.cwd,
                            check=True, capture_output=True, text=True,
                        ).stdout.strip()
                        final_reviewer = runner.governance.release_reviewer_id
                        reply = response([
                            message(
                                "lead", "review", "Approve exact release candidate for mission scope.",
                                release_candidate["sha"], "final-release", "release",
                            ),
                            message(
                                final_reviewer, "review", "Approve exact release candidate independently.",
                                release_candidate["sha"], "final-release", "release",
                            ),
                        ], state="working")
                    elif release_candidate["sha"] and prompt.count("APPROVED:") >= 2:
                        reply = response(
                            state="done", candidate=release_candidate["sha"],
                            risk="release", final=True,
                        )
                elif "Independent review assignment" in prompt:
                    worker_reviewer["id"] = agent_id
                    reply = response([
                        message(
                            "manager-a", "decision", "APPROVED: diff and focused evidence pass.",
                            worker_candidate["sha"], "app-change", "routine",
                        ),
                    ])
                elif release_candidate["sha"] and "Approve exact release candidate independently" in prompt:
                    reply = response([
                        message(
                            "manager-d", "decision", "APPROVED: independent integrated review passes.",
                            release_candidate["sha"], "final-release", "release",
                        ),
                    ])

                return {"value": reply, "session_id": f"session-{agent_id}", "usage": {}}

            worktree_parent = None
            try:
                with patch.object(SubscriptionSession, "run", new=fake_run):
                    result = runner.run()
                worktree_parent = Path(result["integration_workspace"]).parent
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["rounds"], 8)
                self.assertEqual(result["working_rounds"], 4)
                self.assertEqual(result["closeout_rounds"], 4)
                self.assertGreater(result["completed_turns"], 8)
                self.assertEqual(result["provider"], "mixed")
                self.assertEqual(result["provider_assignments"], provider_assignments)
                self.assertEqual(result["blind_verifier_provider"], "codex")
                self.assertEqual(result["blind_review"]["candidate"], release_candidate["sha"])
                self.assertEqual(result["verified_candidate"], release_candidate["sha"])
                self.assertTrue(Path(result["evidence_manifest"]).is_file())
                self.assertTrue(Path(result["evidence_snapshot_root"]).is_dir())
                self.assertNotEqual(result["promotion_candidate"], release_candidate["sha"])
                self.assertTrue(result["promotion_branch"].endswith("-promotion"))
                self.assertEqual(
                    (run_dir / "deliverables" / "delivery.md").read_text(),
                    "# Verified delivery\n",
                )
                manifest = json.loads((run_dir / "artifact-manifest.json").read_text())
                self.assertEqual(manifest["verified_candidate"], release_candidate["sha"])
                self.assertEqual(manifest["files"][0]["path"], "deliverables/delivery.md")
                promotion_tree = subprocess.run(
                    ["git", "ls-tree", "-r", "--name-only", result["promotion_candidate"]],
                    cwd=root, check=True, capture_output=True, text=True,
                ).stdout
                self.assertNotIn(runner.artifact_staging_prefix, promotion_tree)
                self.assertEqual(
                    (Path(result["integration_workspace"]) / "app.py").read_text(),
                    "print('shipped')\n",
                )
                self.assertNotIn(
                    worker_reviewer["id"], {"manager-a", "manager-d"},
                )
                self.assertNotEqual(
                    seen_providers[worker_reviewer["id"]],
                    seen_providers["worker-a"],
                )
                self.assertEqual(seen_providers["manager-d"], "claude")
                self.assertEqual(
                    seen_providers[f"blind-verifier-{release_candidate['sha']}"],
                    "codex",
                )
            finally:
                if worktree_parent is not None:
                    shutil.rmtree(worktree_parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
