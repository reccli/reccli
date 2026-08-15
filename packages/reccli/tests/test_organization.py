"""Offline tests for the subscription-backed organization MCP engine."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path
from unittest.mock import Mock, patch

from reccli import organization as organization_module
from reccli.organization import (
    AGENT_REPLY_SCHEMA,
    HOST_CANDIDATE,
    RUN_CONCLUSION_SCHEMA,
    AgentSpec,
    Governance,
    OrganizationRunner,
    SubscriptionSession,
    Workspace,
    build_provider_assignments,
    create_run_request as _admission_gated_create_run_request,
    get_topology,
    prepare_context_packs,
    prepare_evidence_snapshot,
    prepare_workspaces,
    resolve_experiment_policy,
    resolve_provider_plan,
    validate_agent_reply,
    verify_context_packs,
    verify_context_sources_unchanged,
    verify_evidence_sources_unchanged,
    verify_evidence_snapshot,
)
from reccli.organization_worker import main as organization_worker_main


VALID_ADMISSION = {
    "consumer": {
        "name": "will",
        "type": "human",
        "intended_use": "merge and ship the reviewed fix",
    },
    "work_class": "deployable_artifact",
    "done_condition": (
        "the reviewed candidate is merged-ready with the suite passing"
    ),
    "stop_conditions": [
        "the evaluator shows no improvement after two contracts",
    ],
}


def create_run_request(*args, **kwargs):
    """Launch-surface wrapper: tests exercise the gate explicitly elsewhere."""
    kwargs.setdefault("admission", VALID_ADMISSION)
    return _admission_gated_create_run_request(*args, **kwargs)


def _reply(summary="ok"):
    return {
        "messages": [],
        "summary": summary,
        "state": "working",
        "artifacts": [],
        "candidate": None,
        "risk": None,
        "disposition": "continue",
        "final": False,
    }


def _conclusion(summary="The bounded run produced a useful result."):
    return {
        "summary": summary,
        "accomplishments": ["Reproduced the declared control."],
        "conclusive_findings": ["The earliest failing layer is deterministic."],
        "evidence_and_tests": ["T1000 passed on the exact candidate."],
        "scientific_or_product_blockers": ["T1001 remains unresolved."],
        "infrastructure_failures": [],
        "unresolved": ["The candidate still needs human review."],
        "promotion_readiness": "not_ready",
        "next_action": "Review the exact failing receipt.",
        "limitations": ["No canonical effects were applied."],
        "proposed_successor_admission": None,
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


def _add_context_manifest(
    root: Path, *, lane_paths_mode: str | None = None,
) -> Path:
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
    definition = {
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
        "full_context_agents": ["lead"],
    }
    if lane_paths_mode is not None:
        definition["lane_paths_mode"] = lane_paths_mode
    manifest.write_text(
        json.dumps(definition, indent=2) + "\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "add", "docs", "context-packs.json"], cwd=root, check=True,
    )
    subprocess.run(
        ["git", "commit", "-qm", "add context packs"], cwd=root, check=True,
    )
    return manifest


def _add_experiment_policy(
    root: Path,
    *,
    require_goal_progress: bool = False,
) -> Path:
    evaluator = root / "evaluator.py"
    evaluator.write_text(
        "from pathlib import Path\n"
        "raise SystemExit(0 if \"improved\" in "
        "Path(\"app.py\").read_text() else 1)\n",
        encoding="utf-8",
    )
    policy = root / "experiment-policy.json"
    definition = {
        "schema": "reccli.organization-experiment-policy.v1",
        "enabled": True,
        "max_trials_per_contract": 3,
        "max_consecutive_non_improving": 2,
        "max_contract_wall_seconds": 300,
        "evaluators": [{
            "id": "app-regression",
            "commands": [{
                "argv": ["python3", "evaluator.py"],
                "timeout_seconds": 30,
            }],
            "immutable_paths": ["evaluator.py"],
            "mutable_roots": ["app.py"],
            "result_mode": "command_exit",
            "goal_success_rule": (
                "Make the immutable evaluator change from failing to passing."
                if require_goal_progress else ""
            ),
            "hard_gates": [],
            "metrics": [],
            "predicates": (
                [{
                    "id": "app-output-passes",
                    "goal_class": "production_pipeline",
                    "source": "commands_pass",
                    "result_id": None,
                    "comparison_rule_id": "false_to_true",
                }]
                if require_goal_progress else []
            ),
            "resource_limits": {
                "max_threads": 1,
                "same_host_required": True,
            },
            "change_limits": {
                "max_changed_lines": 20,
                "max_diff_hunks": 4,
            },
        }],
    }
    if require_goal_progress:
        definition["promotion_requires_goal_progress"] = True
    policy.write_text(
        json.dumps(definition, indent=2) + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "evaluator.py", "experiment-policy.json"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-qm", "add experiment policy"],
        cwd=root,
        check=True,
    )
    return policy


class OrganizationTopologyTests(unittest.TestCase):
    def test_reply_validation_rejects_protocol_drift(self):
        self.assertEqual(validate_agent_reply(_reply())["summary"], "ok")
        pending = _reply()
        pending.update({
            "disposition": "pending_human",
            "final": True,
            "candidate": "abc123",
            "risk": "release",
        })
        self.assertEqual(
            validate_agent_reply(pending)["disposition"],
            "pending_human",
        )
        invalid = _reply()
        invalid["extra"] = True
        with self.assertRaisesRegex(ValueError, "fields must be exactly"):
            validate_agent_reply(invalid)

    def test_flat_review_is_fully_sighted_veto_not_truth_approval(self):
        topology = get_topology("flat")
        governance = Governance(topology, "flat-run")
        release_reviewer = governance.release_reviewer_id
        self.assertIn(release_reviewer, topology.final_reviewer_pool)
        non_release_auditor = next(
            auditor for auditor in topology.final_reviewer_pool
            if auditor != release_reviewer
        )
        handoff = {
            "to": "lead", "tag": "handoff", "content": "Sandbox result ready.",
            "candidate": "candidate-1", "workItem": "tmp-hypothesis", "risk": "high",
        }
        accepted, _, review = governance.process_message("worker-a", handoff, 1)
        self.assertTrue(accepted)
        self.assertEqual(review["to"], non_release_auditor)
        self.assertIn("NO_VETO", review["content"])

        governance.record_decision(non_release_auditor, {
            "to": "lead", "tag": "decision",
            "content": "NO_VETO: no blocking falsification; visual meaning remains human judgment.",
            "candidate": "candidate-1", "workItem": "tmp-hypothesis", "risk": "high",
        })
        self.assertEqual(governance.assignments["candidate-1"]["status"], "reviewed")

        governance.record_decision(release_reviewer, {
            "to": "lead", "tag": "decision",
            "content": "BLOCKED: primary receipt contradicts the claimed sign.",
            "candidate": "release-1", "workItem": "final-release", "risk": "release",
        })
        self.assertIn(
            release_reviewer, governance.missing_final_approvers("release-1"),
        )

    def test_flat_candidate_review_assigns_the_non_release_auditor(self):
        topology = get_topology("flat")
        governance = Governance(topology, "flat-rotation")
        non_release_auditor = next(
            auditor for auditor in topology.final_reviewer_pool
            if auditor != governance.release_reviewer_id
        )
        reviewers = []
        for index, worker in enumerate(("worker-a", "worker-b")):
            accepted, _, review = governance.process_message(worker, {
                "to": "lead",
                "tag": "handoff",
                "content": "Candidate ready.",
                "candidate": f"candidate-{index}",
                "workItem": f"work-{index}",
                "risk": "high",
            }, index + 1)
            self.assertTrue(accepted)
            reviewers.append(review["to"])
        self.assertEqual(set(reviewers), {non_release_auditor})
        self.assertNotIn(governance.release_reviewer_id, reviewers)

    def test_flat_topology_grants_reversible_worker_agency(self):
        topology = get_topology("flat")
        scopes = {agent.agent_id: agent.write_scope for agent in topology.agents}
        web_research = {
            agent.agent_id for agent in topology.agents if agent.web_research
        }
        self.assertEqual(scopes["lead"], "integration")
        self.assertEqual(
            {agent_id for agent_id, scope in scopes.items() if scope == "workspace"},
            {f"worker-{letter}" for letter in "abcdef"},
        )
        self.assertEqual(scopes["auditor-a"], "none")
        self.assertEqual(scopes["auditor-b"], "none")
        self.assertEqual(web_research, {"lead", "auditor-a", "auditor-b"})
        self.assertTrue(topology.can_route("worker-a", "lead", "question")[0])
        self.assertFalse(topology.can_route("worker-a", "worker-b", "question")[0])
        self.assertTrue(topology.can_route("auditor-a", "worker-a", "question")[0])
        self.assertEqual(topology.review_policy, "veto")
        self.assertTrue(topology.human_promotion_required)
        self.assertTrue(topology.blind_final_review)
        self.assertEqual(topology.leader_id, "lead")
        self.assertEqual(topology.finalizer_id, "lead")
        self.assertIsNone(topology.release_manager_id)
        self.assertEqual(topology.manager_ids, [])
        self.assertEqual(
            topology.final_reviewer_pool, ["auditor-a", "auditor-b"],
        )

    def test_flat_role_slots_are_project_neutral(self):
        topology = get_topology("flat")
        roles = {agent.agent_id: agent.role for agent in topology.agents}
        self.assertEqual(roles["lead"], "coordinator")
        for worker_id in topology.worker_ids:
            self.assertEqual(roles[worker_id], "worker")
        self.assertEqual(roles["auditor-a"], "independent auditor")
        self.assertEqual(roles["auditor-b"], "independent auditor")
        role_contract = "\n".join(
            f"{agent.role}\n{agent.instructions}" for agent in topology.agents
        ).lower()
        for project_term in (
            "cad", "geometry", "scan", "surface-family", "step readback",
            "rescan",
        ):
            self.assertNotIn(project_term, role_contract)

    def test_legacy_topology_names_alias_to_flat_and_unknown_names_raise(self):
        for legacy in ("google", "google-rotating", "scientific"):
            self.assertEqual(get_topology(legacy).topology_id, "flat")
        with self.assertRaisesRegex(ValueError, "flat"):
            get_topology("scientific-takeover")


class ProviderPlanTests(unittest.TestCase):
    @staticmethod
    def _which(name):
        return f"/fake/{name}" if name in {"claude", "codex"} else None

    def test_auto_mixes_two_authenticated_native_clis(self):
        topology = get_topology("flat")
        with patch("reccli.organization.shutil.which", side_effect=self._which), patch(
            "reccli.organization._provider_authentication_status", return_value="authenticated",
        ), patch.dict(os.environ, {"RECCLI_HOST": "claude"}, clear=False):
            plan = resolve_provider_plan("auto", topology)
        self.assertEqual(plan.mode, "mixed")
        self.assertEqual(plan.host_provider, "claude")
        self.assertEqual(plan.provider_assignments["lead"], "claude")
        self.assertEqual(plan.provider_assignments["worker-a"], "codex")
        self.assertEqual(plan.blind_verifier_provider, "codex")

    def test_auto_falls_back_when_only_one_cli_is_usable(self):
        topology = get_topology("flat")
        statuses = {"claude": "authenticated", "codex": "not_authenticated"}
        with patch("reccli.organization.shutil.which", side_effect=self._which), patch(
            "reccli.organization._provider_authentication_status",
            side_effect=lambda name: statuses[name],
        ), patch.dict(os.environ, {"RECCLI_HOST": "claude"}, clear=False):
            plan = resolve_provider_plan("auto", topology)
        self.assertEqual(plan.mode, "claude")
        self.assertEqual(set(plan.provider_assignments.values()), {"claude"})

    def test_explicit_mixed_rejects_missing_subscription_auth(self):
        topology = get_topology("flat")
        statuses = {"claude": "authenticated", "codex": "not_authenticated"}
        with patch("reccli.organization.shutil.which", side_effect=self._which), patch(
            "reccli.organization._provider_authentication_status",
            side_effect=lambda name: statuses[name],
        ):
            with self.assertRaisesRegex(RuntimeError, "authenticated claude and codex"):
                resolve_provider_plan("mixed", topology)

    def test_explicit_provider_remains_homogeneous(self):
        topology = get_topology("flat")
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
print(json.dumps({{'type': 'assistant', 'message': {{'content': [{{'type': 'tool_use', 'id': 'read-1', 'name': 'Read', 'input': {{'file_path': 'docs/Core/Critical/mathematical-foundation-v2.txt'}}}}]}}}}))
print(json.dumps({{'type': 'result', 'is_error': False, 'session_id': sid, 'structured_output': {_reply()!r}, 'usage': {{'input_tokens': 2, 'cache_read_input_tokens': 3, 'output_tokens': 1}}}}))
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
            activity = [
                json.loads(line)
                for line in (root / "activity.jsonl").read_text().splitlines()
            ]
            reads = [entry for entry in activity if entry["type"] == "read"]
            self.assertEqual(len(reads), 2)
            self.assertEqual(
                reads[0]["paths"],
                ["docs/Core/Critical/mathematical-foundation-v2.txt"],
            )
            self.assertNotIn("structured_output", json.dumps(activity))
            persisted_stdout = (
                root / "worker_turn_001_stdout.txt"
            ).read_text()
            self.assertIn('"type": "result"', persisted_stdout)
            self.assertNotIn("tool_use", persisted_stdout)
            self.assertNotIn("thinking", persisted_stdout)

    def test_claude_read_only_reviewer_can_run_verification_without_edit_tools(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            log_path = root / "claude_args.json"
            executable = bin_dir / "claude"
            executable.write_text(f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path
args = sys.argv[1:]
sid = args[args.index('--session-id') + 1]
Path({str(log_path)!r}).write_text(json.dumps(args))
print(json.dumps({{'type': 'result', 'is_error': False, 'session_id': sid, 'structured_output': {_reply()!r}, 'usage': {{}}}}))
""", encoding="utf-8")
            executable.chmod(0o755)
            env_path = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            session = SubscriptionSession(
                "claude", self._workspace(root), False, "auditor", root,
                web_research=True,
            )
            with patch.dict(os.environ, {"PATH": env_path}):
                session.run("verify", AGENT_REPLY_SCHEMA, 10)
            invocation = json.loads(log_path.read_text(encoding="utf-8"))
            mode_index = invocation.index("--permission-mode")
            self.assertEqual(invocation[mode_index + 1], "dontAsk")
            self.assertIn("--allowedTools", invocation)
            self.assertIn("Read", invocation)
            self.assertIn("Glob", invocation)
            self.assertIn("Grep", invocation)
            self.assertIn("Bash(.venv/bin/python -m pytest*)", invocation)
            self.assertIn(
                "Bash(.venv/bin/python scripts/* --check*)", invocation,
            )
            self.assertIn("WebSearch", invocation)
            self.assertIn("WebFetch", invocation)
            disallowed_index = invocation.index("--disallowedTools")
            self.assertEqual(
                invocation[disallowed_index + 1:disallowed_index + 4],
                ["Edit", "Write", "NotebookEdit"],
            )

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
print(json.dumps({{'type': 'item.started', 'item': {{'id': 'cmd-1', 'type': 'command_execution', 'command': 'API_KEY=supersecret .venv/bin/python -m pytest -q tests/test_fit.py', 'status': 'in_progress', 'exit_code': None}}}}))
print(json.dumps({{'type': 'item.completed', 'item': {{'id': 'cmd-1', 'type': 'command_execution', 'command': 'API_KEY=supersecret .venv/bin/python -m pytest -q tests/test_fit.py', 'status': 'completed', 'exit_code': 0}}}}))
print(json.dumps({{'type': 'item.started', 'item': {{'id': 'read-1', 'type': 'command_execution', 'command': \"/bin/zsh -lc 'sed -n 1,20p docs/Core/Critical/mathematical-foundation-v2.txt'\", 'status': 'in_progress', 'exit_code': None}}}}))
print(json.dumps({{'type': 'item.started', 'item': {{'id': 'search-1', 'type': 'command_execution', 'command': 'rg -n eigenpair src tests', 'status': 'in_progress', 'exit_code': None}}}}))
print(json.dumps({{'type': 'item.started', 'item': {{'id': 'web-1', 'type': 'web_search', 'query': 'generalized eigenvalue numerical conditioning original paper', 'status': 'in_progress'}}}}))
print(json.dumps({{'type': 'item.started', 'item': {{'id': 'git-1', 'type': 'command_execution', 'command': 'git log --oneline -5', 'status': 'in_progress', 'exit_code': None}}}}))
print(json.dumps({{'type': 'item.started', 'item': {{'id': 'edit-1', 'type': 'file_change', 'changes': [{{'path': str(Path.cwd() / 'src' / 'fit.py'), 'kind': 'update'}}], 'status': 'in_progress'}}}}))
print(json.dumps({{'type': 'turn.completed', 'usage': {{'input_tokens': 4, 'cached_input_tokens': 1, 'output_tokens': 2}}}}))
""", encoding="utf-8")
            executable.chmod(0o755)
            env_path = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            session = SubscriptionSession(
                "codex", self._workspace(root), True, "manager", root,
                web_research=True,
            )
            with patch.dict(os.environ, {"PATH": env_path}):
                first = session.run("first", AGENT_REPLY_SCHEMA, 10)
                second = session.run("second", AGENT_REPLY_SCHEMA, 10)
            self.assertEqual(first["session_id"], "thread-123")
            self.assertEqual(second["usage"]["output_tokens"], 2)
            invocations = [json.loads(line) for line in log_path.read_text().splitlines()]
            self.assertNotIn("resume", invocations[0])
            self.assertIn("resume", invocations[1])
            self.assertIn("thread-123", invocations[1])
            self.assertIn("--search", invocations[0])
            self.assertIn("--search", invocations[1])
            self.assertLess(invocations[0].index("--search"), invocations[0].index("exec"))
            self.assertLess(invocations[1].index("--search"), invocations[1].index("exec"))
            activity_text = (root / "activity.jsonl").read_text()
            activity = [json.loads(line) for line in activity_text.splitlines()]
            self.assertTrue(any(entry["type"] == "test" for entry in activity))
            self.assertTrue(any(entry["type"] == "edit" for entry in activity))
            self.assertTrue(any(entry["type"] == "read" for entry in activity))
            self.assertTrue(any(entry["type"] == "search" for entry in activity))
            self.assertTrue(any(entry["type"] == "git" for entry in activity))
            self.assertTrue(any(entry["type"] == "web" for entry in activity))
            self.assertTrue(any(
                "docs/Core/Critical/mathematical-foundation-v2.txt"
                in entry["content"]
                for entry in activity
                if entry["type"] == "read"
            ))
            self.assertNotIn("supersecret", activity_text)

    def test_reply_disposition_records_waiting_without_model_prose(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            session = SubscriptionSession(
                "codex", self._workspace(root), True, "worker-a", root,
            )
            session.record_reply_disposition({
                "state": "blocked",
                "messages": [{
                    "to": "manager-a",
                    "tag": "blocker",
                    "content": "Long scientific rationale must not be copied.",
                }],
            })
            activity = json.loads((root / "activity.jsonl").read_text())
            self.assertEqual(activity["type"], "waiting")
            self.assertEqual(activity["content"], "Blocked; waiting on manager-a")
            self.assertNotIn("scientific rationale", json.dumps(activity))

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
                "codex", self._workspace(root), False, "verifier", root,
                reasoning="minimal", fresh=True,
            )
            with patch.dict(os.environ, {"PATH": env_path}):
                session.run("verify", AGENT_REPLY_SCHEMA, 10)
            invocation = json.loads(log_path.read_text())
            self.assertIn("--ephemeral", invocation)
            self.assertIn('model_reasoning_effort="low"', invocation)


class OrganizationProjectTests(unittest.TestCase):
    def test_supervisor_failure_writes_emergency_conclusion(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "failed-run"
            run_dir.mkdir(parents=True)
            request_path = run_dir / "request.json"
            request_path.write_text(json.dumps({
                "run_id": "failed-run",
                "run_dir": str(run_dir),
                "project_root": str(root),
                "provider": "claude",
                "provider_assignments": {"lead": "claude"},
                "topology": "flat",
                "max_experiments": 3,
            }), encoding="utf-8")
            (run_dir / "status.json").write_text(json.dumps({
                "run_id": "failed-run",
                "status": "starting",
                "round": 0,
                "completed_turns": 0,
                "attempted_turns": 0,
                "failed_turns": 0,
            }), encoding="utf-8")
            with patch.object(
                sys,
                "argv",
                ["reccli.organization_worker", str(request_path)],
            ), patch(
                "reccli.organization_worker.run_request",
                side_effect=RuntimeError("mechanical failure"),
            ):
                exit_code = organization_worker_main()

            self.assertEqual(exit_code, 1)
            conclusion = json.loads(
                (run_dir / "run-conclusion.json").read_text(encoding="utf-8")
            )
            self.assertEqual(conclusion["terminal_status"], "failed")
            self.assertEqual(conclusion["generated_by"], "host-fallback")
            self.assertIn(
                "mechanical failure",
                conclusion["infrastructure_failures"][0],
            )
            status = json.loads(
                (run_dir / "status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                status["conclusion"]["summary"],
                conclusion["summary"],
            )

    def test_request_is_built_without_api_keys(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            with patch("reccli.organization.shutil.which", return_value="/fake/claude"):
                request = create_run_request(
                    str(root), "Ship the tested application.", provider="claude", max_rounds=3,
                )
            self.assertEqual(request["provider"], "claude")
            self.assertNotIn("api_key", request)
            self.assertTrue(Path(request["run_dir"], "status.json").exists())

    def test_continuation_identity_is_validated_and_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            conclusion_sha = "a" * 64
            with patch(
                "reccli.organization.shutil.which",
                return_value="/fake/claude",
            ):
                request = create_run_request(
                    str(root),
                    "Continue from the exact terminal conclusion.",
                    provider="claude",
                    continuation_from_run_id="parent-run",
                    continuation_conclusion_sha256=conclusion_sha,
                    mission_origin="terminal-conclusion",
                )
            self.assertEqual(
                request["continuation_from_run_id"],
                "parent-run",
            )
            self.assertEqual(
                request["continuation_conclusion_sha256"],
                conclusion_sha,
            )
            self.assertEqual(
                request["mission_origin"],
                "terminal-conclusion",
            )
            persisted = json.loads(
                Path(request["run_dir"], "request.json").read_text(
                    encoding="utf-8",
                )
            )
            self.assertEqual(
                persisted["continuation_from_run_id"],
                "parent-run",
            )

    def test_partial_continuation_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            with self.assertRaisesRegex(ValueError, "supplied together"):
                create_run_request(
                    str(root),
                    "Continue.",
                    provider="claude",
                    continuation_from_run_id="parent-run",
                )

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
            self.assertEqual(request["provider_assignments"]["lead"], "codex")
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
                    topology="flat",
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
                    topology="flat",
                    context_manifest="context-packs.json",
                )
            self.assertEqual(request["context_manifest"], "context-packs.json")
            persisted = json.loads(
                Path(request["run_dir"], "request.json").read_text()
            )
            self.assertEqual(
                persisted["context_manifest"], "context-packs.json",
            )

    def test_request_persists_validated_experiment_policy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            _init_project(root)
            _add_experiment_policy(root)
            with patch(
                "reccli.organization.shutil.which",
                return_value="/fake/claude",
            ):
                request = create_run_request(
                    str(root),
                    "Optimize the bounded evaluator target.",
                    provider="claude",
                    topology="flat",
                    experiment_policy="experiment-policy.json",
                )
            self.assertEqual(
                request["experiment_policy"],
                "experiment-policy.json",
            )
            self.assertEqual(
                resolve_experiment_policy(
                    root,
                    "experiment-policy.json",
                ),
                root.resolve() / "experiment-policy.json",
            )
            self.assertIn(
                "experiment-policy.json",
                request["protected_paths"],
            )
            self.assertIn("evaluator.py", request["protected_paths"])

    def test_context_packs_route_worker_lanes_and_full_lead_union(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            _init_project(root)
            _add_context_manifest(root)
            run_dir = root / "devsession" / "agent-organizations" / "context-run"
            run_dir.mkdir(parents=True)
            manifest = prepare_context_packs(
                root, run_dir, "context-packs.json", get_topology("flat"),
            )
            self.assertIsNotNone(manifest)
            worker = manifest["agent_packs"]["worker-a"]
            manager = manifest["agent_packs"]["lead"]
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

    def test_context_packs_can_route_worker_lane_reading_on_demand(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            _init_project(root)
            _add_context_manifest(root, lane_paths_mode="on_demand")
            run_dir = root / "devsession" / "agent-organizations" / "context-jit"
            run_dir.mkdir(parents=True)
            manifest = prepare_context_packs(
                root, run_dir, "context-packs.json", get_topology("flat"),
            )

            worker = manifest["agent_packs"]["worker-a"]
            self.assertEqual(worker["declared_reading_paths"], ["docs/common.md"])
            self.assertEqual(
                worker["declared_library_paths"],
                ["docs/worker-a.md", "docs/library-a.md"],
            )
            self.assertEqual(worker["lane_paths_mode"], "on_demand")
            canonical = Path(worker["root"]) / "canonical"
            self.assertTrue((canonical / "docs/worker-a.md").is_file())
            self.assertTrue((canonical / "docs/library-a.md").is_file())

            runner = OrganizationRunner(
                root, "Qualify the pipeline.", "claude", "flat",
                "context-jit", run_dir, context_manifest="context-packs.json",
            )
            runner.context_pack_manifest = manifest
            runner.workspaces["worker-a"] = Workspace(
                root, "worker-a", "integration", root,
                [Path(worker["root"])],
            )
            prompt = runner._build_prompt(
                runner.topology.agent("worker-a"), [], 1, True,
            )
            self.assertIn("Retrieve non-Critical entries through the index", prompt)
            required = prompt.split("Indexed reference library", 1)[0]
            self.assertNotIn("docs/worker-a.md", required)

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
            topology = get_topology("flat")
            shared_evidence = root / "shared-evidence"
            shared_evidence.mkdir()
            workspaces = prepare_workspaces(
                root, topology, "test-run",
                additional_directories=[shared_evidence],
                protected_paths=["app.py"],
            )
            self.assertEqual(len(workspaces), 9)
            self.assertEqual(
                workspaces["lead"].branch,
                workspaces["lead"].integration_branch,
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
                root, get_topology("flat"), "protected-symlink-run",
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
            canonical_environment = root / ".venv"
            subprocess.run(
                [
                    sys.executable, "-m", "venv", "--without-pip",
                    str(canonical_environment),
                ],
                check=True,
            )
            canonical_python = canonical_environment / "bin" / "python"
            site_packages = Path(subprocess.run(
                [
                    str(canonical_python), "-c",
                    "import sysconfig; print(sysconfig.get_path('purelib'))",
                ],
                check=True, capture_output=True, text=True,
            ).stdout.strip())
            (site_packages / "canonical_environment_probe.py").write_text(
                "VALUE = 'from-canonical-venv'\n", encoding="utf-8",
            )

            workspaces = prepare_workspaces(
                root, get_topology("flat"),
                f"runtime-bridge-{Path(td).name}",
            )
            worker = workspaces["worker-a"]
            self.assertEqual(worker.runtime_paths, {".venv"})
            worker.environment.update({
                "RECCLI_EVIDENCE_MANIFEST": "/run/evidence-manifest.json",
                "RECCLI_EVIDENCE_SNAPSHOT_ROOT": "/run/evidence-snapshot",
            })
            environment = SubscriptionSession(
                "codex", worker, True, "runtime-bridge", Path(td),
            )._process_environment()
            self.assertEqual(
                environment["RECCLI_EVIDENCE_MANIFEST"],
                "/run/evidence-manifest.json",
            )
            self.assertEqual(
                environment["RECCLI_EVIDENCE_SNAPSHOT_ROOT"],
                "/run/evidence-snapshot",
            )
            bridge = worker.cwd / ".venv" / "bin" / "python"
            self.assertTrue(os.access(bridge, os.X_OK))
            proc = subprocess.run(
                [
                    str(bridge), "-c",
                    "import candidate_probe, canonical_environment_probe; "
                    "print(candidate_probe.LOCATION); "
                    "print(canonical_environment_probe.VALUE)",
                ],
                cwd=worker.cwd, check=True, capture_output=True, text=True,
            )
            output = proc.stdout.splitlines()
            self.assertTrue(
                Path(output[0]).is_relative_to(worker.cwd),
                proc.stdout,
            )
            self.assertEqual(output[1], "from-canonical-venv")

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
                "flat", "host-candidate", Path(td) / "run",
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
                    "to": "lead", "tag": "handoff",
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

    def test_host_materialization_ignores_runtime_bridge_and_stages_explicit_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            _init_project(root)
            (root / ".gitignore").write_text(".venv/\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", ".gitignore"], cwd=root, check=True,
            )
            subprocess.run(
                ["git", "commit", "-qm", "ignore runtime"],
                cwd=root, check=True,
            )
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            bridge = root / ".venv" / "bin"
            bridge.mkdir(parents=True)
            (bridge / "python").symlink_to(Path(sys.executable))
            runner = OrganizationRunner(
                root, "Create a reversible candidate.", "codex",
                "flat", "runtime-safe-candidate", Path(td) / "run",
            )
            runner.workspaces["worker-a"] = Workspace(
                root, "worker-a", "main", root, [], base, {".venv"},
            )
            session = Mock()

            def edit_without_git(*_args, **_kwargs):
                (root / "app.py").write_text(
                    "print('runtime-safe')\n", encoding="utf-8",
                )
                reply = _reply("candidate ready")
                reply["messages"] = [{
                    "to": "lead", "tag": "handoff",
                    "content": "Review the runtime-safe candidate.",
                    "candidate": HOST_CANDIDATE,
                    "workItem": "runtime-safe", "risk": "high",
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
            changed = subprocess.run(
                ["git", "diff-tree", "--no-commit-id", "--name-only", "-r",
                 candidate],
                cwd=root, check=True, capture_output=True, text=True,
            ).stdout.splitlines()
            self.assertEqual(changed, ["app.py"])
            self.assertNotIn(".venv", "\n".join(changed))

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
                "flat", "host-integration", Path(td) / "run",
            )
            runner.workspaces["lead"] = Workspace(
                root, initial_branch, initial_branch, root, [], base,
            )
            handoff = {
                "to": "lead", "tag": "handoff",
                "content": "Candidate ready.",
                "candidate": candidate, "workItem": "integration-test",
                "risk": "high",
            }
            accepted, _, review = runner.governance.process_message(
                "worker-a", handoff, 3,
            )
            self.assertTrue(accepted)
            self.assertNotEqual(
                review["to"], runner.governance.release_reviewer_id,
            )
            runner.governance.record_decision(review["to"], {
                "to": "lead", "tag": "decision",
                "content": "NO_VETO: exact diff and tests inspected.",
                "candidate": candidate, "workItem": "integration-test",
                "risk": "high",
            })
            # The worker's supervisor IS the finalizer in a flat topology, so
            # host integration accepts the reviewed handoff directly from the
            # worker; there is no manager relay.
            runner.inboxes["lead"] = [{
                "runId": runner.run_id, "round": 4,
                "from": "worker-a", "to": "lead", "tag": "handoff",
                "content": "Candidate ready.",
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
                runner.topology.agent("lead"),
            )

    def test_failed_provider_turn_does_not_consume_inbox(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "failed-turn"
            runner = OrganizationRunner(
                root, "Ship the feature.", "claude", "flat",
                "failed-turn", run_dir, max_rounds=2,
            )
            runner.workspaces["lead"] = Workspace(root, "main", "main", root, [])
            message = {
                "from": "worker-a", "tag": "question", "content": "Need scope.",
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
                "claude", "flat", "artifact-run", run_dir,
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
            self.assertIn("RECCLI_HOST_CANDIDATE", prompt)
            self.assertIn("Do not run git add, commit", prompt)
            self.assertNotIn("RecCli force-stages that exact prefix", prompt)

    def test_lead_prompt_bounds_external_research_and_workers_get_none(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "web-policy"
            runner = OrganizationRunner(
                root, "Resolve the documented failure.", "claude",
                "flat", "web-policy", run_dir,
            )
            runner.workspaces["lead"] = Workspace(
                root, "lead", "integration", root, [],
            )
            runner.workspaces["worker-b"] = Workspace(
                root, "worker-b", "integration", root, [],
            )

            lead_prompt = runner._build_prompt(
                runner.topology.agent("lead"), [], 1, True,
            )
            self.assertIn("External research is available", lead_prompt)
            self.assertIn("Use primary sources", lead_prompt)
            self.assertIn("never disclose private", lead_prompt)

            resumed_lead_prompt = runner._build_prompt(
                runner.topology.agent("lead"), [], 2, False,
            )
            self.assertNotIn(
                "External research is available", resumed_lead_prompt,
            )
            self.assertLess(len(resumed_lead_prompt), len(lead_prompt))

            worker_prompt = runner._build_prompt(
                runner.topology.agent("worker-b"), [], 1, True,
            )
            self.assertNotIn("External research", worker_prompt)

    def test_prompt_injects_assigned_context_without_hard_read_isolation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            _add_context_manifest(root)
            run_dir = root / "devsession" / "agent-organizations" / "context-prompt"
            run_dir.mkdir(parents=True)
            runner = OrganizationRunner(
                root, "Qualify the pipeline.", "claude", "flat",
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
            self.assertIn("## Shared foundation and on-demand context", prompt)
            self.assertIn("docs/common.md", prompt)
            self.assertIn("docs/worker-a.md", prompt)
            self.assertIn("Context index:", prompt)
            self.assertNotIn("docs/library-a.md", prompt)
            self.assertNotIn("docs/worker-b.md", prompt)
            self.assertNotIn("docs/library-b.md", prompt)
            self.assertIn("once for this native session", prompt)
            self.assertIn("Retrieve non-Critical entries", prompt)
            resumed = runner._build_prompt(
                runner.topology.agent("worker-a"), [], 2, False,
            )
            self.assertNotIn("## Shared foundation", resumed)
            self.assertNotIn("docs/common.md", resumed)

    def test_experiment_loop_runs_baseline_keeps_and_reverts_without_manager_churn(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            _init_project(root)
            policy_path = _add_experiment_policy(
                root,
                require_goal_progress=True,
            )
            run_dir = Path(td) / "durable-run"
            runner = OrganizationRunner(
                root,
                "Improve one file against an immutable evaluator.",
                "claude",
                "flat",
                "experiment-loop-run",
                run_dir,
                experiment_policy="experiment-policy.json",
                max_experiments=2,
            )
            runner.experiment_loop_root.mkdir(parents=True)
            runner.experiment_policy = (
                organization_module._load_experiment_policy_definition(
                    root,
                    policy_path,
                )
            )
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            for agent_id in ("lead", "worker-a"):
                runner.workspaces[agent_id] = Workspace(
                    root,
                    agent_id,
                    "main",
                    root,
                    [],
                    base,
                )

            work_item = "experiment/app-improvement"
            contract_path = (
                root
                / runner.artifact_staging_prefix
                / "experiment-loop"
                / "contracts"
                / "app-improvement.json"
            )
            contract_path.parent.mkdir(parents=True)
            contract_path.write_text(json.dumps({
                "schema": "reccli.organization-experiment-contract.v1",
                "run_id": runner.run_id,
                "work_item": work_item,
                "manager_id": "lead",
                "worker_id": "worker-a",
                "baseline_mode": "worker_head_at_activation",
                "mutable_file": "app.py",
                "evaluator_id": "app-regression",
                "objective": "Make the immutable evaluator pass.",
                "success_rule": (
                    "Make the immutable evaluator change from failing to passing."
                ),
                "max_trials": 2,
                "max_consecutive_non_improving": 2,
                "max_wall_seconds": 300,
            }), encoding="utf-8")
            contract_head = runner._materialize_agent_candidate(
                runner.topology.agent("lead"),
                _reply("contract registered"),
                base,
                2,
            )
            contract_sha = runner.experiment_contract_by_work_item[work_item]
            accepted, reason = runner._bind_worker_goal(
                worker_id="worker-a",
                manager_id="lead",
                work_item=work_item,
                objective="Make the immutable evaluator pass.",
                risk="high",
                round_number=2,
            )
            self.assertTrue(accepted, reason)
            runner._activate_experiment_contract(
                manager_id="lead",
                worker_id="worker-a",
                work_item=work_item,
                round_number=2,
            )
            self.assertEqual(
                runner.experiment_contracts[contract_sha]["goal_sha256"],
                runner.worker_goals["worker-a"]["goal_sha256"],
            )
            second_sha = "f" * 64
            runner.workspaces["worker-b"] = Workspace(
                root,
                "worker-b",
                "main",
                root,
                [],
                base,
            )
            runner.experiment_contracts[second_sha] = {
                **runner.experiment_contracts[contract_sha],
                "sha256": second_sha,
                "work_item": "experiment/second-file",
                "manager_id": "lead",
                "worker_id": "worker-b",
                "max_trials": 1,
                "status": "registered",
                "activation_baseline_candidate": None,
                "objective": "Improve the second file.",
            }
            runner.experiment_contract_by_work_item[
                "experiment/second-file"
            ] = second_sha
            accepted, reason = runner._bind_worker_goal(
                worker_id="worker-b",
                manager_id="lead",
                work_item="experiment/second-file",
                objective="Improve the second file.",
                risk="high",
                round_number=2,
            )
            self.assertFalse(accepted)
            self.assertIn("already has active owner worker-a", reason)
            runner._ensure_experiment_baseline(
                runner.topology.agent("worker-a"),
                3,
            )
            self.assertEqual(
                runner.experiment_trials[0]["verdict"],
                "baseline",
            )
            self.assertNotIn(
                "stdout_tail",
                runner.experiment_trials[0]["outcome"]["commands"][0],
            )
            self.assertFalse(
                runner.experiment_trials[0]["outcome"]["commands_pass"]
            )
            baseline_command = (
                runner.experiment_trials[0]["outcome"]["commands"][0]
            )
            self.assertRegex(
                baseline_command["stdout_sha256"], r"^[0-9a-f]{64}$",
            )
            self.assertRegex(
                runner.experiment_trials[0]["record_sha256"],
                r"^[0-9a-f]{64}$",
            )
            self.assertIsNone(
                runner.experiment_trials[0]["previous_record_sha256"],
            )
            self.assertEqual(
                runner.experiment_trials[0]["outcome"][
                    "resource_envelope"
                ]["fingerprint"]["max_threads"],
                1,
            )
            self.assertEqual(runner._experiment_used(), 0)

            trial_path = (
                root
                / runner.artifact_staging_prefix
                / "experiment-loop"
                / "trials"
                / "current.json"
            )

            def write_trial(hypothesis: str, change: str) -> None:
                trial_path.parent.mkdir(parents=True, exist_ok=True)
                trial_path.write_text(json.dumps({
                    "schema": "reccli.organization-experiment-trial.v1",
                    "run_id": runner.run_id,
                    "contract_sha256": contract_sha,
                    "work_item": work_item,
                    "worker_id": "worker-a",
                    "hypothesis": hypothesis,
                    "single_change": change,
                    "expected_result": "The immutable evaluator changes state.",
                }), encoding="utf-8")

            (root / "app.py").write_text(
                "print('improved')\n",
                encoding="utf-8",
            )
            write_trial("The target token is load-bearing.", "Change output token.")
            kept_head = runner._materialize_agent_candidate(
                runner.topology.agent("worker-a"),
                _reply("first challenger"),
                contract_head,
                3,
            )
            self.assertEqual(runner.experiment_trials[-1]["verdict"], "keep")
            progress = runner._candidate_goal_progress_verdict(
                kept_head,
                round_number=3,
            )
            self.assertTrue(progress["required"])
            self.assertTrue(progress["qualifies"])
            self.assertEqual(progress["decision"], "retain")
            self.assertEqual(
                progress["qualifying_trials"][0]["goal_sha256"],
                runner.worker_goals["worker-a"]["goal_sha256"],
            )
            self.assertTrue(
                runner.experiment_trials[-1]["outcome"]["patch_shape"][
                    "passes"
                ]
            )
            self.assertIn("improved", (root / "app.py").read_text())
            self.assertEqual(runner._experiment_used(), 1)

            (root / "app.py").write_text(
                "print('regression')\n",
                encoding="utf-8",
            )
            write_trial(
                "Removing the target may simplify the file.",
                "Replace the passing token.",
            )
            resulting_head = runner._materialize_agent_candidate(
                runner.topology.agent("worker-a"),
                _reply("second challenger"),
                kept_head,
                4,
            )
            self.assertEqual(
                runner.experiment_trials[-1]["verdict"],
                "discard",
            )
            self.assertNotEqual(resulting_head, kept_head)
            self.assertIn("improved", (root / "app.py").read_text())
            self.assertEqual(runner._experiment_used(), 2)
            self.assertIn("worker-a", runner.experiment_halted_workers)
            self.assertNotIn("worker-a", runner.active_experiment_by_worker)
            self.assertTrue(runner.inboxes["lead"])
            self.assertEqual(
                runner.inboxes["lead"][-1]["workItem"],
                work_item,
            )
            persisted_intent = Path(
                runner.experiment_trials[-1]["intent_persisted_path"]
            )
            self.assertEqual(
                hashlib.sha256(persisted_intent.read_bytes()).hexdigest(),
                runner.experiment_trials[-1]["intent_sha256"],
            )
            verified, ledger_head, error = (
                organization_module.verify_experiment_trial_records(
                    runner.experiment_trials
                )
            )
            self.assertTrue(verified, error)
            self.assertEqual(
                ledger_head,
                runner.experiment_trials[-1]["record_sha256"],
            )
            tampered = [
                json.loads(json.dumps(trial))
                for trial in runner.experiment_trials
            ]
            tampered[-1]["verdict"] = "keep"
            verified, _, error = (
                organization_module.verify_experiment_trial_records(tampered)
            )
            self.assertFalse(verified)
            self.assertIn("mismatch", error)

    def test_ordinary_candidate_uses_one_predicate_bound_goal_evaluation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            _init_project(root)
            policy_path = _add_experiment_policy(
                root,
                require_goal_progress=True,
            )
            run_dir = Path(td) / "run"
            runner = OrganizationRunner(
                root,
                "Improve app.py through one measured ordinary candidate.",
                "claude",
                "flat",
                "ordinary-goal",
                run_dir,
                experiment_policy="experiment-policy.json",
            )
            runner.experiment_policy = (
                organization_module._load_experiment_policy_definition(
                    root,
                    policy_path,
                )
            )
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            runner.workspaces["worker-a"] = Workspace(
                root, "worker-a", "main", root, [], base,
            )
            accepted, reason = runner._bind_worker_goal(
                worker_id="worker-a",
                manager_id="lead",
                work_item="ordinary/app-improvement",
                objective="Make the immutable app evaluator pass.",
                risk="high",
                round_number=2,
                goal_class="production_pipeline",
                predicate_id="app-output-passes",
                evaluator_id="app-regression",
            )
            self.assertTrue(accepted, reason)
            goal = runner.worker_goals["worker-a"]
            self.assertFalse(goal["baseline_value"])
            for field in (
                "goal_sha256",
                "goal_class",
                "predicate_id",
                "baseline_candidate",
                "evaluator_profile_sha256",
                "immutable_ground_truth_sha256",
                "baseline_value",
                "baseline_result_sha256",
                "baseline_result_path",
                "comparison_rule_id",
            ):
                self.assertIn(field, goal)
            self.assertNotIn("trusted_evaluator_profile_sha256", goal)
            self.assertRegex(
                goal["baseline_result_sha256"],
                r"^[0-9a-f]{64}$",
            )
            self.assertTrue(Path(goal["baseline_result_path"]).is_file())

            (root / "app.py").write_text(
                "print('improved')\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "app.py"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "ordinary improvement"],
                cwd=root,
                check=True,
            )
            candidate = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            runner._deliver_message(
                "worker-a",
                {
                    "to": "lead",
                    "tag": "handoff",
                    "content": "Exact ordinary candidate improves the bound predicate.",
                    "candidate": candidate,
                    "workItem": "ordinary/app-improvement",
                    "risk": "high",
                },
                3,
            )
            self.assertEqual(
                runner.inboxes["lead"][-1]["candidate"],
                candidate,
            )
            progress = runner._candidate_goal_progress_verdict(
                candidate,
                round_number=3,
            )
            self.assertTrue(progress["qualifies"])
            self.assertEqual(
                progress["qualifying_goal_evaluations"][0]["predicate_id"],
                "app-output-passes",
            )
            persisted = json.loads(
                (run_dir / "goal-state.json").read_text(encoding="utf-8")
            )
            persisted_goal = persisted["worker_goals"]["worker-a"]
            self.assertNotIn("outcome", persisted_goal)
            self.assertNotIn("commands", persisted_goal)

            saturated = OrganizationRunner(
                root,
                "Do not spend a worker turn on an already-satisfied predicate.",
                "claude",
                "flat",
                "saturated-goal",
                Path(td) / "saturated-run",
                experiment_policy="experiment-policy.json",
            )
            saturated.experiment_policy = (
                organization_module._load_experiment_policy_definition(
                    root,
                    policy_path,
                )
            )
            saturated.workspaces["worker-b"] = Workspace(
                root, "worker-b", "main", root, [], candidate,
            )
            accepted, reason = saturated._bind_worker_goal(
                worker_id="worker-b",
                manager_id="lead",
                work_item="ordinary/already-satisfied",
                objective="Make the immutable app evaluator pass.",
                risk="routine",
                round_number=2,
                goal_class="production_pipeline",
                predicate_id="app-output-passes",
                evaluator_id="app-regression",
            )
            self.assertFalse(accepted)
            self.assertIn("already satisfied at baseline", reason)
            self.assertEqual(
                saturated.worker_goals["worker-b"]["status"],
                "unevaluable",
            )
            self.assertNotIn(
                "worker-b",
                {
                    agent.agent_id
                    for agent in saturated._select_agents(3)
                },
            )

    def test_bound_predicate_itself_must_improve(self):
        evaluator = {
            "metrics": [
                {
                    "id": "goal-metric",
                    "direction": "maximize",
                    "tolerance": 0.1,
                },
                {
                    "id": "unrelated-metric",
                    "direction": "maximize",
                    "tolerance": 0.0,
                },
            ],
        }
        predicate = {
            "result_id": "goal-metric",
            "comparison_rule_id": "maximize",
        }
        self.assertFalse(
            OrganizationRunner._goal_predicate_improved(
                evaluator,
                predicate,
                1.0,
                1.0,
            )
        )
        self.assertTrue(
            OrganizationRunner._goal_predicate_improved(
                evaluator,
                predicate,
                1.0,
                1.2,
            )
        )

    def test_experiment_metric_verdict_is_pareto_strict_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root,
                "Rank bounded challengers.",
                "claude",
                "flat",
                "metric-verdict",
                root / "run",
            )
            runner.experiment_policy = {
                "evaluators": {
                    "metric": {
                        "metrics": [
                            {
                                "id": "error",
                                "direction": "minimize",
                                "tolerance": 0.01,
                            },
                            {
                                "id": "coverage",
                                "direction": "maximize",
                                "tolerance": 0.01,
                            },
                        ],
                    }
                }
            }
            contract = {"evaluator_id": "metric"}
            champion = {
                "commands_pass": True,
                "result_error": None,
                "timed_out": False,
                "hard_gates": {"valid": True},
                "metrics": {"error": 1.0, "coverage": 0.5},
            }
            improved = {
                **champion,
                "metrics": {"error": 0.8, "coverage": 0.5},
            }
            regressed = {
                **champion,
                "metrics": {"error": 1.2, "coverage": 0.5},
            }
            tradeoff = {
                **champion,
                "metrics": {"error": 0.8, "coverage": 0.4},
            }
            self.assertEqual(
                runner._experiment_verdict(contract, improved, champion),
                "keep",
            )
            self.assertEqual(
                runner._experiment_verdict(contract, regressed, champion),
                "discard",
            )
            self.assertEqual(
                runner._experiment_verdict(contract, tradeoff, champion),
                "inconclusive",
            )

    def test_experiment_patch_shape_enforces_mechanical_atomicity_bounds(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            parent = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            (root / "app.py").write_text(
                "print('one')\nprint('two')\nprint('three')\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "app.py"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "bounded challenger"],
                cwd=root,
                check=True,
            )
            challenger = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            runner = OrganizationRunner(
                root,
                "Bound one trial.",
                "claude",
                "flat",
                "patch-shape",
                root / "run",
            )
            runner.workspaces["worker-a"] = Workspace(
                root, "worker-a", "main", root, [], parent,
            )
            runner.experiment_policy = {
                "evaluators": {
                    "bounded": {
                        "change_limits": {
                            "max_changed_lines": 2,
                            "max_diff_hunks": 2,
                        },
                    },
                },
            }
            shape = runner._experiment_patch_shape({
                "worker_id": "worker-a",
                "evaluator_id": "bounded",
            }, challenger)
            self.assertFalse(shape["passes"])
            self.assertGreater(shape["changed_lines"], 2)
            self.assertIn("mechanical atomicity", shape["scope"])

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
                "flat", "scope-run", run_dir,
            )
            artifact_agent = AgentSpec(
                "auditor-a", "artifact-only test role", "Write only the report.",
                True, "medium", "artifacts",
            )
            runner.workspaces["auditor-a"] = Workspace(
                root, "main", "main", root, [], base,
            )
            session = Mock()

            def mutate_source(*_args, **_kwargs):
                (root / "app.py").write_text("print('forbidden')\n", encoding="utf-8")
                return {"value": _reply(), "session_id": "scope", "usage": {}}

            session.run.side_effect = mutate_source
            session.provider = "claude"
            runner.sessions["auditor-a"] = session
            with self.assertRaisesRegex(RuntimeError, "may write only"):
                runner._run_turn(artifact_agent, 1)

    def test_read_only_reviewer_execution_still_rejects_source_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            runner = OrganizationRunner(
                root, "Audit without mutation.", "claude",
                "flat", "read-only-audit", Path(td) / "run",
            )
            runner.workspaces["auditor-a"] = Workspace(
                root, "auditor-a", "main", root, [], base,
            )

            def mutate_source(_session, *_args, **_kwargs):
                (root / "app.py").write_text(
                    "print('forbidden reviewer edit')\n", encoding="utf-8",
                )
                return {"value": _reply(), "session_id": "audit", "usage": {}}

            # Auditors run fresh sessions, so the fake is patched onto the
            # class instead of injected through runner.sessions.
            with patch.object(SubscriptionSession, "run", new=mutate_source):
                with self.assertRaisesRegex(RuntimeError, "read-only but changed"):
                    runner._run_turn(
                        runner.topology.agent("auditor-a"), 3,
                    )

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
                "flat", "scope-run", run_dir,
            )
            artifact_agent = AgentSpec(
                "auditor-a", "artifact-only test role", "Write only the report.",
                True, "medium", "artifacts",
            )
            runner.workspaces["auditor-a"] = Workspace(
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
            runner.sessions["auditor-a"] = session
            result = runner._run_turn(artifact_agent, 1)
            self.assertEqual(result["reply"]["summary"], "ok")

    def test_artifact_only_commit_is_not_routed_by_an_unassigned_worker(self):
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
                "flat", "scope-run", run_dir,
            )
            artifact_agent = AgentSpec(
                "auditor-a", "artifact-only test role", "Write only the report.",
                True, "medium", "artifacts",
            )
            runner.workspaces["auditor-a"] = Workspace(
                root, "main", "main", root, [], base,
            )
            runner.workspaces["worker-a"] = Workspace(
                root, "worker-a", "main", root, [], base,
            )
            session = Mock()

            def write_artifact(*_args, **_kwargs):
                report = root / runner.artifact_staging_prefix / "takeover.md"
                report.parent.mkdir(parents=True)
                report.write_text("# Takeover\n", encoding="utf-8")
                reply = _reply("report ready")
                reply["messages"] = [{
                    "to": "lead", "tag": "review",
                    "content": "Review this durable report identity.",
                    "candidate": HOST_CANDIDATE,
                    "workItem": "report-only", "risk": "high",
                }]
                return {"value": reply, "session_id": "scope", "usage": {}}

            session.run.side_effect = write_artifact
            session.provider = "claude"
            runner.sessions["auditor-a"] = session
            result = runner._run_turn(artifact_agent, 1)
            self.assertIsNone(
                result["reply"]["messages"][0]["candidate"],
            )
            artifact_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            self.assertEqual(
                runner.candidate_kinds[artifact_head]["kind"],
                "artifact-only",
            )

            runner._deliver_message("worker-a", {
                "to": "lead", "tag": "handoff",
                "content": "Do not route a report commit as implementation.",
                "candidate": artifact_head,
                "workItem": "report-only", "risk": "high",
            }, 1)
            self.assertNotIn(artifact_head, runner.governance.assignments)
            dropped = [
                json.loads(line)
                for line in (run_dir / "messages.jsonl").read_text().splitlines()
            ]
            self.assertEqual(dropped[-1]["status"], "dropped")
            self.assertIn("not an implementation candidate", dropped[-1]["reason"])

            runner._deliver_message("worker-a", {
                "to": "lead", "tag": "review",
                "content": "Review this exact terminal evidence report.",
                "candidate": artifact_head,
                "workItem": "L8-EXACT-BLOCKED-CLOSEOUT",
                "risk": "release",
            }, 2)
            routed = [
                json.loads(line)
                for line in (run_dir / "messages.jsonl").read_text().splitlines()
            ]
            self.assertEqual(routed[-1]["status"], "dropped")
            self.assertIn("without one active goal", routed[-1]["reason"])

            runner._deliver_message("lead", {
                "to": "organization", "tag": "status",
                "content": "Macro checkpoint for every connected agent.",
                "candidate": None, "workItem": None, "risk": None,
            }, 3)
            self.assertTrue(all(
                any(
                    item.get("content")
                    == "Macro checkpoint for every connected agent."
                    for item in runner.inboxes[agent_id]
                )
                for agent_id in [
                    *runner.topology.worker_ids,
                    *runner.topology.final_reviewer_pool,
                ]
            ))

    def test_terminal_lead_conclusion_is_durable_and_outside_work_rounds(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            _init_project(root)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            run_dir = root / "devsession" / "agent-organizations" / "conclusion"
            run_dir.mkdir(parents=True)
            runner = OrganizationRunner(
                root, "Qualify the bounded system.", "claude",
                "flat", "conclusion", run_dir,
            )
            runner.workspaces["lead"] = Workspace(
                root, "lead", "main", root, [], base,
            )
            runner.attempted_turns = 9
            runner.completed_turns = 8
            runner.failed_turns = 1
            session = Mock()
            session.provider = "claude"
            session.run.return_value = {
                "value": _conclusion(
                    "The run ended at its 8-turn limit: 8 working turns plus "
                    "0 closeout turns."
                ),
                "session_id": "lead-session",
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 2,
                    "output_tokens": 3,
                },
            }
            runner.sessions["lead"] = session

            conclusion = runner._write_terminal_lead_conclusion(
                "round_limit",
                8,
                verified_candidate=None,
                promotion_candidate=None,
                promotion_request=None,
            )

            self.assertEqual(conclusion["generated_by"], "lead")
            self.assertEqual(conclusion["terminal_status"], "round_limit")
            self.assertEqual(conclusion["promotion_readiness"], "no_candidate")
            self.assertEqual(conclusion["turn_counts"]["attempted"], 9)
            self.assertEqual(
                conclusion["round_counts"],
                {"total": 8, "working": 8, "closeout": 0},
            )
            self.assertEqual(
                json.loads(
                    (run_dir / "run-conclusion.json").read_text(
                        encoding="utf-8",
                    )
                )["summary"],
                (
                    "The run ended at its 8-round limit: 8 working rounds plus "
                    "0 closeout rounds."
                ),
            )
            markdown = (run_dir / "run-conclusion.md").read_text(
                encoding="utf-8",
            )
            self.assertIn("What was accomplished", markdown)
            self.assertIn("Recommended next action", markdown)
            self.assertIs(
                session.run.call_args.args[1],
                RUN_CONCLUSION_SCHEMA,
            )
            conclusion_prompt = session.run.call_args.args[0]
            self.assertIn(
                "Never describe a round limit as a turn limit",
                conclusion_prompt,
            )
            self.assertIn(
                "Mission retained from this session's bootstrap",
                conclusion_prompt,
            )
            self.assertNotIn(
                "Qualify the bounded system.",
                conclusion_prompt,
            )
            self.assertIn('"mission_sha256"', conclusion_prompt)
            self.assertNotIn('"mission":', conclusion_prompt)

    def test_cancelled_run_writes_fallback_without_another_model_turn(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            _init_project(root)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            run_dir = root / "devsession" / "agent-organizations" / "cancelled"
            run_dir.mkdir(parents=True)
            runner = OrganizationRunner(
                root, "Qualify the bounded system.", "claude",
                "flat", "cancelled", run_dir,
            )
            runner.workspaces["lead"] = Workspace(
                root, "lead", "main", root, [], base,
            )
            session = Mock()
            session.provider = "claude"
            runner.sessions["lead"] = session

            conclusion = runner._write_terminal_lead_conclusion(
                "cancelled",
                3,
                verified_candidate=None,
                promotion_candidate=None,
                promotion_request=None,
            )

            session.run.assert_not_called()
            self.assertEqual(conclusion["generated_by"], "host-fallback")
            self.assertEqual(conclusion["promotion_readiness"], "cancelled")
            self.assertTrue((run_dir / "run-conclusion.json").is_file())

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
                "flat", "integration-run", run_dir,
            )
            runner.workspaces["lead"] = Workspace(
                root, initial_branch, initial_branch, root, [], base,
            )
            handoff = {
                "to": "lead", "tag": "handoff", "content": "Ready.",
                "candidate": candidate, "workItem": "experiment-a103", "risk": "high",
            }
            accepted, _, review = runner.governance.process_message("worker-d", handoff, 1)
            self.assertTrue(accepted)
            runner.governance.record_decision(review["to"], {
                "to": "lead", "tag": "decision", "content": "NO_VETO: no blocking falsification found.",
                "candidate": candidate, "workItem": "experiment-a103", "risk": "high",
            })
            subprocess.run(["git", "cherry-pick", candidate], cwd=root, check=True, capture_output=True)
            runner._validate_agent_write_scope(runner.topology.agent("lead"))

            (root / "app.py").write_text("print('lead-authored')\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "unapproved lead edit"], cwd=root, check=True)
            with self.assertRaisesRegex(RuntimeError, "was not eligible"):
                runner._validate_agent_write_scope(runner.topology.agent("lead"))

    def test_generated_output_bundle_is_sealed_without_putting_cad_in_git(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "bundle-run"
            run_dir.mkdir(parents=True)
            runner = OrganizationRunner(
                root, "Execute one bounded experiment.", "claude",
                "flat", "bundle-run", run_dir, max_experiments=1,
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
                "to": "lead", "tag": "handoff", "content": "Temporary experiment complete.",
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

    def test_tracked_report_artifact_does_not_discard_delegation_turn(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "report-run"
            run_dir.mkdir(parents=True)
            runner = OrganizationRunner(
                root, "Delegate bounded work.", "claude",
                "flat", "report-run", run_dir, max_experiments=3,
            )
            runner.candidate_artifact_root.mkdir()
            runner.candidate_artifact_root.chmod(0o555)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            runner.workspaces["lead"] = Workspace(
                root, "lead", "main", root, [], base,
            )
            report = (
                root / runner.artifact_staging_prefix / "lead" /
                "r2" / "delegation.md"
            )
            report.parent.mkdir(parents=True)
            report.write_text(
                "# Delegation\n\nWorker B owns the bounded implementation.\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "add", "-f", report.relative_to(root).as_posix()],
                cwd=root, check=True,
            )
            subprocess.run(
                ["git", "commit", "-qm", "record delegation"],
                cwd=root, check=True,
            )
            reply = _reply()
            reply["artifacts"] = [report.relative_to(root).as_posix()]
            reply["messages"] = [{
                "to": "worker-b", "tag": "plan",
                "content": "Implement the bounded two-file change.",
                "candidate": None,
                "workItem": "B-R1-CLEAN-SUCCESSOR",
                "risk": "high",
            }]

            bundle = runner._seal_reported_artifacts(
                runner.topology.agent("lead"), reply, 2,
            )

            self.assertIsNone(bundle)
            self.assertEqual(runner._experiment_used(), 0)

    def test_git_backed_probe_consumes_budget_but_report_does_not(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            run_dir = root / "run"
            runner = OrganizationRunner(
                root, "Bound empirical work.", "claude",
                "flat", "git-experiment-budget", run_dir,
                max_experiments=1,
            )
            runner.workspaces["worker-a"] = Workspace(
                root, "main", "main", root, [], base,
            )
            worker = runner.topology.agent("worker-a")

            report = (
                root / runner.artifact_staging_prefix / "worker-a" /
                "r1" / "review.md"
            )
            report.parent.mkdir(parents=True)
            report.write_text(
                "# Review\n\nThis only summarizes existing evidence.\n",
                encoding="utf-8",
            )
            runner._materialize_agent_candidate(
                worker, _reply("report only"), base, 1,
            )
            self.assertEqual(runner._experiment_used(), 0)

            prior = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            probe = (
                root / runner.artifact_staging_prefix / "worker-a" /
                "r2" / "probes" / "radius_probe.py"
            )
            probe.parent.mkdir(parents=True)
            probe.write_text("print('measure radius')\n", encoding="utf-8")
            runner._materialize_agent_candidate(
                worker, _reply("probe captured"), prior, 2,
            )
            self.assertEqual(runner._experiment_used(), 1)
            self.assertEqual(
                runner.experiment_records[0]["kinds"],
                ["git-backed-probe-or-data"],
            )
            self.assertIn(
                probe.relative_to(root).as_posix(),
                runner.experiment_records[0]["paths"],
            )

            prior = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            measurement = (
                root / runner.artifact_staging_prefix / "worker-a" /
                "r3" / "measurements" / "radius.json"
            )
            measurement.parent.mkdir(parents=True)
            measurement.write_text('{"radius": 8.0}\n', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
                runner._materialize_agent_candidate(
                    worker, _reply("second experiment"), prior, 3,
                )
            self.assertEqual(runner._experiment_used(), 1)

    def test_experiment_channels_share_one_turn_slot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root, "Bound empirical work.", "claude",
                "flat", "unified-experiment-budget", root / "run",
                max_experiments=1,
            )
            worker = runner.topology.agent("worker-b")
            first = runner._claim_experiment_slot(
                worker, 3,
                kind="git-backed-probe-or-data",
                candidate=None,
                paths=[".reccli-org-artifacts/run/worker-b/r3/probe.py"],
            )
            second = runner._claim_experiment_slot(
                worker, 3,
                kind="sealed-generated-output",
                candidate="candidate-3",
                paths=["out/experiments/worker-b-r3/result.step"],
            )
            self.assertEqual(first["slot"], second["slot"])
            self.assertEqual(runner._experiment_used(), 1)
            self.assertEqual(
                second["kinds"],
                ["git-backed-probe-or-data", "sealed-generated-output"],
            )
            self.assertEqual(second["candidate"], "candidate-3")

    def test_experiment_budget_is_atomic_across_parallel_turns(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root, "Bound parallel empirical work.", "claude",
                "flat", "parallel-experiment-budget", root / "run",
                max_experiments=3,
            )
            worker = runner.topology.agent("worker-c")

            def claim(round_number):
                return runner._claim_experiment_slot(
                    worker, round_number,
                    kind="git-backed-probe-or-data",
                    candidate=None,
                    paths=[
                        f".reccli-org-artifacts/run/worker-c/r{round_number}/"
                        "probe.py"
                    ],
                )

            successes = 0
            failures = 0
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(claim, index) for index in range(1, 9)]
                for future in futures:
                    try:
                        future.result()
                        successes += 1
                    except RuntimeError as exc:
                        self.assertIn("budget exhausted", str(exc))
                        failures += 1
            self.assertEqual(successes, 3)
            self.assertEqual(failures, 5)
            self.assertEqual(runner._experiment_used(), 3)
            self.assertEqual(
                {record["slot"] for record in runner.experiment_records},
                {1, 2, 3},
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
                "flat", "protected-run", run_dir,
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
                "flat", "caller-guard", root / "run",
            )
            runner.caller_head = base
            (root / "app.py").write_text("print('canonical mutation')\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "caller repository changed"):
                runner._verify_caller_repository_unchanged()

    def test_completion_writes_human_promotion_request_only(self):
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
                "flat", "promotion-run", run_dir,
                protected_paths=["app.py"], max_experiments=2,
            )
            runner.workspaces["lead"] = Workspace(
                root, "main", "main", root, [], base,
            )
            artifact_manifest = {"manifest_sha256": "artifact-manifest-hash"}
            request = runner._write_promotion_request(
                base, base, "reccli-org/flat/proposal", artifact_manifest,
            )
            self.assertEqual(request["status"], "awaiting_human_authorization")
            self.assertFalse(request["canonical_effects_applied"])
            self.assertEqual(request["protected_paths"], ["app.py"])
            self.assertTrue((run_dir / "promotion-request.json").is_file())

    def test_pending_human_request_embeds_exact_reviewed_dossier(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            run_dir = (
                root / "devsession" / "agent-organizations" / "human-gate"
            )
            run_dir.mkdir(parents=True)
            runner = OrganizationRunner(
                root,
                "Require an exact sponsor decision.",
                "claude",
                "flat",
                "human-gate",
                run_dir,
            )
            runner.caller_head = base
            runner.workspaces["lead"] = Workspace(
                root, "main", "main", root, [], base,
            )
            dossier_path = (
                root / runner.artifact_staging_prefix / "approval-dossier.md"
            )
            dossier_path.parent.mkdir(parents=True)
            dossier_path.write_text(
                "# Approval request\n\nApprove checkpoint X only.\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "add", "-f", runner.artifact_staging_prefix],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-qm", "approval dossier"],
                cwd=root,
                check=True,
            )
            candidate = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            (run_dir / "request.json").write_text(
                json.dumps({
                    "provider_requested": "claude",
                    "topology": "flat",
                    "max_rounds": 8,
                    "max_concurrency": 5,
                    "turn_timeout_seconds": 1200,
                    "model": None,
                    "evidence_paths": [],
                    "protected_paths": [],
                    "context_manifest": None,
                    "experiment_policy": "experiment-policy.json",
                    "max_experiments": 3,
                }) + "\n",
                encoding="utf-8",
            )
            request = runner._write_pending_human_approval_request(
                candidate,
                {
                    "summary": "Sponsor authority is the only blocker.",
                    "accomplishments": ["Bounded the request."],
                    "conclusive_findings": [],
                    "evidence_and_tests": ["Exact dossier reviewed."],
                    "scientific_or_product_blockers": [],
                    "infrastructure_failures": [],
                    "unresolved": ["Sponsor decision."],
                    "next_action": "Approve or reject.",
                    "limitations": [],
                },
            )
            self.assertEqual(
                request["request_kind"],
                "checkpoint_continuation",
            )
            self.assertEqual(request["report_candidate"], candidate)
            self.assertEqual(
                request["action"]["type"],
                "start_successor",
            )
            self.assertEqual(
                request["continuation"]["experiment_policy"],
                "experiment-policy.json",
            )
            self.assertIn(
                "Approve checkpoint X only.",
                request["report_files"][0]["content"],
            )
            self.assertTrue((run_dir / "approval-request.json").is_file())

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
                "flat", "new-run", run_dir,
            )
            runner.workspaces["lead"] = Workspace(
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

    def test_scheduler_does_not_treat_worker_turns_as_experiments(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root, "Explore within one experiment slot.", "claude",
                "flat", "budget-run", root / "run", max_experiments=1,
            )
            message = {
                "from": "lead", "tag": "plan", "content": "Run a sandbox experiment.",
                "candidate": None, "workItem": "bounded-experiment", "risk": "high",
            }
            runner.inboxes["worker-a"] = [message]
            runner.inboxes["worker-b"] = [{
                **message,
                "workItem": "bounded-experiment-b",
            }]
            scheduled = runner._select_agents(3)
            writers = [agent for agent in scheduled if agent.write_scope == "workspace"]
            self.assertEqual(
                {agent.agent_id for agent in writers},
                {"worker-a", "worker-b"},
            )

    def test_event_scheduler_runs_lead_first_and_rewakes_it_on_inbox(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root, "Assign, execute, then integrate.", "claude",
                "flat", "delegation-run", root / "run",
            )
            self.assertEqual(
                [agent.agent_id for agent in runner._select_agents(1)],
                ["lead"],
            )
            lead_message = {
                "from": "lead", "tag": "plan",
                "content": "Execute the bounded lane.",
                "candidate": None, "workItem": "lane-a", "risk": "routine",
            }
            runner.inboxes["worker-a"] = [lead_message]
            runner.states["lead"] = "working"
            scheduled = {
                agent.agent_id for agent in runner._select_agents(2)
            }
            self.assertIn("worker-a", scheduled)
            self.assertNotIn("lead", scheduled)
            runner.inboxes["lead"] = [{
                **lead_message,
                "from": "worker-a",
                "content": "Result and observed output.",
            }]
            self.assertIn(
                "lead",
                {agent.agent_id for agent in runner._select_agents(2)},
            )

    def test_worker_has_one_host_owned_problem_solving_goal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "run"
            runner = OrganizationRunner(
                root, "Fix the delivery pipeline.", "claude",
                "flat", "one-goal", run_dir,
            )
            goal = {
                "to": "worker-a",
                "tag": "plan",
                "content": "Fix the radius qualifier and pass its focused test.",
                "candidate": None,
                "workItem": "radius-qualifier",
                "risk": "high",
            }

            # A plan from anyone but the worker's supervisor is dropped by the
            # supervisor-only gate before it can bind anything.
            runner._deliver_message("auditor-a", goal, 2)
            self.assertEqual(runner.inboxes["worker-a"], [])
            runner._deliver_message("lead", goal, 2)
            self.assertEqual(
                runner.worker_goals["worker-a"]["work_item"],
                "radius-qualifier",
            )
            self.assertEqual(
                runner.worker_goals["worker-a"]["status"],
                "active",
            )

            runner._deliver_message("lead", {
                **goal,
                "content": "Standby and monitor the repository.",
                "workItem": "standby-lane",
            }, 3)
            self.assertEqual(
                runner.worker_goals["worker-a"]["work_item"],
                "radius-qualifier",
            )
            runner._deliver_message("lead", {
                **goal,
                "content": "Also refactor unrelated documentation.",
                "workItem": "unrelated-docs",
            }, 3)
            self.assertEqual(
                runner.worker_goals["worker-a"]["work_item"],
                "radius-qualifier",
            )
            goal_state = json.loads(
                (run_dir / "goal-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                goal_state["worker_goals"]["worker-a"]["objective"],
                "Fix the radius qualifier and pass its focused test.",
            )
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            runner.workspaces["worker-a"] = Workspace(
                root, "worker-a", "main", root, [], base,
            )
            prompt = runner._build_prompt(
                runner.topology.agent("worker-a"),
                runner.inboxes["worker-a"],
                3,
                True,
            )
            self.assertIn("## One active goal", prompt)
            self.assertIn("Goal:", prompt)
            self.assertIn(
                "Fix the radius qualifier and pass its focused test.",
                prompt,
            )
            self.assertNotIn(
                "Own the single semantic reconciliation",
                prompt,
            )
            records = [
                json.loads(line)
                for line in (run_dir / "messages.jsonl").read_text().splitlines()
            ]
            self.assertIn("may come only from lead", records[0]["reason"])
            reasons = [
                str(record.get("reason") or "")
                for record in records
                if record.get("status") == "dropped"
            ]
            self.assertTrue(any(
                "problem-solving outcome" in reason for reason in reasons
            ))
            self.assertTrue(any(
                "already has active goal" in reason for reason in reasons
            ))

    def test_off_goal_flag_is_raised_then_acted_by_a_supervisor_decision(self):
        """A workItem-matched supervisor decision adjudicates an open flag.

        KNOWN DEFECT (organization.py): _record_off_goal_flag stores
        manager_id from primary_manager_by_worker (None in flat), while the
        decision path looks the flag up with manager_id=sender ("lead"), so
        the supervisor's decision never matches and the flag stays raised.
        The lookup should resolve through _supervisor_of. This test encodes
        the intended contract and fails until that is fixed.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "run"
            runner = OrganizationRunner(
                root, "Fix the delivery pipeline.", "claude",
                "flat", "off-goal-decision", run_dir,
            )
            runner._deliver_message("lead", {
                "to": "worker-a",
                "tag": "plan",
                "content": "Fix the primitive qualifier and run its tests.",
                "candidate": None,
                "workItem": "primitive-qualifier",
                "risk": "high",
            }, 2)
            runner._deliver_message("worker-a", {
                "to": "lead",
                "tag": "flag",
                "content": (
                    "docs/a.md contradicts docs/b.md about an unrelated "
                    "release naming rule; no code was changed for it."
                ),
                "candidate": None,
                "workItem": "primitive-qualifier",
                "risk": "high",
            }, 3)
            flag = next(iter(runner.off_goal_flags.values()))
            self.assertEqual(flag["status"], "raised")

            # An open flag does not let the worker switch scope by itself.
            self.assertEqual(
                runner.worker_goals["worker-a"]["work_item"],
                "primitive-qualifier",
            )

            runner._deliver_message("lead", {
                "to": "worker-a",
                "tag": "decision",
                "content": "Confirmed, but it does not affect the active fix.",
                "candidate": None,
                "workItem": "primitive-qualifier",
                "risk": "high",
            }, 4)
            self.assertEqual(flag["status"], "acted")
            self.assertEqual(
                runner.worker_goals["worker-a"]["work_item"],
                "primitive-qualifier",
            )

    def test_rebind_with_an_open_off_goal_flag_replaces_the_goal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "run"
            runner = OrganizationRunner(
                root, "Fix the delivery pipeline.", "claude",
                "flat", "off-goal-rebind", run_dir,
            )
            runner._deliver_message("lead", {
                "to": "worker-a",
                "tag": "plan",
                "content": "Fix the primitive qualifier and run its tests.",
                "candidate": None,
                "workItem": "primitive-qualifier",
                "risk": "high",
            }, 2)
            runner._deliver_message("worker-a", {
                "to": "lead",
                "tag": "flag",
                "content": (
                    "docs/a.md contradicts docs/b.md about an unrelated "
                    "release naming rule; no code was changed for it."
                ),
                "candidate": None,
                "workItem": "primitive-qualifier",
                "risk": "high",
            }, 3)
            flag = next(iter(runner.off_goal_flags.values()))
            self.assertEqual(flag["status"], "raised")

            # An open raised flag on the current goal UNLOCKS a rebind, and
            # the rebind is the adjudication.
            runner._deliver_message("lead", {
                "to": "worker-a",
                "tag": "plan",
                "content": "Fix the validated release naming contradiction.",
                "candidate": None,
                "workItem": "naming-rule",
                "risk": "routine",
            }, 4)
            self.assertEqual(flag["status"], "acted")
            self.assertEqual(
                flag["decision"], "Replaced goal with naming-rule",
            )
            self.assertEqual(
                runner.worker_goals["worker-a"]["work_item"],
                "naming-rule",
            )
            self.assertEqual(
                runner.worker_goal_history[-1]["work_item"],
                "primitive-qualifier",
            )

    def test_event_scheduler_keeps_the_lead_inbox_driven(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root, "Do bounded work.", "claude",
                "flat", "event-run", root / "run",
            )
            runner.states["lead"] = "working"
            runner.states["worker-a"] = "working"
            runner.turned.update({"lead", "worker-a"})
            runner.inboxes["worker-a"] = [{
                "from": "lead", "to": "worker-a", "tag": "plan",
                "content": "Continue the bounded implementation.",
                "candidate": None, "workItem": "work-a", "risk": "routine",
            }]
            selected = {
                agent.agent_id for agent in runner._select_agents(3)
            }
            self.assertNotIn("lead", selected)
            self.assertIn("worker-a", selected)

    def test_veto_auditor_requires_exact_candidate_for_review_traffic(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "run"
            runner = OrganizationRunner(
                root, "Review only exact candidates.", "claude",
                "flat", "review-run", run_dir,
            )
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            runner.workspaces["lead"] = Workspace(
                root, "main", "main", root, [], base,
            )
            candidate_less = {
                "to": "auditor-a", "tag": "review",
                "content": "Please re-check repository state.",
                "candidate": None, "workItem": "census", "risk": "routine",
            }
            runner._deliver_message("lead", candidate_less, 3)
            self.assertEqual(runner.inboxes["auditor-a"], [])
            self.assertEqual(runner.dropped_messages, 1)

            exact = {
                **candidate_less,
                "content": "Review this exact release dossier.",
                "candidate": base,
                "workItem": "final-report",
                "risk": "release",
            }
            runner._deliver_message("lead", exact, 4)
            self.assertEqual(
                runner.inboxes["auditor-a"][0]["candidate"], base,
            )

    def test_exact_no_veto_review_is_normalized_into_final_decision(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "run"
            runner = OrganizationRunner(
                root, "Review one exact release dossier.", "claude",
                "flat", "normalize-review", run_dir,
            )
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            release_reviewer = runner.governance.release_reviewer_id
            runner.workspaces[release_reviewer] = Workspace(
                root, "main", "main", root, [], base,
            )
            runner._deliver_message(release_reviewer, {
                "to": "lead",
                "tag": "review",
                "content": (
                    f"NO_VETO {base}: no blocking falsification was "
                    "established for this exact dossier."
                ),
                "candidate": base,
                "workItem": "final-no-promotion",
                "risk": "release",
            }, 4)

            delivered = runner.inboxes["lead"][0]
            self.assertEqual(delivered["tag"], "decision")
            self.assertEqual(delivered["normalizedFromTag"], "review")
            self.assertEqual(
                runner.governance.candidate_approvals[release_reviewer], base,
            )
            events = [
                json.loads(line)
                for line in (run_dir / "events.jsonl").read_text().splitlines()
            ]
            self.assertIn(
                "message.decision_normalized",
                {event["type"] for event in events},
            )

    def test_no_veto_review_without_exact_identity_is_rejected_and_requeued(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "run"
            runner = OrganizationRunner(
                root, "Review one exact release dossier.", "claude",
                "flat", "reject-ambiguous-review", run_dir,
            )
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            release_reviewer = runner.governance.release_reviewer_id
            runner.workspaces[release_reviewer] = Workspace(
                root, "main", "main", root, [], base,
            )
            runner._deliver_message(release_reviewer, {
                "to": "lead",
                "tag": "review",
                "content": "NO_VETO: the dossier appears internally consistent.",
                "candidate": base,
                "workItem": "final-no-promotion",
                "risk": "release",
            }, 4)

            self.assertEqual(runner.inboxes["lead"], [])
            self.assertNotIn(
                release_reviewer, runner.governance.candidate_approvals,
            )
            retry = runner.inboxes[release_reviewer][0]
            self.assertEqual(retry["tag"], "blocker")
            self.assertIn(base, retry["content"])
            messages = [
                json.loads(line)
                for line in (run_dir / "messages.jsonl").read_text().splitlines()
            ]
            self.assertEqual(messages[0]["status"], "dropped")
            self.assertIn("decision was not recorded", messages[0]["reason"])

    def test_closeout_ignores_routine_chatter_and_detects_no_progress(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root, "Close out efficiently.", "claude",
                "flat", "closeout-run", root / "run",
            )
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            runner.workspaces["lead"] = Workspace(
                root, "main", "main", root, [], base,
            )
            runner.inboxes["worker-a"] = [{
                "from": "lead", "tag": "status", "content": "Still waiting.",
                "candidate": None, "workItem": "status", "risk": "routine",
            }]
            runner.inboxes["lead"] = [{
                "from": "auditor-a", "tag": "status",
                "content": "Still waiting.",
                "candidate": None, "workItem": "status", "risk": "routine",
            }]
            self.assertEqual(runner._select_closeout_agents(), [])
            runner.inboxes["lead"].append({
                "from": "auditor-a", "tag": "plan",
                "content": "Assemble the terminal dossier.",
                "candidate": None, "workItem": "final-report",
                "risk": "release",
            })
            self.assertEqual(
                [agent.agent_id for agent in runner._select_closeout_agents()],
                ["lead"],
            )
            first = runner._closeout_progress_signature()
            second = runner._closeout_progress_signature()
            self.assertEqual(first, second)

    def test_codex_usage_is_accounted_as_session_delta(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root, "Count tokens.", "mixed",
                "flat", "usage-run", root / "run",
                provider_assignments={
                    agent.agent_id: (
                        "codex" if agent.agent_id == "lead" else "claude"
                    )
                    for agent in get_topology("flat").agents
                },
            )
            first = runner._add_usage({
                "input_tokens": 100,
                "cached_input_tokens": 80,
                "output_tokens": 10,
            }, "codex", "thread-1")
            second = runner._add_usage({
                "input_tokens": 135,
                "cached_input_tokens": 110,
                "output_tokens": 14,
            }, "codex", "thread-1")
            claude = runner._add_usage({
                "input_tokens": 7,
                "cached_input_tokens": 5,
                "output_tokens": 2,
            }, "claude", "session-1")
            self.assertEqual(first["input_tokens"], 100)
            self.assertEqual(second, {
                "input_tokens": 35,
                "cached_input_tokens": 30,
                "output_tokens": 4,
            })
            self.assertEqual(claude["input_tokens"], 7)
            self.assertEqual(runner.usage, {
                "input_tokens": 142,
                "cached_input_tokens": 115,
                "output_tokens": 16,
            })

    def test_resumed_prompt_is_delta_only_with_bounded_overlap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root, "Inspect " + ("the declared acceptance contract. " * 80),
                "claude", "flat", "prompt-run", root / "run",
                protected_paths=[f"docs/protected-{index}" for index in range(20)],
            )
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            workspace = Workspace(
                root, "worker-a", "integration", root, [], base,
            )
            runner.workspaces["worker-a"] = workspace
            runner.host_state_brief = {
                "content_sha256": "state-sha",
                "round": 2,
                "mechanical_authority": "Host facts are mechanical.",
                "repository": {"launch_head": base},
                "mission_commit_inventory": {
                    "launch_head": base, "mentioned_commits": [],
                },
                "known_candidates": [],
                "integrated_candidates": {},
                "governance": {},
                "workspaces": {
                    "worker-a": {
                        "base_commit": base, "head": base,
                        "changed_from_base": False,
                    },
                },
                "experiment_budget": {
                    "maximum": 3, "used": 0, "remaining": 3,
                },
            }
            bootstrap = runner._build_prompt(
                runner.topology.agent("worker-a"), [], 3, True,
            )
            runner.model_prompt_state_by_agent["worker-a"] = (
                runner._model_prompt_state("worker-a")
            )
            incremental = runner._build_prompt(
                runner.topology.agent("worker-a"), [], 4, False,
            )
            next_incremental = runner._build_prompt(
                runner.topology.agent("worker-a"), [], 5, False,
            )
            self.assertIn("# RecCli delta worker-a R4", incremental)
            self.assertNotIn("state-sha", incremental)
            self.assertNotIn("## Mission", incremental)
            self.assertNotIn("Operational boundary", incremental)
            self.assertNotIn("Context index", incremental)
            self.assertNotIn("Evidence manifest", incremental)
            self.assertNotIn("protected-19", incremental)
            self.assertNotIn("mission_commit_inventory", incremental)
            self.assertNotIn("## RecCli project memory", bootstrap)
            self.assertNotIn("## Organization charter", bootstrap)
            self.assertLess(len(bootstrap) - len(runner.mission), 8_000)
            self.assertLess(len(incremental), len(bootstrap))
            self.assertLess(len(incremental), 500)
            left = incremental.splitlines(keepends=True)
            right = next_incremental.splitlines(keepends=True)
            repeated_chars = sum(
                len("".join(left[match.a:match.a + match.size]))
                for match in SequenceMatcher(
                    None, left, right, autojunk=False,
                ).get_matching_blocks()
            )
            self.assertLess(repeated_chars, 128)
            self.assertLess(repeated_chars / len(next_incremental), 0.8)

    def test_review_assignment_appears_once_without_unrelated_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "run"
            run_dir.mkdir()
            runner = OrganizationRunner(
                root, "Review one exact candidate.", "claude",
                "flat", "review-once", run_dir,
            )
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            runner.workspaces["auditor-a"] = Workspace(
                root, "auditor-a", "main", root, [], base,
            )
            candidate = "a" * 40
            unrelated = "f" * 40
            review = {
                "runId": "review-once",
                "round": 4,
                "from": "worker-a",
                "to": "auditor-a",
                "tag": "review",
                "content": "REVIEW_THIS_EXACT_CANDIDATE_ONCE",
                "candidate": candidate,
                "workItem": "exact-review",
                "risk": "high",
                "status": "delivered",
            }
            (run_dir / "messages.jsonl").write_text(
                json.dumps(review) + "\n",
                encoding="utf-8",
            )
            runner.inboxes["auditor-a"] = [review]
            runner.host_state_brief = {
                "content_sha256": "host-state",
                "round": 4,
                "repository": {"launch_head": base},
                "known_candidates": [
                    {"candidate": candidate, "kind": "implementation"},
                    {"candidate": unrelated, "kind": "implementation"},
                ],
                "governance": {"history": ["unrelated"]},
                "workspaces": {},
                "experiment_budget": {
                    "maximum": 3, "used": 0, "remaining": 3,
                },
            }
            prompt = runner._build_prompt(
                runner.topology.agent("auditor-a"), [review], 4, True,
            )
            self.assertEqual(
                prompt.count("REVIEW_THIS_EXACT_CANDIDATE_ONCE"),
                1,
            )
            self.assertEqual(prompt.count(candidate), 1)
            self.assertNotIn(unrelated, prompt)
            self.assertNotIn("governance", prompt)
            self.assertNotIn("experiment_budget", prompt)
            self.assertNotIn("ASSIGNABLE PREDICATES", prompt)

    def test_auditors_do_not_receive_worker_planning_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root, "Answer one bounded question.", "claude",
                "flat", "role-state", root / "run",
            )
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            runner.worker_goals["worker-a"] = {
                "worker_id": "worker-a",
                "manager_id": "lead",
                "work_item": "SECRET_UNRELATED_WORK_ITEM",
                "objective": "SECRET_UNRELATED_OBJECTIVE",
                "risk": "routine",
                "status": "active",
                "goal_sha256": "1" * 64,
                "predicate_id": "unrelated-predicate",
            }
            runner.host_state_brief = {
                "content_sha256": "state",
                "experiment_budget": {
                    "maximum": 3, "used": 0, "remaining": 3,
                },
            }
            # The lead is deliberately absent here: it is the planner, and the
            # goal state is exactly what it plans with.
            for agent_id in ("auditor-a", "auditor-b"):
                runner.workspaces[agent_id] = Workspace(
                    root, agent_id, "main", root, [], base,
                )
                prompt = runner._build_prompt(
                    runner.topology.agent(agent_id), [], 3, True,
                )
                self.assertNotIn("SECRET_UNRELATED", prompt)
                self.assertNotIn("unrelated-predicate", prompt)
                self.assertNotIn("experiment_budget", prompt)
                self.assertNotIn("ASSIGNABLE PREDICATES", prompt)

    def test_lead_goal_packet_omits_terminal_worker_goals(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root, "Assign only current measurable work.", "claude",
                "flat", "lead-current-goals", root / "run",
            )
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            runner.workspaces["lead"] = Workspace(
                root, "lead", "main", root, [], base,
            )
            runner.worker_goals = {
                "worker-a": {
                    "worker_id": "worker-a",
                    "manager_id": "lead",
                    "work_item": "CURRENT_GOAL",
                    "objective": "Change the measured production behavior.",
                    "risk": "routine",
                    "status": "active",
                    "goal_sha256": "1" * 64,
                    "predicate_id": "current-predicate",
                },
                "worker-c": {
                    "worker_id": "worker-c",
                    "manager_id": "lead",
                    "work_item": "STALE_TERMINAL_GOAL",
                    "objective": "This goal is already closed.",
                    "risk": "routine",
                    "status": "unevaluable",
                    "goal_sha256": "2" * 64,
                    "predicate_id": "stale-predicate",
                },
            }
            prompt = runner._build_prompt(
                runner.topology.agent("lead"), [], 3, True,
            )
            self.assertIn("CURRENT_GOAL", prompt)
            self.assertNotIn("STALE_TERMINAL_GOAL", prompt)
            self.assertNotIn("stale-predicate", prompt)

    def test_active_experiment_state_is_delivered_once_and_then_only_on_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = OrganizationRunner(
                root, "Run one exact bounded experiment.", "claude",
                "flat", "experiment-once", root / "run",
            )
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            runner.workspaces["worker-a"] = Workspace(
                root, "worker-a", "main", root, [], base,
            )
            contract_sha = "c" * 64
            runner.active_experiment_by_worker["worker-a"] = contract_sha
            runner.experiment_contracts[contract_sha] = {
                "sha256": contract_sha,
                "work_item": "one-experiment",
                "mutable_file": "src/app.py",
                "evaluator_id": "truth-evaluator",
                "status": "active",
            }
            bootstrap = runner._build_prompt(
                runner.topology.agent("worker-a"), [], 3, True,
            )
            self.assertEqual(bootstrap.count(contract_sha), 1)
            self.assertEqual(bootstrap.count("src/app.py"), 1)
            runner.model_prompt_state_by_agent["worker-a"] = (
                runner._model_prompt_state("worker-a")
            )
            unchanged = runner._build_prompt(
                runner.topology.agent("worker-a"), [], 4, False,
            )
            self.assertNotIn(contract_sha, unchanged)

    def test_host_state_resolves_mission_commit_identity_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            launch = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            ancestor = subprocess.run(
                ["git", "rev-list", "--max-parents=0", "HEAD"],
                cwd=root, check=True, capture_output=True, text=True,
            ).stdout.strip()
            run_dir = root / "run"
            run_dir.mkdir()
            runner = OrganizationRunner(
                root, f"Compare exact candidate {ancestor}.", "claude",
                "flat", "host-state-run", run_dir,
            )
            runner.caller_head = launch
            runner.workspaces["lead"] = Workspace(
                root, "main", "main", root, [], launch,
            )
            state = runner._write_host_state_brief(0)
            record = state["mission_commit_inventory"]["mentioned_commits"][0]
            self.assertTrue(record["exists_as_commit"])
            self.assertEqual(record["commit"], ancestor)
            self.assertEqual(
                record["relation_to_launch_head"], "ancestor_of_launch_head",
            )
            self.assertEqual(
                json.loads((run_dir / "host-state.json").read_text())[
                    "content_sha256"
                ],
                state["content_sha256"],
            )


class OrganizationRunnerTests(unittest.TestCase):
    def test_flat_run_explores_then_emits_human_promotion_request(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            authority = root / "authority.md"
            authority.write_text("human-frozen standard\n", encoding="utf-8")
            subprocess.run(["git", "add", "authority.md"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "freeze authority"], cwd=root, check=True)
            run_dir = root / "devsession" / "agent-organizations" / "flat-promo-run"
            runner = OrganizationRunner(
                root, "Explore one reversible hypothesis and prepare promotion evidence.",
                "claude", "flat", "flat-promo-run", run_dir,
                max_rounds=9, max_experiments=1,
                protected_paths=["authority.md"],
            )
            release_reviewer = runner.governance.release_reviewer_id
            worker_candidate = {"sha": None}
            release_candidate = {"sha": None}

            def message(to, tag, content, candidate=None, work_item=None, risk=None):
                return {
                    "to": to, "tag": tag, "content": content,
                    "candidate": candidate, "workItem": work_item, "risk": risk,
                }

            def response(messages=None, state="idle", artifacts=None, candidate=None, risk=None, disposition="continue", final=False):
                return {
                    "messages": messages or [], "summary": "flat simulated turn",
                    "state": state, "artifacts": artifacts or [], "candidate": candidate,
                    "risk": risk, "disposition": disposition, "final": final,
                }

            def fake_run(session, prompt, schema, timeout_seconds):
                session.turn += 1
                agent_id = session.session_key
                if schema is RUN_CONCLUSION_SCHEMA:
                    return {
                        "value": _conclusion(
                            "The flat organization produced a "
                            "human-reviewable promotion proposal."
                        ),
                        "session_id": f"session-{agent_id}",
                        "usage": {},
                    }
                if agent_id.startswith("blind-verifier-"):
                    verified = agent_id[len("blind-verifier-"):]
                    return {
                        "value": {
                            "candidate": verified,
                            "verdict": "approved", "summary": "Checks passed.",
                            "evidence": ["Exact HEAD and dossier verified."],
                            "blockers": [],
                        },
                        "session_id": "blind", "usage": {},
                    }
                reply = response()
                if agent_id == "lead":
                    if session.turn == 1:
                        reply = response([message(
                            "worker-a", "plan",
                            "Run one bounded reversible experiment and hand back its exact candidate; preserve its generated receipt.",
                            None, "flat-promo-run/worker-a/r2", "high",
                        )])
                    elif (
                        release_candidate["sha"] is None
                        and "exact integration HEAD is" in prompt
                    ):
                        report = session.workspace.cwd / runner.artifact_staging_prefix / "promotion-dossier.md"
                        report.parent.mkdir(parents=True)
                        report.write_text("# Provisional promotion dossier\n", encoding="utf-8")
                        subprocess.run(
                            ["git", "add", "-f", runner.artifact_staging_prefix],
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
                        reply = response([message(
                            release_reviewer, "review",
                            "Veto or annotate the fully-sighted final dossier.",
                            release_candidate["sha"], "final-release", "release",
                        )], state="working")
                    elif (
                        release_candidate["sha"]
                        and "final-release" in prompt
                        and "NO_VETO" in prompt
                    ):
                        reply = response(
                            state="done", candidate=release_candidate["sha"],
                            risk="release", disposition="promote", final=True,
                        )
                elif agent_id == "worker-a":
                    if worker_candidate["sha"] is None:
                        (session.workspace.cwd / "app.py").write_text(
                            "print('reversible hypothesis')\n", encoding="utf-8",
                        )
                        subprocess.run(["git", "add", "app.py"], cwd=session.workspace.cwd, check=True)
                        subprocess.run(["git", "commit", "-qm", "sandbox experiment"], cwd=session.workspace.cwd, check=True)
                        worker_candidate["sha"] = subprocess.run(
                            ["git", "rev-parse", "HEAD"], cwd=session.workspace.cwd,
                            check=True, capture_output=True, text=True,
                        ).stdout.strip()
                        output = session.workspace.cwd / "out" / "tmp-worker-a-r2"
                        output.mkdir(parents=True)
                        (output / "receipt.json").write_text('{"result":"provisional"}\n', encoding="utf-8")
                        reply = response(
                            [message(
                                "lead", "handoff", "Sandbox experiment and provisional receipt ready.",
                                worker_candidate["sha"], "flat-promo-run/worker-a/r2", "high",
                            )],
                            state="working", artifacts=["out/tmp-worker-a-r2"],
                        )
                    else:
                        # Re-send the handoff so it is still in the lead inbox
                        # when the adversarial review completes; the sealed
                        # receipt bundle already holds the generated output.
                        shutil.rmtree(session.workspace.cwd / "out", ignore_errors=True)
                        reply = response(
                            [message(
                                "lead", "handoff", "Sandbox experiment and provisional receipt ready.",
                                worker_candidate["sha"], "flat-promo-run/worker-a/r2", "high",
                            )],
                            state="done",
                        )
                elif agent_id in {"auditor-a", "auditor-b"}:
                    # The final-review branch is checked first: the prior
                    # review evidence section embeds the earlier candidate
                    # assignment prose in later prompts.
                    if (
                        release_candidate["sha"]
                        and "fully-sighted final dossier" in prompt
                    ):
                        reply = response([message(
                            "lead", "decision",
                            f"NO_VETO {release_candidate['sha']}: dossier exposes provisional status and primary receipt.",
                            release_candidate["sha"], "final-release", "release",
                        )])
                    elif (
                        worker_candidate["sha"]
                        and "Adversarial review assignment" in prompt
                    ):
                        reply = response([message(
                            "lead", "decision",
                            f"NO_VETO {worker_candidate['sha']}: no blocking falsification; no truth approval implied.",
                            worker_candidate["sha"], "flat-promo-run/worker-a/r2", "high",
                        )])
                return {"value": reply, "session_id": f"session-{agent_id}", "usage": {}}

            worktree_parent = None
            try:
                with patch.object(SubscriptionSession, "run", new=fake_run):
                    result = runner.run()
                worktree_parent = Path(result["integration_workspace"]).parent
                self.assertEqual(result["status"], "completed")
                self.assertTrue(result["human_promotion_required"])
                self.assertFalse(result["canonical_effects_applied"])
                self.assertIn("conclusion", result)
                self.assertEqual(result["conclusion"]["generated_by"], "lead")
                self.assertTrue(Path(result["conclusion_json"]).is_file())
                self.assertTrue(Path(result["conclusion_markdown"]).is_file())
                self.assertEqual(
                    result["blind_review"]["candidate"],
                    release_candidate["sha"],
                )
                self.assertEqual(result["blind_review"]["verdict"], "approved")
                self.assertEqual(
                    result["verified_candidate"], release_candidate["sha"],
                )
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

    def test_flat_mixed_run_completes_exact_candidate_release(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "system-run"
            topology = get_topology("flat")
            provider_assignments = build_provider_assignments(
                topology, "claude", "codex",
            )
            evidence = root / "out" / "baseline.txt"
            evidence.parent.mkdir()
            evidence.write_text("A005 accepted\n", encoding="utf-8")
            runner = OrganizationRunner(
                root, "Change app.py to print shipped and verify it.", "mixed",
                "flat", "system-run", run_dir,
                max_rounds=4, max_concurrency=5,
                provider_assignments=provider_assignments,
                host_provider="claude", blind_verifier_provider="codex",
                evidence_paths=["out"],
            )
            release_reviewer = runner.governance.release_reviewer_id
            worker_candidate = {"sha": None}
            release_candidate = {"sha": None}
            seen_providers = {}

            def message(to, tag, content, candidate=None, work_item=None, risk=None):
                return {
                    "to": to, "tag": tag, "content": content,
                    "candidate": candidate, "workItem": work_item, "risk": risk,
                }

            def response(messages=None, state="idle", candidate=None, risk=None, disposition="continue", final=False):
                return {
                    "messages": messages or [], "summary": "simulated turn",
                    "state": state, "artifacts": [], "candidate": candidate,
                    "risk": risk, "disposition": disposition, "final": final,
                }

            def fake_run(session, prompt, schema, timeout_seconds):
                session.turn += 1
                agent_id = session.session_key
                seen_providers[agent_id] = session.provider
                if schema is RUN_CONCLUSION_SCHEMA:
                    return {
                        "value": _conclusion(
                            "The organization shipped the exact reviewed "
                            "candidate."
                        ),
                        "session_id": f"session-{agent_id}",
                        "usage": {},
                    }
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
                                "worker-a", "plan",
                                "Implement the app.py change and hand back the exact candidate.",
                                None, "app-change", "routine",
                            ),
                        ])
                    elif (
                        release_candidate["sha"] is None
                        and "exact integration HEAD is" in prompt
                    ):
                        release_candidate["sha"] = subprocess.run(
                            ["git", "rev-parse", "HEAD"], cwd=session.workspace.cwd,
                            check=True, capture_output=True, text=True,
                        ).stdout.strip()
                        reply = response([
                            message(
                                release_reviewer, "review",
                                "Approve exact release candidate independently.",
                                release_candidate["sha"], "final-release", "release",
                            ),
                        ], state="working")
                    elif (
                        release_candidate["sha"]
                        and "final-release" in prompt
                        and "NO_VETO" in prompt
                    ):
                        reply = response(
                            state="done", candidate=release_candidate["sha"],
                            risk="release", disposition="promote", final=True,
                        )
                elif agent_id == "worker-a":
                    if worker_candidate["sha"] is None:
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
                                "lead", "handoff", "Implementation and focused check complete.",
                                worker_candidate["sha"], "app-change", "routine",
                            ),
                        ], state="working")
                    else:
                        # Keep the handoff in the lead inbox until the
                        # adversarial review lands, then stand down.
                        reply = response([
                            message(
                                "lead", "handoff", "Implementation and focused check complete.",
                                worker_candidate["sha"], "app-change", "routine",
                            ),
                        ], state="done")
                elif agent_id in {"auditor-a", "auditor-b"}:
                    # The final-review branch is checked first: the prior
                    # review evidence section embeds the earlier candidate
                    # assignment prose in later prompts.
                    if (
                        release_candidate["sha"]
                        and "Approve exact release candidate independently" in prompt
                    ):
                        reply = response([
                            message(
                                "lead", "decision",
                                f"NO_VETO {release_candidate['sha']}: independent integrated review passes.",
                                release_candidate["sha"], "final-release", "release",
                            ),
                        ])
                    elif (
                        worker_candidate["sha"]
                        and "Adversarial review assignment" in prompt
                    ):
                        reply = response([
                            message(
                                "lead", "decision",
                                f"NO_VETO {worker_candidate['sha']}: diff and focused evidence pass.",
                                worker_candidate["sha"], "app-change", "routine",
                            ),
                        ])

                return {"value": reply, "session_id": f"session-{agent_id}", "usage": {}}

            worktree_parent = None
            try:
                with patch.object(SubscriptionSession, "run", new=fake_run):
                    result = runner.run()
                worktree_parent = Path(result["integration_workspace"]).parent
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["rounds"], 6)
                self.assertEqual(result["working_rounds"], 4)
                self.assertEqual(result["closeout_rounds"], 2)
                self.assertGreaterEqual(result["completed_turns"], 8)
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
                self.assertEqual(seen_providers["lead"], "claude")
                self.assertEqual(seen_providers["worker-a"], "codex")
                self.assertEqual(
                    seen_providers[f"blind-verifier-{release_candidate['sha']}"],
                    "codex",
                )
            finally:
                if worktree_parent is not None:
                    shutil.rmtree(worktree_parent, ignore_errors=True)

    def test_flat_no_promotion_report_ends_without_round_limit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "no-promotion"
            runner = OrganizationRunner(
                root, "Determine whether a change is justified.", "claude",
                "flat", "no-promotion", run_dir,
                max_rounds=5, max_closeout_rounds=2,
            )
            release_reviewer = runner.governance.release_reviewer_id
            report = {"candidate": None}

            def message(to, tag, content, candidate=None, work_item=None, risk=None):
                return {
                    "to": to, "tag": tag, "content": content,
                    "candidate": candidate, "workItem": work_item, "risk": risk,
                }

            def response(
                messages=None, state="idle", candidate=None, risk=None,
                disposition="continue", final=False,
            ):
                return {
                    "messages": messages or [],
                    "summary": "bounded no-promotion turn",
                    "state": state,
                    "artifacts": [],
                    "candidate": candidate,
                    "risk": risk,
                    "disposition": disposition,
                    "final": final,
                }

            def fake_run(session, prompt, schema, timeout_seconds):
                session.turn += 1
                agent_id = session.session_key
                if schema is RUN_CONCLUSION_SCHEMA:
                    return {
                        "value": _conclusion(
                            "The reviewed evidence supports no promotion."
                        ),
                        "session_id": f"session-{agent_id}",
                        "usage": {},
                    }
                if agent_id == "lead" and session.turn == 1:
                    # The lead authors the terminal dossier itself: RecCli
                    # commits the staged artifact after the turn, so the
                    # review names the host-materialized report identity.
                    dossier = (
                        session.workspace.cwd
                        / runner.artifact_staging_prefix
                        / "no-promotion.md"
                    )
                    dossier.parent.mkdir(parents=True)
                    dossier.write_text(
                        "# No promotion\n\nThe bounded evidence rejects a change.\n",
                        encoding="utf-8",
                    )
                    reply = response([
                        message(
                            release_reviewer, "review",
                            "Independently review the exact no-promotion dossier.",
                            HOST_CANDIDATE, "final-no-promotion", "release",
                        ),
                    ], state="working", candidate=HOST_CANDIDATE,
                        risk="release", disposition="no_promotion")
                elif (
                    agent_id == release_reviewer
                    and report["candidate"]
                    and "final-no-promotion" in prompt
                ):
                    reply = response([
                        message(
                            "lead", "decision",
                            (
                                f"NO_VETO {report['candidate']}: exact "
                                "no-promotion dossier is supported."
                            ),
                            report["candidate"], "final-no-promotion", "release",
                        ),
                    ])
                elif (
                    agent_id == "lead"
                    and report["candidate"]
                    and "NO_VETO" in prompt
                ):
                    reply = response(
                        state="done", candidate=report["candidate"],
                        risk="release", disposition="no_promotion", final=True,
                    )
                else:
                    reply = response(
                        state="working" if agent_id == "lead" else "idle"
                    )
                return {
                    "value": reply,
                    "session_id": f"session-{agent_id}",
                    "usage": {},
                }

            original_run_turn = runner._run_turn

            def capture_report(agent, round_number):
                result = original_run_turn(agent, round_number)
                reply = result.get("reply") or {}
                if agent.agent_id == "lead" and report["candidate"] is None:
                    for sent in reply.get("messages", []):
                        if (
                            sent.get("workItem") == "final-no-promotion"
                            and sent.get("candidate")
                        ):
                            report["candidate"] = sent["candidate"]
                return result

            worktree_parent = None
            try:
                with (
                    patch.object(SubscriptionSession, "run", new=fake_run),
                    patch.object(runner, "_run_turn", side_effect=capture_report),
                ):
                    result = runner.run()
                worktree_parent = Path(result["integration_workspace"]).parent
                self.assertEqual(result["status"], "completed_no_promotion")
                self.assertLessEqual(result["rounds"], 5)
                self.assertIsNone(result["verified_candidate"])
                self.assertEqual(result["no_promotion_report"], report["candidate"])
                self.assertIsNone(result["promotion_request"])
                self.assertEqual(
                    result["conclusion"]["promotion_readiness"], "no_candidate",
                )
                self.assertEqual(
                    result["conclusion"]["no_promotion_report"],
                    report["candidate"],
                )
            finally:
                if worktree_parent is not None:
                    shutil.rmtree(worktree_parent, ignore_errors=True)


class DeadLaneReleaseTests(unittest.TestCase):
    def test_consecutive_failures_release_the_goal_and_notify_the_lead(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "dead-lane"
            runner = OrganizationRunner(
                root, "Close the cone gate.", "claude",
                "flat", "dead-lane", run_dir,
            )
            accepted, reason = runner._bind_worker_goal(
                worker_id="worker-a",
                manager_id="lead",
                work_item="cone-fix",
                objective="Fix the cone refinement and pass its scoring test.",
                risk="high",
                round_number=1,
            )
            self.assertTrue(accepted, reason)
            runner.inboxes["worker-a"].append({
                "from": "lead", "tag": "plan", "content": "stale delegation",
            })
            worker = runner.topology.agent("worker-a")

            runner._record_turn_failure(worker, "schema rejected", 2)
            self.assertEqual(
                runner.worker_goals["worker-a"]["status"], "cancelled",
                "the first failed worker turn must release the goal: blind "
                "retry of a dead lane costs a full turn timeout",
            )
            self.assertEqual(runner.inboxes["worker-a"], [])
            blockers = [
                message for message in runner.inboxes["lead"]
                if message.get("tag") == "blocker"
                and "failed a provider turn" in message.get("content", "")
            ]
            self.assertEqual(len(blockers), 1)
            # The released predicate/work is rebindable to another worker.
            accepted, reason = runner._bind_worker_goal(
                worker_id="worker-b",
                manager_id="lead",
                work_item="cone-fix",
                objective="Fix the cone refinement and pass its scoring test.",
                risk="high",
                round_number=3,
            )
            self.assertTrue(accepted, reason)

    def test_lead_failures_never_release_worker_goals(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "lead-fail"
            runner = OrganizationRunner(
                root, "Close the cone gate.", "claude",
                "flat", "lead-fail", run_dir,
            )
            lead = runner.topology.agent("lead")
            runner._record_turn_failure(lead, "timeout", 2)
            runner._record_turn_failure(lead, "timeout", 3)
            self.assertEqual(
                runner._consecutive_turn_failures["lead"], 2,
            )
            self.assertEqual(runner.inboxes["lead"], [])


class GateProposalExtractionTests(unittest.TestCase):
    def _runner_with_commit(self, root, manifest_text=None):
        run_dir = root / "devsession" / "agent-organizations" / "gate"
        runner = OrganizationRunner(
            root, "Author the next gate.", "claude", "flat", "gate", run_dir,
        )
        runner.workspaces["lead"] = Workspace(root, "test", "test-main", root, [])
        staging = root / runner.artifact_staging_prefix / "gate-proposal"
        (staging / "files").mkdir(parents=True)
        (staging / "files" / "predicate.json").write_text(
            '{"id": "shell-detection-v1", "tolerance": 1e-3}\n',
            encoding="utf-8",
        )
        if manifest_text is None:
            manifest_text = json.dumps({
                "schema": "reccli.organization-gate-proposal.v1",
                "predicate_id": "shell-detection-v1",
                "evaluator_id": "geometry-eval-v1",
                "rationale": "Real-scan shell fixtures need a declared gate.",
                "baseline_command": ".venv/bin/python scripts/score.py",
                "measured_baseline": 0.42,
                "proposed_tolerance": 0.001,
                "discrimination": {
                    "truth_exact_command": "scripts/score.py truth.stl",
                    "truth_exact_score": 0.0000004,
                    "corrupted_command": "scripts/score.py corrupted.stl",
                    "corrupted_score": 0.31,
                },
                "what_fools_this_gate": (
                    "A shell duplicated with zero offset scores identically; "
                    "the thickness histogram cannot see coincident surfaces."
                ),
                "files": [{
                    "path": (
                        f"{runner.artifact_staging_prefix}/gate-proposal/"
                        "files/predicate.json"
                    ),
                    "target": "benchmarks/gates/shell-detection-v1.json",
                }],
            })
        (staging / "gate-proposal.json").write_text(
            manifest_text + "\n", encoding="utf-8",
        )
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "stage gate proposal"],
            cwd=root, check=True,
        )
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return runner, head

    def test_staged_proposal_is_extracted_and_normalized(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner, head = self._runner_with_commit(root)
            proposal = runner._extract_gate_proposal(head)
            self.assertEqual(proposal["predicate_id"], "shell-detection-v1")
            self.assertEqual(
                proposal["files"][0]["target"],
                "benchmarks/gates/shell-detection-v1.json",
            )
            self.assertNotIn("error", proposal)
            self.assertEqual(
                proposal["discrimination"]["corrupted_score"], 0.31,
            )
            self.assertIn("coincident", proposal["what_fools_this_gate"])

    def test_invalid_staged_proposal_warns_the_author_at_materialization(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner, head = self._runner_with_commit(
                root,
                manifest_text=json.dumps({
                    "schema": "reccli.organization-gate-proposal.v1",
                    "predicate_id": None,
                    "evaluator_id": None,
                    "rationale": "substance lives in the dossier",
                    "baseline_command": None,
                    "measured_baseline": None,
                    "proposed_tolerance": 0.005,
                    "files": [],
                }),
            )
            worker = runner.topology.agent("worker-a")
            runner._warn_invalid_gate_proposal(
                worker, runner.workspaces["lead"], head, 2,
            )
            blockers = [
                message for message in runner.inboxes["worker-a"]
                if message.get("tag") == "blocker"
                and "cannot be ratified as-is" in message.get("content", "")
            ]
            self.assertEqual(len(blockers), 1)
            events = [
                json.loads(line)
                for line in (
                    runner.run_dir / "events.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(any(
                event["type"] == "gate_proposal.invalid"
                for event in events
            ))

    def test_foreign_run_prefix_is_a_run_artifact_and_adoptable(self):
        # Chain adoption: a successor consumes a packet pinned under the
        # AUTHORING run's prefix. Run-scoped matching silently filtered those
        # files at capture and would have misclassified them as an
        # implementation; both must treat any staging-root path as artifact.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "adopt"
            runner = OrganizationRunner(
                root, "Carry the packet to ratification.", "claude",
                "flat", "adopt", run_dir,
            )
            self.assertTrue(runner._artifact_path(
                ".reccli-org-artifacts/adopt/report.md",
            ))
            self.assertTrue(runner._artifact_path(
                ".reccli-org-artifacts/authoring-run/gate-proposal/x.json",
            ))
            self.assertFalse(runner._artifact_path("src/app.py"))

    def test_adopted_foreign_prefix_proposal_is_discovered(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "adopt"
            runner = OrganizationRunner(
                root, "Carry the packet to ratification.", "claude",
                "flat", "adopt", run_dir,
            )
            runner.workspaces["lead"] = Workspace(
                root, "test", "test-main", root, [],
            )
            staging = (
                root / ".reccli-org-artifacts" / "authoring-run"
                / "gate-proposal"
            )
            (staging / "files").mkdir(parents=True)
            (staging / "files" / "predicate.json").write_text(
                '{"id": "envelope-coverage-v1"}\n', encoding="utf-8",
            )
            (staging / "gate-proposal.json").write_text(json.dumps({
                "schema": "reccli.organization-gate-proposal.v1",
                "predicate_id": "envelope-coverage-v1",
                "evaluator_id": "candidate-qualification-v1",
                "rationale": "Adopted byte-identical from the authoring run.",
                "baseline_command": "scripts/probe.py --timeout 120",
                "measured_baseline": 999.0,
                "proposed_tolerance": 0.005,
                "discrimination": {
                    "truth_exact_command": "scripts/probe.py truth.stl",
                    "truth_exact_score": 0.0,
                    "corrupted_command": "scripts/probe.py corrupted.stl",
                    "corrupted_score": 0.735,
                },
                "what_fools_this_gate": (
                    "Coverage counts supported area only; a support that "
                    "touches without bonding is invisible to this gate."
                ),
                "files": [{
                    "path": (
                        ".reccli-org-artifacts/authoring-run/gate-proposal/"
                        "files/predicate.json"
                    ),
                    "target": "benchmarks/gates/envelope-coverage-v1.json",
                }],
            }) + "\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", ".reccli-org-artifacts"], cwd=root, check=True,
            )
            subprocess.run(
                ["git", "commit", "-qm", "adopt packet"], cwd=root, check=True,
            )
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            proposal = runner._extract_gate_proposal(head)
            self.assertIsNotNone(proposal)
            self.assertNotIn("error", proposal)
            self.assertEqual(
                proposal["predicate_id"], "envelope-coverage-v1",
            )
            self.assertEqual(
                proposal["files"][0]["target"],
                "benchmarks/gates/envelope-coverage-v1.json",
            )

    def test_gate_that_fails_its_own_discrimination_is_rejected(self):
        # The cylinder-scorer shape: truth-exact input scoring far above the
        # proposed tolerance means the gate measures something other than
        # what it claims. Ratification must see this as an error, not prose.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner, head = self._runner_with_commit(
                root,
                manifest_text=json.dumps({
                    "schema": "reccli.organization-gate-proposal.v1",
                    "predicate_id": "shell-detection-v1",
                    "evaluator_id": "geometry-eval-v1",
                    "rationale": "Real-scan shells need a declared gate.",
                    "baseline_command": "scripts/score.py",
                    "measured_baseline": 0.42,
                    "proposed_tolerance": 0.000001,
                    "discrimination": {
                        "truth_exact_command": "scripts/score.py truth.stl",
                        "truth_exact_score": 0.000061,
                        "corrupted_command": "scripts/score.py bad.stl",
                        "corrupted_score": 0.31,
                    },
                    "what_fools_this_gate": (
                        "Nothing we know of; the histogram separates shells "
                        "from solids in every probe we ran this week."
                    ),
                    "files": [{
                        "path": (
                            ".reccli-org-artifacts/gate/gate-proposal/"
                            "files/predicate.json"
                        ),
                        "target": "benchmarks/gates/shell-detection-v1.json",
                    }],
                }),
            )
            proposal = runner._extract_gate_proposal(head)
            self.assertIn("error", proposal)
            self.assertIn("discrimination proof", proposal["error"])
            self.assertIn("truth-exact", proposal["error"])

    def test_candidate_without_proposal_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "gate"
            runner = OrganizationRunner(
                root, "Author the next gate.", "claude",
                "flat", "gate", run_dir,
            )
            runner.workspaces["lead"] = Workspace(
                root, "test", "test-main", root, [],
            )
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertIsNone(runner._extract_gate_proposal(head))

    def test_traversal_target_is_reported_not_applied(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner, head = self._runner_with_commit(
                root,
                manifest_text=json.dumps({
                    "schema": "reccli.organization-gate-proposal.v1",
                    "predicate_id": "shell-detection-v1",
                    "evaluator_id": "geometry-eval-v1",
                    "rationale": "Real-scan shell fixtures need a gate.",
                    "baseline_command": ".venv/bin/python scripts/score.py",
                    "measured_baseline": 0.42,
                    "proposed_tolerance": 0.001,
                    "discrimination": {
                        "truth_exact_command": "scripts/score.py truth.stl",
                        "truth_exact_score": 0.0000004,
                        "corrupted_command": "scripts/score.py corrupted.stl",
                        "corrupted_score": 0.31,
                    },
                    "what_fools_this_gate": (
                        "A shell duplicated with zero offset scores "
                        "identically; coincident surfaces are invisible."
                    ),
                    "files": [{
                        "path": (
                            ".reccli-org-artifacts/gate/gate-proposal/"
                            "files/predicate.json"
                        ),
                        "target": "../outside/escape.json",
                    }],
                }),
            )
            proposal = runner._extract_gate_proposal(head)
            self.assertIn("error", proposal)
            self.assertIn("traversal", proposal["error"])


class DispositionMarkerTests(unittest.TestCase):
    def test_bounded_labels_strip_and_markers_parse(self):
        from reccli.organization import disposition_marker

        cases = {
            "NO_VETO abc123: verified.": "NO_VETO",
            "FORMAL DISPOSITION: NO_VETO — candidate d2e314e1 verified":
                "NO_VETO",
            "Disposition: VETO abc123: discrimination fails": "VETO",
            "verdict: APPROVED abc123": "APPROVED",
            "REVIEW: REVIEWED abc123": "REVIEWED",
        }
        for content, expected in cases.items():
            self.assertEqual(
                disposition_marker(content), expected, content,
            )

    def test_negations_and_prose_fail_closed(self):
        from reccli.organization import disposition_marker

        for content in (
            "NOT NO_VETO abc123",
            "I would NO_VETO this if the probe passed",
            "FORMAL DISPOSITION: pending further review",
            "OPINION: NO_VETO seems right",
            "",
            None,
        ):
            self.assertIsNone(disposition_marker(content), content)


class FinalLedgerUnificationTests(unittest.TestCase):
    def _governance(self, root):
        run_dir = root / "devsession" / "agent-organizations" / "ledger"
        runner = OrganizationRunner(
            root, "Close the packet.", "claude", "flat", "ledger", run_dir,
        )
        return runner.governance

    def test_either_auditors_no_veto_satisfies_final_approval(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            governance = self._governance(root)
            release = governance.release_reviewer_id
            other = next(
                auditor
                for auditor in governance.topology.final_reviewer_pool
                if auditor != release
            )
            candidate = "a" * 40
            governance.record_decision(other, {
                "tag": "decision", "to": "lead",
                "candidate": candidate,
                "content": f"NO_VETO {candidate}: closure fidelity verified.",
            })
            self.assertEqual(
                governance.missing_final_approvers(candidate), [],
                "run thirteen held three durable NO_VETOs against an empty "
                "approvals ledger because only the hash-picked reviewer "
                "counted",
            )

    def test_any_auditors_veto_blocks_regardless_of_hash_pick(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            governance = self._governance(root)
            release = governance.release_reviewer_id
            other = next(
                auditor
                for auditor in governance.topology.final_reviewer_pool
                if auditor != release
            )
            candidate = "b" * 40
            governance.record_decision(release, {
                "tag": "decision", "to": "lead",
                "candidate": candidate,
                "content": f"NO_VETO {candidate}: packet verified.",
            })
            governance.record_decision(other, {
                "tag": "decision", "to": "lead",
                "candidate": candidate,
                "content": f"VETO {candidate}: discrimination claim fails "
                           "under re-execution.",
            })
            self.assertNotEqual(
                governance.missing_final_approvers(candidate), [],
                "a veto auditor's veto must block whichever auditor the "
                "hash picked",
            )


class ReleaseLaneLivenessTests(unittest.TestCase):
    def test_dropped_report_review_bounces_to_the_sender(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "bounce"
            runner = OrganizationRunner(
                root, "Route the dossier review.", "claude",
                "flat", "bounce", run_dir,
            )
            runner.workspaces["lead"] = Workspace(
                root, "test", "test-main", root, [],
            )
            runner.caller_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            report = root / ".reccli-org-artifacts" / "bounce" / "dossier.md"
            report.parent.mkdir(parents=True)
            report.write_text("# Dossier\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", ".reccli-org-artifacts"], cwd=root, check=True,
            )
            subprocess.run(
                ["git", "commit", "-qm", "dossier"], cwd=root, check=True,
            )
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            runner._deliver_message("lead", {
                "to": "auditor-a", "tag": "review",
                "content": "Review the staged ratification dossier.",
                "candidate": head, "workItem": None, "risk": "release",
            }, 2)
            self.assertEqual(
                runner.inboxes["auditor-a"], [],
                "the malformed report review must not deliver",
            )
            bounces = [
                message for message in runner.inboxes["lead"]
                if message.get("tag") == "blocker"
                and "was dropped" in message.get("content", "")
                and "workItem" in message.get("content", "")
            ]
            self.assertEqual(
                len(bounces), 1,
                "a silently dropped review request strands the release lane",
            )

    def test_undisposed_assignment_is_nudged_exactly_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "nudge"
            runner = OrganizationRunner(
                root, "Route the dossier review.", "claude",
                "flat", "nudge", run_dir,
            )
            runner.governance.assignments["c" * 40] = {
                "candidate": "c" * 40,
                "workItem": "packet-review",
                "risk": "release",
                "workerId": "worker-b",
                "primaryManagerId": "lead",
                "reviewerId": "auditor-a",
                "status": "assigned",
            }
            self.assertEqual(runner._nudge_pending_reviews(3), 1)
            reviews = [
                message for message in runner.inboxes["auditor-a"]
                if "awaits your recorded" in message.get("content", "")
                and message.get("candidate") == "c" * 40
            ]
            self.assertEqual(len(reviews), 1)
            self.assertEqual(
                runner._nudge_pending_reviews(4), 0,
                "a reviewer that ignores the nudge lets the run end honestly",
            )


class CompletedGoalWakesSupervisorTests(unittest.TestCase):
    def test_state_done_completion_wakes_the_lead(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "wake"
            runner = OrganizationRunner(
                root, "Stage the governance packet.", "claude",
                "flat", "wake", run_dir,
            )
            accepted, reason = runner._bind_worker_goal(
                worker_id="worker-b",
                manager_id="lead",
                work_item="packet-close",
                objective=(
                    "Complete the gate packet manifest and reproduce the "
                    "discrimination probe."
                ),
                risk="high",
                round_number=1,
            )
            self.assertTrue(accepted, reason)
            reply = _reply()
            reply.update({"state": "done", "candidate": None})
            runner._update_worker_goal_after_reply("worker-b", reply, 2)
            self.assertEqual(
                runner.worker_goals["worker-b"]["status"], "completed",
            )
            wakes = [
                message for message in runner.inboxes["lead"]
                if "completed goal 'packet-close'" in message.get("content", "")
            ]
            self.assertEqual(
                len(wakes), 1,
                "completed work must schedule its supervisor once; the "
                "close-out run stalled over finished work in exactly this "
                "silence",
            )
            scheduled = {
                agent.agent_id for agent in runner._select_agents(3)
            }
            self.assertIn("lead", scheduled)


class GateAuthoringBindingTests(unittest.TestCase):
    def _runner(self, root, admission=None):
        run_dir = root / "devsession" / "agent-organizations" / "bind"
        runner = OrganizationRunner(
            root, "Author the envelope gate.", "claude",
            "flat", "bind", run_dir, admission=admission,
        )
        runner.experiment_policy = {
            "promotion_requires_goal_progress": True,
            "evaluators": {
                "geometry-eval-v1": {
                    "id": "geometry-eval-v1",
                    "profile_sha256": "p" * 64,
                    "immutable_ground_truth_sha256": "g" * 64,
                    "predicates": {
                        "cone-parameter-v1": {
                            "id": "cone-parameter-v1",
                            "goal_class": "production_pipeline",
                            "comparison_rule_id": "minimize",
                        },
                    },
                },
            },
        }
        return runner

    def test_gate_authoring_admission_binds_without_a_profile(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            admission = dict(VALID_ADMISSION)
            admission["work_class"] = "uncertainty_reduction"
            runner = self._runner(root, admission=admission)
            measurement, error = runner._resolve_goal_measurement(
                goal_class="evaluator_infrastructure",
                predicate_id=None,
                evaluator_id=None,
            )
            self.assertEqual(error, "")
            self.assertIsNone(measurement)

    def test_unclassed_delegation_binds_under_a_gate_authoring_admission(self):
        # Round one of three consecutive ceremony runs died on the lead
        # omitting goalClass; the admission already declares the run's shape.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            admission = dict(VALID_ADMISSION)
            admission["work_class"] = "uncertainty_reduction"
            runner = self._runner(root, admission=admission)
            measurement, error = runner._resolve_goal_measurement(
                goal_class=None, predicate_id=None, evaluator_id=None,
            )
            self.assertEqual(error, "")
            self.assertIsNone(measurement)

    def test_explicit_production_request_still_demands_measurement(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            admission = dict(VALID_ADMISSION)
            admission["work_class"] = "uncertainty_reduction"
            runner = self._runner(root, admission=admission)
            measurement, error = runner._resolve_goal_measurement(
                goal_class="production_pipeline",
                predicate_id="no-such-predicate",
                evaluator_id=None,
            )
            self.assertIn("unevaluable", error)
            self.assertIsNone(measurement)

    def test_a_materialized_implementation_stands_down_the_deadline(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "impl"
            runner = OrganizationRunner(
                root, "Close the envelope gate.", "claude", "flat", "impl",
                run_dir, max_rounds=6, max_experiments=2,
                admission=dict(VALID_ADMISSION),
            )
            runner.experiment_policy = {"evaluators": {}}
            deadline = runner._experiment_contract_deadline
            self.assertTrue(
                runner._experiment_contract_deadline_passed(deadline),
            )
            runner.candidate_kinds["c" * 40] = {
                "candidate": "c" * 40, "kind": "implementation",
                "paths": ["src/scan2param/segmentation/convert.py"],
            }
            self.assertFalse(
                runner._experiment_contract_deadline_passed(deadline),
                "a materialized implementation candidate IS the executed "
                "thing this deadline detects the absence of",
            )

    def test_ceremony_admission_stands_down_the_contract_deadline(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            admission = dict(VALID_ADMISSION)
            admission["work_class"] = "uncertainty_reduction"
            run_dir = root / "devsession" / "agent-organizations" / "cd"
            runner = OrganizationRunner(
                root, "Carry the packet.", "claude", "flat", "cd", run_dir,
                max_rounds=6, max_experiments=2, admission=admission,
            )
            runner.experiment_policy = {"evaluators": {}}
            deadline = runner._experiment_contract_deadline
            self.assertFalse(
                runner._experiment_contract_deadline_passed(deadline),
                "a run whose admission declares no experiments cannot be "
                "executed by the no-contract deadline",
            )

    def test_deployable_admission_still_requires_a_profile(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root, admission=dict(VALID_ADMISSION))
            measurement, error = runner._resolve_goal_measurement(
                goal_class="evaluator_infrastructure",
                predicate_id=None,
                evaluator_id=None,
            )
            self.assertIn("gate-authoring", error)
            self.assertIsNone(measurement)

    def test_production_binding_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root)
            measurement, error = runner._resolve_goal_measurement(
                goal_class="production_pipeline",
                predicate_id="cone-parameter-v1",
                evaluator_id="geometry-eval-v1",
            )
            self.assertEqual(error, "")
            self.assertEqual(
                measurement["predicate_id"], "cone-parameter-v1",
            )


class StrictSchemaConformanceTests(unittest.TestCase):
    """Every model-facing schema must satisfy OpenAI strict mode.

    Strict structured output requires every declared property to appear in
    `required`; optionality is expressed only through anyOf-with-null on the
    field itself. A property missing from `required` makes Codex reject the
    response_format before the model runs, while Claude tolerates it, so the
    defect ships invisibly until a codex lane executes. This killed every
    codex turn in the first live flat run.
    """

    def _assert_strict(self, node, path="$"):
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                declared = set(properties)
                required = set(node.get("required") or [])
                self.assertEqual(
                    declared, required,
                    f"{path}: strict mode requires every property in "
                    f"'required'; missing {sorted(declared - required)}, "
                    f"extra {sorted(required - declared)}",
                )
            for key, value in node.items():
                self._assert_strict(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                self._assert_strict(value, f"{path}[{index}]")

    def test_all_provider_schemas_are_strict_mode_valid(self):
        from reccli.organization import BLIND_REVIEW_SCHEMA

        for name, schema in (
            ("AGENT_REPLY_SCHEMA", AGENT_REPLY_SCHEMA),
            ("BLIND_REVIEW_SCHEMA", BLIND_REVIEW_SCHEMA),
            ("RUN_CONCLUSION_SCHEMA", RUN_CONCLUSION_SCHEMA),
        ):
            self._assert_strict(schema, name)


class NoOpDispositionTests(unittest.TestCase):
    def test_contradictory_dispositions_normalize_instead_of_failing_turns(self):
        # Workers say "no_op" to mean "nothing this turn". Raising here failed
        # whole turns with no feedback to the model across two live runs; the
        # safe reading of every contradiction is an ordinary continue.
        reply = _reply()
        reply.update({"disposition": "no_op", "final": False})
        normalized = validate_agent_reply(reply)
        self.assertEqual(normalized["disposition"], "continue")
        self.assertFalse(normalized["final"])

        reply = _reply()
        reply.update({
            "disposition": "no_op", "final": True, "candidate": "abc123",
        })
        normalized = validate_agent_reply(reply)
        self.assertEqual(normalized["disposition"], "continue")
        self.assertFalse(normalized["final"])

        reply = _reply()
        reply.update({"disposition": "continue", "final": True})
        self.assertFalse(validate_agent_reply(reply)["final"])

        # The lead's intended terminal no_op passes through untouched.
        reply = _reply()
        reply.update({
            "disposition": "no_op", "final": True, "candidate": None,
            "state": "done",
        })
        normalized = validate_agent_reply(reply)
        self.assertEqual(normalized["disposition"], "no_op")
        self.assertTrue(normalized["final"])

    def test_stale_reported_paths_skip_sealing_instead_of_failing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "stale"
            runner = OrganizationRunner(
                root, "Close the cone gate.", "claude",
                "flat", "stale", run_dir,
            )
            runner.workspaces["worker-a"] = Workspace(
                root, "test", "test-main", root, [],
            )
            reply = _reply()
            reply["artifacts"] = ["discarded-by-host-reset.json"]
            bundle = runner._seal_reported_artifacts(
                runner.topology.agent("worker-a"), reply, 4,
            )
            self.assertIsNone(
                bundle,
                "a stale path after host discard-and-reset must be skipped, "
                "not fail the turn",
            )
            events = [
                json.loads(line)
                for line in (run_dir / "events.jsonl").read_text(
                    encoding="utf-8",
                ).splitlines()
            ]
            self.assertTrue(any(
                event["type"] == "artifacts.stale_reported_paths"
                and event["paths"] == ["discarded-by-host-reset.json"]
                for event in events
            ))

    def test_supervisor_experiment_note_names_the_declared_ids(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "ids"
            runner = OrganizationRunner(
                root, "Close the cone gate.", "claude",
                "flat", "ids", run_dir,
            )
            runner.experiment_policy_path = "experiment-policy.json"
            runner.experiment_policy = {
                "evaluators": {
                    "geometry-eval-v1": {
                        "predicates": {"cone-parameter-v1": {}},
                    },
                },
            }
            runner.workspaces["lead"] = Workspace(
                root, "test", "test-main", root, [],
            )
            prompt = runner._build_prompt(
                runner.topology.agent("lead"), [], 1, True,
            )
            self.assertIn("geometry-eval-v1", prompt)
            self.assertIn("cone-parameter-v1", prompt)

    def test_finalization_attempt_stands_down_the_contract_deadline(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "deadline"
            runner = OrganizationRunner(
                root, "Close the cylinder gate.", "claude",
                "flat", "deadline", run_dir,
                max_rounds=6, max_experiments=2,
            )
            runner.experiment_policy = {"evaluators": {}}
            deadline = runner._experiment_contract_deadline
            self.assertTrue(
                runner._experiment_contract_deadline_passed(deadline),
            )
            runner._finalization_attempted = True
            self.assertFalse(
                runner._experiment_contract_deadline_passed(deadline),
                "a run attempting terminal closure is not a run with "
                "nothing to execute",
            )

    def test_finalizer_prompt_teaches_the_dossier_mechanics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "recipe"
            runner = OrganizationRunner(
                root, "Close the cylinder gate.", "claude",
                "flat", "recipe", run_dir,
            )
            runner.workspaces["lead"] = Workspace(
                root, "test", "test-main", root, [],
            )
            prompt = runner._build_prompt(
                runner.topology.agent("lead"), [], 1, True,
            )
            self.assertIn("author the dossier YOURSELF", prompt)
            self.assertIn(runner.governance.release_reviewer_id, prompt)

    def test_probe_outputs_on_a_status_reply_defer_sealing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "probe"
            runner = OrganizationRunner(
                root, "Close the cone gate.", "claude",
                "flat", "probe", run_dir,
            )
            (root / "baseline-output.json").write_text(
                '{"cone_parameter_error": 0.00027295462432978693}\n',
                encoding="utf-8",
            )
            runner.workspaces["worker-a"] = Workspace(
                root, "test", "test-main", root, [],
            )
            reply = _reply()
            reply["artifacts"] = ["baseline-output.json"]
            bundle = runner._seal_reported_artifacts(
                runner.topology.agent("worker-a"), reply, 2,
            )
            self.assertIsNone(
                bundle,
                "generated outputs on a status-only reply must defer "
                "sealing, not fail the turn",
            )
            events = [
                json.loads(line)
                for line in (run_dir / "events.jsonl").read_text(
                    encoding="utf-8",
                ).splitlines()
            ]
            self.assertTrue(any(
                event["type"] == "artifacts.unsealed_probe_outputs"
                and event["paths"] == ["baseline-output.json"]
                for event in events
            ))
            self.assertEqual(runner._experiment_used(), 0)

    def test_lead_no_op_ends_run_as_successful_terminal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            run_dir = root / "devsession" / "agent-organizations" / "no-op"
            runner = OrganizationRunner(
                root, "Verify the controls still hold.", "claude",
                "flat", "no-op", run_dir,
                max_rounds=4, max_closeout_rounds=0,
                admission=VALID_ADMISSION,
            )
            prompts = {}

            def fake_run(session, prompt, schema, timeout_seconds):
                session.turn += 1
                agent_id = session.session_key
                prompts.setdefault(agent_id, prompt)
                if schema is RUN_CONCLUSION_SCHEMA:
                    return {
                        "value": _conclusion(
                            "The done condition was already satisfied."
                        ),
                        "session_id": f"session-{agent_id}",
                        "usage": {},
                    }
                if agent_id == "lead":
                    reply = {
                        "messages": [],
                        "summary": (
                            "The done condition is already satisfied: the "
                            "reviewed candidate is merged-ready with the "
                            "suite passing."
                        ),
                        "state": "done",
                        "artifacts": [],
                        "candidate": None,
                        "risk": None,
                        "disposition": "no_op",
                        "final": True,
                    }
                else:
                    reply = _reply()
                return {
                    "value": reply,
                    "session_id": f"session-{agent_id}",
                    "usage": {},
                }

            worktree_parent = None
            try:
                with patch.object(SubscriptionSession, "run", new=fake_run):
                    result = runner.run()
                worktree_parent = Path(result["integration_workspace"]).parent
                self.assertEqual(result["status"], "completed_no_op")
                self.assertEqual(result["rounds"], 1)
                self.assertEqual(result["finalized_by"], "lead")
                self.assertIsNone(result["verified_candidate"])
                self.assertFalse(result["canonical_effects_applied"])
                conclusion = json.loads(
                    (run_dir / "run-conclusion.json").read_text(
                        encoding="utf-8",
                    )
                )
                self.assertEqual(
                    conclusion["terminal_status"], "completed_no_op",
                )
                events = [
                    json.loads(line)
                    for line in (run_dir / "events.jsonl").read_text(
                        encoding="utf-8",
                    ).splitlines()
                ]
                no_op_events = [
                    event for event in events
                    if event["type"] == "finalization.no_op"
                ]
                self.assertEqual(len(no_op_events), 1)
                self.assertEqual(
                    no_op_events[0]["done_condition"],
                    VALID_ADMISSION["done_condition"],
                )
                degraded = [
                    event for event in events
                    if event["type"] == "delegation.degraded"
                ]
                self.assertEqual(
                    degraded, [],
                    "a lead no_op must not trigger forged fallback delegation",
                )
                self.assertIn("Admission contract", prompts["lead"])
                self.assertIn("disposition=no_op", prompts["lead"])
            finally:
                if worktree_parent is not None:
                    shutil.rmtree(worktree_parent, ignore_errors=True)


class ArtifactDemotionTests(unittest.TestCase):
    def _runner(self, root: Path) -> OrganizationRunner:
        run_dir = root / "devsession" / "agent-organizations" / "sig"
        return OrganizationRunner(
            root, "Fingerprint progress.", "claude",
            "flat", "sig", run_dir,
        )

    def test_closeout_signature_ignores_paper_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root)
            baseline = runner._closeout_progress_signature()
            runner.candidate_kinds["r1"] = {
                "candidate": "r1", "kind": "artifact-only",
                "paths": ["report.md"],
            }
            runner.candidate_kinds["e1"] = {
                "candidate": "e1", "kind": "identity-only", "paths": [],
            }
            self.assertEqual(
                runner._closeout_progress_signature(), baseline,
                "artifact-only and identity-only commits must not buy "
                "closeout rounds",
            )
            runner.candidate_kinds["i1"] = {
                "candidate": "i1", "kind": "implementation",
                "paths": ["app.py"],
            }
            self.assertNotEqual(
                runner._closeout_progress_signature(), baseline,
            )

    def test_candidate_counts_separate_work_from_paper(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            runner = self._runner(root)
            runner.candidate_kinds = {
                "i1": {"kind": "implementation"},
                "r1": {"kind": "artifact-only"},
                "r2": {"kind": "artifact-only"},
                "e1": {"kind": "identity-only"},
                "u1": {"kind": "unknown"},
            }
            self.assertEqual(
                runner._candidate_counts(),
                {
                    "implementation": 1,
                    "artifact_only": 2,
                    "identity_only": 1,
                },
            )


if __name__ == "__main__":
    unittest.main()
