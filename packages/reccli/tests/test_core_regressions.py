#!/usr/bin/env python3
"""
Focused regression tests for core persistence and retrieval paths.
"""

import sys
import tempfile
import unittest
import shutil
from unittest import mock
from pathlib import Path
import json
from copy import deepcopy


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reccli.session.devsession import DevSession
from reccli.project.devproject import DevProjectManager
from reccli.summarization.preemptive_compaction import PreemptiveCompactor
from reccli.retrieval.retrieval import ContextRetriever
from reccli.retrieval.search import filter_deleted_results
from reccli.summarization.summarizer import SessionSummarizer
from reccli.summarization.summary_verification import SummaryVerifier
from reccli.summarization.summary_schema import (
    create_summary_skeleton,
    create_decision_item,
    create_open_issue_item,
    create_code_change_item,
)


class _FakeChatCompletions:
    def __init__(self, payload):
        self.payload = deepcopy(payload)

    def create(self, **kwargs):
        if isinstance(self.payload, list):
            if not self.payload:
                raise AssertionError("Fake LLM client exhausted payload sequence")
            payload = self.payload.pop(0)
        else:
            payload = self.payload
        content = json.dumps(payload)
        message = type("Message", (), {"content": content})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


class _FakeChatClient:
    def __init__(self, payload):
        self.completions = _FakeChatCompletions(payload)


class _FakeLLMClient:
    def __init__(self, payload):
        self.chat = _FakeChatClient(payload)


def _load_fixture_suite(filename: str):
    fixture_path = Path(__file__).resolve().parent / "fixtures" / filename
    with fixture_path.open(encoding="utf-8") as f:
        return json.load(f)


def _materialize_repo_fixture(project_root: Path, files: dict):
    (project_root / ".git").mkdir(exist_ok=True)
    for rel_path, content in files.items():
        path = project_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _materialize_repo_fixture_dir(project_root: Path, fixture_root: Path):
    shutil.copytree(fixture_root, project_root)
    (project_root / ".git").mkdir(exist_ok=True)


class DevSessionRegressionTests(unittest.TestCase):
    def test_new_session_save_defaults_to_project_devsession_directory(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td) / "reccli"
            (project_root / ".git").mkdir(parents=True)

            session = DevSession("save_path_test")
            session.metadata["project_root"] = str(project_root)
            session.conversation = [{"role": "user", "content": "hello"}]
            session.save()

            self.assertIsNotNone(session.path)
            self.assertEqual(session.path.parent, (project_root / "devsession").resolve())
            self.assertEqual(session.path.suffix, ".devsession")
            self.assertRegex(session.path.name, r"^\d{8}_\d{4}\.devsession$")

    def test_loaded_session_can_save_without_explicit_path(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "session.devsession"

            session = DevSession("save_path_test")
            session.conversation = [{"role": "user", "content": "hello"}]
            session.save(path)

            loaded = DevSession.load(path)
            loaded.conversation.append({"role": "assistant", "content": "world"})
            loaded.save()

            reloaded = DevSession.load(path)
            self.assertEqual(len(reloaded.conversation), 2)
            self.assertEqual(reloaded.path, path)

    def test_devproject_manager_uses_project_named_devproject_file(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td) / "RecCli"
            project_root.mkdir()
            manager = DevProjectManager(project_root)

            document = manager.load_or_create()
            saved_path = manager.save(document)

            self.assertEqual(saved_path, (project_root / "RecCli.devproject").resolve())
            self.assertTrue(saved_path.exists())

    def test_checkpoints_round_trip_through_devsession(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "checkpoints.devsession"

            session = DevSession("checkpoint_test")
            session.checkpoints = [
                {
                    "id": "CP_01",
                    "t": "2026-03-13T00:00:00",
                    "label": "baseline",
                    "criteria": None,
                    "message_index": 0,
                    "token_count": 1,
                    "summary_snapshot": {},
                }
            ]
            session.save(path)

            loaded = DevSession.load(path)
            self.assertEqual(len(loaded.checkpoints), 1)
            self.assertEqual(loaded.checkpoints[0]["id"], "CP_01")

    def test_save_synthesizes_spans_and_summary_span_links(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "span_links.devsession"

            session = DevSession("span_link_test")
            session.conversation = [
                {"role": "user", "content": "We should use spans", "_message_id": "msg_001"},
                {"role": "assistant", "content": "Agreed, they stabilize linking", "_message_id": "msg_002"},
                {"role": "user", "content": "Let's do it", "_message_id": "msg_003"},
            ]
            session.summary = {
                "schema_version": "1.1",
                "model": "test-model",
                "model_version": "test",
                "created_at": "2026-03-14T00:00:00",
                "session_hash": "hash",
                "overview": "Decided to add spans",
                "decisions": [
                    {
                        "id": "dec_manual",
                        "decision": "Add spans",
                        "reasoning": "Stabilizes temporal linking",
                        "impact": "high",
                        "references": ["msg_001", "msg_002"],
                        "message_range": {
                            "start": "msg_001",
                            "end": "msg_003",
                            "start_index": 0,
                            "end_index": 3,
                        },
                        "confidence": "high",
                        "quote": "Agreed, they stabilize linking",
                    }
                ],
                "code_changes": [],
                "problems_solved": [],
                "open_issues": [],
                "next_steps": [],
                "causal_edges": [],
                "audit_trail": [],
            }

            session.save(path)
            loaded = DevSession.load(path)

            self.assertEqual(len(loaded.spans), 1)
            self.assertEqual(len(loaded.summary["decisions"][0]["span_ids"]), 1)
            self.assertTrue(loaded.spans[0]["id"].startswith("spn_"))

    def test_tombstone_preserves_indices_and_updates_summary_frontier(self):
        session = DevSession("tombstone_test")
        session.conversation = [
            {"role": "user", "content": "alpha", "_message_id": "msg_001"},
            {"role": "assistant", "content": "beta", "_message_id": "msg_002"},
            {"role": "user", "content": "gamma", "_message_id": "msg_003"},
        ]
        session.spans = [
            {
                "id": "spn_001",
                "kind": "decision_discussion",
                "start_message_id": "msg_001",
                "end_message_id": "msg_002",
                "start_index": 0,
                "end_index": 2,
            }
        ]

        self.assertTrue(session.tombstone_message("msg_002", reason="test_redaction"))
        self.assertTrue(session.conversation[1]["deleted"])
        self.assertEqual(session.conversation[1]["content"], "[TOMBSTONED]")
        self.assertEqual(session.summary_sync["last_synced_msg_id"], "msg_002")
        self.assertEqual(session.summary_sync["pending_messages"], 1)

    def test_external_embeddings_round_trip_via_sidecar(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "embeddings.devsession"

            session = DevSession("embedding_sidecar_test")
            session.conversation = [
                {
                    "role": "user",
                    "content": "hello",
                    "_message_id": "msg_001",
                    "embedding": [0.1, 0.2, 0.3],
                },
                {
                    "role": "assistant",
                    "content": "world",
                    "_message_id": "msg_002",
                    "embedding": [0.4, 0.5, 0.6],
                },
            ]
            session.save(path, skip_validation=True)

            sidecar = session.externalize_message_embeddings()
            self.assertIsNotNone(sidecar)
            self.assertEqual(session.embedding_storage["mode"], "external")
            self.assertNotIn("embedding", session.conversation[0])
            self.assertEqual(session.conversation[0]["embedding_ref"], 0)

            session.save(skip_validation=True)
            loaded = DevSession.load(path)

            self.assertEqual(loaded.embedding_storage["mode"], "external")
            self.assertFalse(loaded.embedding_storage["loaded"])
            hydrated = loaded.load_external_message_embeddings()
            self.assertEqual(hydrated, 2)
            self.assertEqual(len(loaded.conversation[1]["embedding"]), 3)
            self.assertAlmostEqual(loaded.conversation[1]["embedding"][0], 0.4, places=6)
            self.assertAlmostEqual(loaded.conversation[1]["embedding"][1], 0.5, places=6)
            self.assertAlmostEqual(loaded.conversation[1]["embedding"][2], 0.6, places=6)

    def test_open_tail_span_does_not_advance_summary_frontier(self):
        session = DevSession("open_tail_test")
        session.conversation = [
            {"role": "user", "content": "one", "_message_id": "msg_001"},
            {"role": "assistant", "content": "two", "_message_id": "msg_002"},
            {"role": "user", "content": "three", "_message_id": "msg_003"},
            {"role": "assistant", "content": "four", "_message_id": "msg_004"},
        ]
        session.spans = [
            {
                "id": "spn_closed",
                "kind": "decision_discussion",
                "status": "closed",
                "start_message_id": "msg_001",
                "end_message_id": "msg_002",
                "start_index": 0,
                "end_index": 2,
            }
        ]

        open_span = session.replace_open_tail_span(2)
        verifier = SummaryVerifier(session.conversation, session.spans)
        valid, error = verifier.verify_span(open_span)

        self.assertTrue(valid, error)
        self.assertEqual(open_span["status"], "open")
        self.assertEqual(session.summary_sync["last_synced_msg_id"], "msg_002")
        self.assertEqual(session.summary_sync["pending_messages"], 2)

    def test_incremental_summary_preserves_existing_items_and_merges_delta(self):
        summarizer = SessionSummarizer(llm_client=None)
        conversation = [
            {"role": "user", "content": "Please create src/a.py", "_message_id": "msg_001"},
            {"role": "tool", "content": "Created file: src/a.py", "_message_id": "msg_002"},
            {"role": "assistant", "content": "Created file: src/a.py", "_message_id": "msg_003"},
            {"role": "user", "content": "Now create src/b.py", "_message_id": "msg_004"},
            {"role": "tool", "content": "Created file: src/b.py", "_message_id": "msg_005"},
            {"role": "assistant", "content": "Created file: src/b.py", "_message_id": "msg_006"},
        ]

        summary = summarizer.summarize_session(conversation[:3], redact_secrets=False)
        self.assertEqual(len(summary.get("code_changes", [])), 1)

        updated = summarizer.update_summary_incrementally(
            conversation=conversation,
            existing_summary=summary,
            start_index=3,
            end_index=6,
            redact_secrets=False,
        )

        self.assertEqual(len(updated.get("code_changes", [])), 2)
        files = {item["files"][0] for item in updated["code_changes"]}
        self.assertEqual(files, {"src/a.py", "src/b.py"})

    def test_delta_operations_do_not_silently_drop_existing_items(self):
        summarizer = SessionSummarizer(llm_client=None)
        summary = create_summary_skeleton(model="test-model", session_hash="hash")
        existing_decision = create_decision_item(
            decision="Use A",
            reasoning="Initial choice",
            impact="medium",
            references=["msg_001"],
            message_range={"start": "msg_001", "end": "msg_002", "start_index": 0, "end_index": 2},
        )
        existing_issue = create_open_issue_item(
            issue="Need retry handling",
            severity="medium",
            references=["msg_003"],
            message_range={"start": "msg_003", "end": "msg_004", "start_index": 2, "end_index": 4},
        )
        summary["decisions"] = [existing_decision]
        summary["open_issues"] = [existing_issue]

        operations = [
            {
                "op": "update_item",
                "category": "decisions",
                "item_id": existing_decision["id"],
                "changes": {"reasoning": "Initial choice, later reaffirmed"},
            },
            {
                "op": "merge_items",
                "category": "open_issues",
                "source_ids": [existing_issue["id"]],
                "target_item": {
                    "issue": "Retry handling and dead-letter flow",
                    "severity": "high",
                    "references": ["msg_003", "msg_005"],
                    "message_range": {"start": "msg_003", "end": "msg_005", "start_index": 2, "end_index": 5},
                },
            },
        ]

        updated_summary, _ = summarizer._apply_delta_operations(
            summary,
            [],
            operations,
            conversation=[
                {"role": "user", "content": "a", "_message_id": "msg_001"},
                {"role": "assistant", "content": "b", "_message_id": "msg_002"},
                {"role": "assistant", "content": "c", "_message_id": "msg_003"},
                {"role": "assistant", "content": "d", "_message_id": "msg_004"},
                {"role": "assistant", "content": "e", "_message_id": "msg_005"},
            ],
        )

        summarizer._assert_no_silent_item_loss(summary, updated_summary, operations)
        issue_ids = {item["id"] for item in updated_summary["open_issues"]}
        self.assertIn(existing_issue["id"], issue_ids)

    def test_update_summary_state_incrementally_applies_constrained_patch_ops(self):
        existing_summary = create_summary_skeleton(model="test-model", session_hash="hash")
        existing_decision = create_decision_item(
            decision="Use WAL recording",
            reasoning="Native append-only recording is more reliable",
            impact="high",
            references=["msg_001", "msg_002"],
            message_range={"start": "msg_001", "end": "msg_002", "start_index": 0, "end_index": 2},
            span_ids=["spn_decision"],
        )
        existing_issue = create_open_issue_item(
            issue="Need retry handling",
            severity="medium",
            references=["msg_003", "msg_004"],
            message_range={"start": "msg_003", "end": "msg_004", "start_index": 2, "end_index": 4},
            span_ids=["spn_open_issue"],
        )
        existing_summary["decisions"] = [existing_decision]
        existing_summary["open_issues"] = [existing_issue]

        existing_spans = [
            {
                "id": "spn_decision",
                "kind": "decision_discussion",
                "status": "closed",
                "start_message_id": "msg_001",
                "end_message_id": "msg_002",
                "start_index": 0,
                "end_index": 2,
                "message_ids": ["msg_001", "msg_002"],
            },
            {
                "id": "spn_open_issue",
                "kind": "open_issue_discussion",
                "status": "open",
                "start_message_id": "msg_003",
                "start_index": 2,
                "latest_message_id": "msg_004",
                "latest_index": 3,
                "message_ids": ["msg_003", "msg_004"],
            },
        ]
        conversation = [
            {"role": "user", "content": "Use WAL instead of asciinema", "_message_id": "msg_001"},
            {"role": "assistant", "content": "Agreed, native WAL is more stable", "_message_id": "msg_002"},
            {"role": "user", "content": "We still need retries", "_message_id": "msg_003"},
            {"role": "assistant", "content": "Yes, add retry handling", "_message_id": "msg_004"},
            {"role": "assistant", "content": "Next step is implementing retries", "_message_id": "msg_005"},
        ]

        llm_payload = {
            "operations": [
                {
                    "op": "update_item",
                    "category": "decisions",
                    "item_id": existing_decision["id"],
                    "changes": {"reasoning": "Native append-only recording is more reliable and easier to compact"},
                },
                {
                    "op": "close_span",
                    "span_id": "spn_open_issue",
                    "end_message_id": "msg_005",
                    "message_ids": ["msg_003", "msg_004", "msg_005"],
                },
                {
                    "op": "add_item",
                    "category": "next_steps",
                    "item": {
                        "action": "Implement retry handling",
                        "priority": 1,
                        "references": ["msg_004", "msg_005"],
                        "message_range": {
                            "start": "msg_004",
                            "end": "msg_005",
                        },
                    },
                },
            ]
        }
        summarizer = SessionSummarizer(llm_client=_FakeLLMClient(llm_payload))

        updated_state = summarizer.update_summary_state_incrementally(
            conversation=conversation,
            existing_summary=existing_summary,
            existing_spans=existing_spans,
            start_index=4,
            end_index=5,
            redact_secrets=False,
        )

        updated_summary = updated_state["summary"]
        updated_spans = updated_state["spans"]
        updated_decision = next(item for item in updated_summary["decisions"] if item["id"] == existing_decision["id"])
        closed_issue_span = next(span for span in updated_spans if span["id"] == "spn_open_issue")

        self.assertEqual(updated_decision["reasoning"], "Native append-only recording is more reliable and easier to compact")
        self.assertEqual(len(updated_summary["next_steps"]), 1)
        self.assertEqual(updated_summary["next_steps"][0]["action"], "Implement retry handling")
        self.assertEqual(len(updated_summary["next_steps"][0]["span_ids"]), 1)
        self.assertTrue(updated_summary["next_steps"][0]["span_ids"][0].startswith("spn_"))
        self.assertEqual(closed_issue_span["status"], "closed")
        self.assertEqual(closed_issue_span["message_ids"], ["msg_003", "msg_004", "msg_005"])
        self.assertEqual([op["op"] for op in updated_state["operations"]], ["update_item", "close_span", "add_item"])

    def test_invalid_delta_operations_are_rejected_before_state_mutation(self):
        existing_summary = create_summary_skeleton(model="test-model", session_hash="hash")
        existing_decision = create_decision_item(
            decision="Keep summaries incremental",
            reasoning="Full regeneration drops state",
            impact="high",
            references=["msg_001", "msg_002"],
            message_range={"start": "msg_001", "end": "msg_002", "start_index": 0, "end_index": 2},
            span_ids=["spn_existing"],
        )
        existing_summary["decisions"] = [existing_decision]
        existing_summary["next_steps"] = [
            {
                "id": "nxt_existing",
                "action": "Implement retry handling",
                "priority": 1,
                "references": ["msg_003"],
                "message_range": {"start": "msg_003", "end": "msg_003", "start_index": 2, "end_index": 3},
                "span_ids": ["spn_next"],
            }
        ]

        existing_spans = [
            {
                "id": "spn_existing",
                "kind": "decision_discussion",
                "status": "closed",
                "start_message_id": "msg_001",
                "end_message_id": "msg_002",
                "start_index": 0,
                "end_index": 2,
                "message_ids": ["msg_001", "msg_002"],
            },
            {
                "id": "spn_open",
                "kind": "next_step_planning",
                "status": "open",
                "start_message_id": "msg_003",
                "start_index": 2,
                "latest_message_id": "msg_004",
                "latest_index": 3,
                "message_ids": ["msg_003", "msg_004"],
            },
        ]
        conversation = [
            {"role": "user", "content": "Keep summaries incremental", "_message_id": "msg_001"},
            {"role": "assistant", "content": "Full regeneration drops state", "_message_id": "msg_002"},
            {"role": "user", "content": "Implement retry handling", "_message_id": "msg_003"},
            {"role": "assistant", "content": "Yes, retries next", "_message_id": "msg_004"},
            {"role": "assistant", "content": "No real delta here", "_message_id": "msg_005"},
        ]
        llm_payload = {
            "operations": [
                {
                    "op": "update_item",
                    "category": "decisions",
                    "item_id": "dec_missing",
                    "changes": {"reasoning": "Bad target"},
                },
                {
                    "op": "close_span",
                    "span_id": "spn_existing",
                    "end_message_id": "msg_005",
                },
                {
                    "op": "add_item",
                    "category": "next_steps",
                    "item": {
                        "action": "Implement retry handling",
                        "priority": 1,
                        "references": ["msg_003", "msg_004"],
                        "message_range": {
                            "start": "msg_003",
                            "end": "msg_004",
                        },
                    },
                },
            ]
        }
        summarizer = SessionSummarizer(llm_client=_FakeLLMClient(llm_payload))

        updated_state = summarizer.update_summary_state_incrementally(
            conversation=conversation,
            existing_summary=existing_summary,
            existing_spans=existing_spans,
            start_index=4,
            end_index=5,
            redact_secrets=False,
        )

        self.assertEqual(updated_state["operations"], [{"op": "no_change", "reason": "validation_rejected"}])
        self.assertEqual(updated_state["summary"]["decisions"][0]["reasoning"], "Full regeneration drops state")
        self.assertEqual(len(updated_state["summary"]["next_steps"]), 1)
        self.assertEqual(updated_state["summary"]["next_steps"][0]["action"], "Implement retry handling")
        open_span = next(span for span in updated_state["spans"] if span["id"] == "spn_open")
        self.assertEqual(open_span["status"], "open")

    def test_devproject_proposal_is_generated_and_applied_from_session_summary(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            session_path = project_root / "sessions" / "session_001.devsession"
            session_path.parent.mkdir()

            session = DevSession("session_001")
            session.metadata["project_root"] = str(project_root)
            session.summary = create_summary_skeleton(model="test-model", session_hash="hash")
            session.summary["overview"] = "Implemented authentication flow"
            session.summary["code_changes"] = [
                create_code_change_item(
                    files=["src/auth.py", "src/session.py"],
                    description="Implement authentication flow",
                    change_type="feature",
                    references=["msg_001", "msg_002"],
                    message_range={"start": "msg_001", "end": "msg_002", "start_index": 0, "end_index": 2},
                )
            ]

            manager = DevProjectManager(project_root)
            document, proposal = manager.generate_proposal_for_session(session, session_path)

            self.assertIsNotNone(proposal)
            self.assertEqual(len(document["proposals"]), 1)
            op_types = {op["op"] for op in proposal["diff"]}
            self.assertIn("add_feature", op_types)
            self.assertIn("link_session", op_types)

            updated, accepted = manager.apply_proposal(proposal["proposal_id"])
            self.assertEqual(accepted["status"], "accepted")
            self.assertEqual(len(updated["features"]), 1)
            self.assertEqual(updated["features"][0]["session_ids"], ["session_001"])
            self.assertIn("src/auth.py", updated["features"][0]["files_touched"])
            self.assertEqual(len(updated["session_index"]), 1)
            self.assertEqual(updated["session_index"][0]["session_id"], "session_001")
            self.assertEqual(updated["proposals"], [])

    def test_devproject_updates_existing_feature_by_file_overlap(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            manager = DevProjectManager(project_root)
            document = manager.load_or_create()
            document["features"] = [
                {
                    "feature_id": "feat_src_auth_py",
                    "feature_version": 2,
                    "title": "Authentication Flow",
                    "description": "Existing auth feature",
                    "status": "in-progress",
                    "source": "manual",
                    "files_touched": ["src/auth.py"],
                    "file_boundaries": ["src/**"],
                    "session_ids": ["session_000"],
                    "last_updated_session": "session_000",
                    "updated_at": "2026-03-14T00:00:00Z",
                }
            ]
            manager.save(document)

            session = DevSession("session_002")
            session.metadata["project_root"] = str(project_root)
            session.summary = create_summary_skeleton(model="test-model", session_hash="hash")
            session.summary["code_changes"] = [
                create_code_change_item(
                    files=["src/auth.py", "src/token.py"],
                    description="Expand authentication flow",
                    change_type="feature",
                    references=["msg_010", "msg_011"],
                    message_range={"start": "msg_010", "end": "msg_011", "start_index": 9, "end_index": 11},
                )
            ]

            updated_doc, proposal = manager.generate_proposal_for_session(
                session,
                project_root / "sessions" / "session_002.devsession",
            )
            self.assertIsNotNone(proposal)
            self.assertEqual(len(updated_doc["proposals"]), 1)
            op_types = {op["op"] for op in proposal["diff"]}
            self.assertIn("update_feature", op_types)
            self.assertNotIn("add_feature", op_types)

            applied, _ = manager.apply_proposal(proposal["proposal_id"])
            self.assertEqual(len(applied["features"]), 1)
            feature = applied["features"][0]
            self.assertGreaterEqual(feature["feature_version"], 2)
            self.assertIn("src/token.py", feature["files_touched"])
            self.assertIn("session_002", feature["session_ids"])

    def test_devproject_init_scans_codebase_into_features(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "README.md").write_text(
                "# Demo Project\n\nA demo project for codebase scanning.\n",
                encoding="utf-8",
            )
            (project_root / "src").mkdir()
            (project_root / "src" / "api.py").write_text("def handler():\n    return True\n", encoding="utf-8")
            (project_root / "src" / "utils.py").write_text("def util():\n    return 1\n", encoding="utf-8")
            (project_root / "apps").mkdir()
            (project_root / "apps" / "web").mkdir()
            (project_root / "apps" / "web" / "main.ts").write_text("export const app = true;\n", encoding="utf-8")

            manager = DevProjectManager(project_root)
            document = manager.initialize_from_codebase(use_llm=False)

            self.assertEqual(document["project"]["description"], "A demo project for codebase scanning.")
            self.assertGreaterEqual(len(document["features"]), 2)
            feature_ids = {feature["feature_id"] for feature in document["features"]}
            self.assertIn("feat_src_api_py", feature_ids)
            self.assertIn("feat_apps_web", feature_ids)

    def test_devproject_sync_proposes_new_codebase_feature(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "src").mkdir()
            (project_root / "src" / "api.py").write_text("def handler():\n    return True\n", encoding="utf-8")

            manager = DevProjectManager(project_root)
            manager.initialize_from_codebase(use_llm=False)

            (project_root / "backend").mkdir()
            (project_root / "backend" / "server.py").write_text("def serve():\n    return 200\n", encoding="utf-8")

            document, proposal = manager.generate_sync_proposal_from_codebase()
            self.assertIsNotNone(proposal)
            self.assertEqual(len(document["proposals"]), 1)
            op_types = {op["op"] for op in proposal["diff"]}
            self.assertIn("add_feature", op_types)

    def test_devproject_init_force_preserves_manual_feature_boundaries(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "README.md").write_text(
                "# Demo Project\n\nA demo project with manual feature boundaries.\n",
                encoding="utf-8",
            )
            (project_root / "packages" / "core").mkdir(parents=True)
            (project_root / "packages" / "core" / "devsession.py").write_text("from .summary import x\n", encoding="utf-8")
            (project_root / "packages" / "core" / "summary.py").write_text("x = 1\n", encoding="utf-8")
            (project_root / "apps" / "web").mkdir(parents=True)
            (project_root / "apps" / "web" / "index.tsx").write_text("export const page = true;\n", encoding="utf-8")

            manager = DevProjectManager(project_root)
            document = manager.load_or_create()
            document["features"] = [
                {
                    "feature_id": "feat_devsession_runtime",
                    "feature_version": 3,
                    "title": "DevSession Runtime",
                    "description": "Manual feature boundary for session runtime work.",
                    "status": "in-progress",
                    "source": "manual",
                    "files_touched": ["packages/core/devsession.py", "packages/core/summary.py"],
                    "file_boundaries": ["packages/core/**"],
                    "docs": [],
                    "session_ids": [],
                    "last_updated_session": None,
                    "updated_at": "2026-03-15T00:00:00Z",
                    "staleness": {
                        "status": "unknown",
                        "checked_at": "2026-03-15T00:00:00Z",
                        "signals": [],
                    },
                }
            ]
            manager.save(document)

            rebuilt = manager.initialize_from_codebase(force=True, use_llm=False)
            feature_ids = {feature["feature_id"] for feature in rebuilt["features"]}

            self.assertIn("feat_devsession_runtime", feature_ids)
            preserved = next(feature for feature in rebuilt["features"] if feature["feature_id"] == "feat_devsession_runtime")
            self.assertEqual(preserved["source"], "manual")
            self.assertEqual(
                preserved["files_touched"],
                ["packages/core/devsession.py", "packages/core/summary.py"],
            )
            auto_features = [feature for feature in rebuilt["features"] if feature["source"] == "auto"]
            self.assertTrue(all("packages/core/devsession.py" not in feature["files_touched"] for feature in auto_features))

    def test_devproject_init_always_runs_sync(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "src").mkdir()
            (project_root / "src" / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")

            # Cold start: sync should run
            manager = DevProjectManager(project_root)
            with mock.patch.object(
                manager,
                "generate_sync_proposal_from_codebase",
                return_value=({}, {"proposal_id": "projupd_test"}),
            ) as mocked_sync, mock.patch.object(
                manager,
                "apply_proposal",
                return_value=({"features": []}, {"proposal_id": "projupd_test"}),
            ) as mocked_apply:
                manager.initialize_from_codebase(use_llm=False)

            mocked_sync.assert_called_once()
            mocked_apply.assert_called_once_with("projupd_test")

            # Force reinit: sync should also run
            with mock.patch.object(
                manager,
                "generate_sync_proposal_from_codebase",
                return_value=({}, {"proposal_id": "projupd_test2"}),
            ) as mocked_sync, mock.patch.object(
                manager,
                "apply_proposal",
                return_value=({"features": []}, {"proposal_id": "projupd_test2"}),
            ) as mocked_apply:
                manager.initialize_from_codebase(force=True, use_llm=False)

            mocked_sync.assert_called_once()
            mocked_apply.assert_called_once_with("projupd_test2")

    def test_devproject_init_llm_clustering_groups_cross_directory_files(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "README.md").write_text(
                "# Demo Project\n\nA demo auth project.\n",
                encoding="utf-8",
            )
            (project_root / "api").mkdir()
            (project_root / "middleware").mkdir()
            (project_root / "models").mkdir()
            (project_root / "config").mkdir()
            (project_root / "api" / "auth.py").write_text("from middleware.session import Session\n", encoding="utf-8")
            (project_root / "middleware" / "session.py").write_text("from models.user import User\n", encoding="utf-8")
            (project_root / "models" / "user.py").write_text("class User:\n    pass\n", encoding="utf-8")
            (project_root / "config" / "oauth.py").write_text("PROVIDER = 'demo'\n", encoding="utf-8")

            llm_payload = {
                "project": {
                    "name": "Demo Project",
                    "description": "Authentication demo project.",
                },
                "features": [
                    {
                        "title": "Authentication",
                        "description": "Authentication flow across API, session middleware, user model, and OAuth config.",
                        "files": [
                            "api/auth.py",
                            "middleware/session.py",
                            "models/user.py",
                            "config/oauth.py",
                        ],
                        "file_boundaries": [
                            "api/**",
                            "middleware/**",
                            "models/**",
                            "config/**",
                        ],
                        "status": "in-progress",
                    }
                ],
            }

            manager = DevProjectManager(project_root)
            document = manager.initialize_from_codebase(
                force=True,
                use_llm=True,
                llm_client=_FakeLLMClient(llm_payload),
                model="gpt5",
            )

            self.assertEqual(document["project"]["description"], "Authentication demo project.")
            self.assertEqual(len(document["features"]), 1)
            feature = document["features"][0]
            self.assertEqual(feature["title"], "Authentication")
            self.assertEqual(set(feature["files_touched"]), {
                "api/auth.py",
                "middleware/session.py",
                "models/user.py",
                "config/oauth.py",
            })

    def test_devproject_inventory_includes_import_clusters(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "api").mkdir()
            (project_root / "middleware").mkdir()
            (project_root / "models").mkdir()
            (project_root / "api" / "auth.py").write_text("from middleware.session import Session\n", encoding="utf-8")
            (project_root / "middleware" / "session.py").write_text("from models.user import User\n", encoding="utf-8")
            (project_root / "models" / "user.py").write_text("class User:\n    pass\n", encoding="utf-8")

            manager = DevProjectManager(project_root)
            inventory = manager._build_codebase_inventory()

            self.assertEqual(len(inventory["import_clusters"]), 1)
            cluster = inventory["import_clusters"][0]
            self.assertEqual(cluster["size"], 3)
            self.assertEqual(set(cluster["files"]), {
                "api/auth.py",
                "middleware/session.py",
                "models/user.py",
            })
            self.assertIn(cluster["granularity_hint"], {"coarse", "moderate", "fine"})
            self.assertIn("scores", cluster)

    def test_devproject_large_component_exposes_subclusters(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            for dirname in ("session", "retrieval", "compaction"):
                (project_root / dirname).mkdir()

            (project_root / "session" / "devsession.py").write_text(
                "from session.checkpoints import checkpoint\nfrom session.recorder import record\nfrom retrieval.search import search\n",
                encoding="utf-8",
            )
            (project_root / "session" / "checkpoints.py").write_text(
                "from session.recorder import record\n",
                encoding="utf-8",
            )
            (project_root / "session" / "recorder.py").write_text(
                "from session.checkpoints import checkpoint\n",
                encoding="utf-8",
            )

            (project_root / "retrieval" / "search.py").write_text(
                "from retrieval.embeddings import embed\nfrom retrieval.vector_index import index\nfrom compaction.summarizer import summarize\n",
                encoding="utf-8",
            )
            (project_root / "retrieval" / "embeddings.py").write_text(
                "from retrieval.vector_index import index\n",
                encoding="utf-8",
            )
            (project_root / "retrieval" / "vector_index.py").write_text(
                "from retrieval.embeddings import embed\n",
                encoding="utf-8",
            )

            (project_root / "compaction" / "summarizer.py").write_text(
                "from compaction.summary_schema import schema\nfrom compaction.summary_verification import verify\n",
                encoding="utf-8",
            )
            (project_root / "compaction" / "summary_schema.py").write_text(
                "from compaction.summary_verification import verify\n",
                encoding="utf-8",
            )
            (project_root / "compaction" / "summary_verification.py").write_text(
                "from compaction.summary_schema import schema\n",
                encoding="utf-8",
            )

            manager = DevProjectManager(project_root)
            inventory = manager._build_codebase_inventory()

            self.assertEqual(len(inventory["import_clusters"]), 1)
            cluster = inventory["import_clusters"][0]
            self.assertGreaterEqual(cluster["size"], 9)
            self.assertGreaterEqual(len(cluster["subclusters"]), 2)
            subcluster_files = [set(item["files"]) for item in cluster["subclusters"]]
            self.assertTrue(any("session/devsession.py" in files for files in subcluster_files))
            self.assertTrue(any("retrieval/search.py" in files for files in subcluster_files))
            self.assertTrue(any("compaction/summarizer.py" in files for files in subcluster_files))

    def test_devproject_common_feature_cluster_biases_coarse_granularity(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "api").mkdir()
            (project_root / "middleware").mkdir()
            (project_root / "models").mkdir()
            (project_root / "api" / "auth.py").write_text("from middleware.session import Session\n", encoding="utf-8")
            (project_root / "middleware" / "session.py").write_text("from models.user import User\n", encoding="utf-8")
            (project_root / "models" / "user.py").write_text("class User:\n    pass\n", encoding="utf-8")

            manager = DevProjectManager(project_root)
            inventory = manager._build_codebase_inventory()

            self.assertEqual(len(inventory["import_clusters"]), 1)
            cluster = inventory["import_clusters"][0]
            self.assertEqual(cluster["granularity_hint"], "coarse")
            self.assertGreaterEqual(cluster["scores"]["commonality_score"], 0.5)

    def test_devproject_novel_cluster_biases_fine_granularity(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "engine").mkdir()
            (project_root / "linking").mkdir()
            (project_root / "runtime").mkdir()
            (project_root / "engine" / "delta_compaction.py").write_text(
                "from linking.temporal_linking import temporal_link\n",
                encoding="utf-8",
            )
            (project_root / "linking" / "temporal_linking.py").write_text(
                "from runtime.summary_frontier import summary_frontier\n",
                encoding="utf-8",
            )
            (project_root / "runtime" / "summary_frontier.py").write_text(
                "summary_frontier = 'temporal_memory_span_frontier'\n",
                encoding="utf-8",
            )

            manager = DevProjectManager(project_root)
            inventory = manager._build_codebase_inventory()

            self.assertEqual(len(inventory["import_clusters"]), 1)
            cluster = inventory["import_clusters"][0]
            self.assertEqual(cluster["granularity_hint"], "fine")
            self.assertGreaterEqual(cluster["scores"]["novelty_score"], 0.5)

    def test_devproject_inventory_includes_documents_from_shared_scan(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "src").mkdir()
            (project_root / "src" / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
            (project_root / "docs").mkdir()
            (project_root / "docs" / "auth.md").write_text("# Auth\n\nUses src/auth.py for login handling.\n", encoding="utf-8")

            manager = DevProjectManager(project_root)
            inventory = manager._build_codebase_inventory()

            self.assertEqual(len(inventory["files"]), 1)
            self.assertEqual(len(inventory["documents"]), 1)
            doc = inventory["documents"][0]
            self.assertEqual(doc["kind"], "doc")
            self.assertEqual(doc["path"], "docs/auth.md")
            self.assertIn("src/auth.py", doc["referenced_paths"])

    def test_devproject_inventory_extracts_top_identifiers(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "src").mkdir()
            (project_root / "src" / "summarizer.py").write_text(
                "class SessionSummarizer:\n"
                "    pass\n\n"
                "def update_summary_state_incrementally():\n"
                "    return True\n\n"
                "DELTA_SUMMARY_PATCH_PROMPT = 'prompt'\n",
                encoding="utf-8",
            )
            (project_root / "src" / "index.ts").write_text(
                "export class ProjectDashboard {}\n"
                "export function buildFeatureMap() { return null; }\n"
                "const featurePlanner = true;\n",
                encoding="utf-8",
            )

            manager = DevProjectManager(project_root)
            inventory = manager._build_codebase_inventory()

            identifiers_by_path = {
                item["path"]: item["top_identifiers"]
                for item in inventory["files"]
            }
            symbols_by_path = {
                item["path"]: item["structural_symbols"]
                for item in inventory["files"]
            }
            self.assertIn("SessionSummarizer", identifiers_by_path["src/summarizer.py"])
            self.assertIn("update_summary_state_incrementally", identifiers_by_path["src/summarizer.py"])
            self.assertIn("DELTA_SUMMARY_PATCH_PROMPT", identifiers_by_path["src/summarizer.py"])
            self.assertIn("ProjectDashboard", identifiers_by_path["src/index.ts"])
            self.assertIn("buildFeatureMap", identifiers_by_path["src/index.ts"])
            self.assertIn("featurePlanner", identifiers_by_path["src/index.ts"])
            self.assertIn("class:SessionSummarizer", symbols_by_path["src/summarizer.py"])
            self.assertIn("function:update_summary_state_incrementally", symbols_by_path["src/summarizer.py"])
            self.assertIn("class:ProjectDashboard", symbols_by_path["src/index.ts"])

    def test_devproject_inventory_adds_role_and_evidence_hints(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            for dirname in ("api", "billing", "schemas", "shared", "config", "tests", "legacy"):
                (project_root / dirname).mkdir()

            (project_root / "cli.py").write_text(
                "from api.auth import login\n"
                "from billing.charge import charge\n"
                "from schemas.summary_schema import SummaryState\n"
                "from shared.tokens import token_budget\n",
                encoding="utf-8",
            )
            (project_root / "api" / "auth.py").write_text(
                "from shared.tokens import token_budget\n"
                "def login():\n    return token_budget()\n",
                encoding="utf-8",
            )
            (project_root / "billing" / "charge.py").write_text(
                "from shared.tokens import token_budget\n"
                "def charge():\n    return token_budget()\n",
                encoding="utf-8",
            )
            (project_root / "schemas" / "summary_schema.py").write_text(
                "class SummaryState:\n    pass\n",
                encoding="utf-8",
            )
            (project_root / "shared" / "tokens.py").write_text(
                "def token_budget():\n    return 1\n",
                encoding="utf-8",
            )
            (project_root / "config" / "settings.py").write_text(
                "DEFAULT_SETTINGS = {}\n",
                encoding="utf-8",
            )
            (project_root / "tests" / "test_auth.py").write_text(
                "from api.auth import login\n"
                "def test_login():\n    assert login() == 1\n",
                encoding="utf-8",
            )
            (project_root / "legacy" / "legacy_adapter.py").write_text(
                "def adapt_legacy_payload():\n    return True\n",
                encoding="utf-8",
            )

            manager = DevProjectManager(project_root)
            inventory = manager._build_codebase_inventory()
            files_by_path = {
                item["path"]: item
                for item in inventory["files"]
            }

            self.assertEqual(files_by_path["cli.py"]["role_hint"], "hub")
            self.assertGreaterEqual(files_by_path["cli.py"]["hub_score"], 0.7)
            self.assertTrue(files_by_path["cli.py"]["is_entrypoint_candidate"])

            self.assertEqual(files_by_path["shared/tokens.py"]["role_hint"], "shared_infra")
            self.assertGreaterEqual(files_by_path["shared/tokens.py"]["cross_domain_import_count"], 2)

            self.assertEqual(files_by_path["schemas/summary_schema.py"]["role_hint"], "schema")
            self.assertIn("summary", files_by_path["schemas/summary_schema.py"]["semantic_tags"])

            self.assertEqual(files_by_path["config/settings.py"]["role_hint"], "config")

            self.assertEqual(files_by_path["tests/test_auth.py"]["role_hint"], "test")
            self.assertGreaterEqual(files_by_path["tests/test_auth.py"]["support_score"], 0.85)

            self.assertEqual(files_by_path["legacy/legacy_adapter.py"]["role_hint"], "legacy")
            self.assertGreaterEqual(files_by_path["legacy/legacy_adapter.py"]["legacy_score"], 0.75)

    def test_devproject_inventory_extracts_signatures_and_semantic_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "api").mkdir()
            (project_root / "src").mkdir()

            (project_root / "api" / "routes.py").write_text(
                '"""Session route handlers for listing sessions."""\n'
                "__all__ = ['list_sessions']\n"
                "@app.get('/sessions')\n"
                "def list_sessions(limit, cursor=None):\n"
                "    return []\n",
                encoding="utf-8",
            )
            (project_root / "src" / "routes.ts").write_text(
                "/** Session API route handlers */\n"
                "export async function POST(request: Request) { return Response.json({ ok: true }); }\n"
                "export const sessionSchema = z.object({ id: z.string() });\n",
                encoding="utf-8",
            )

            manager = DevProjectManager(project_root)
            inventory = manager._build_codebase_inventory()
            files_by_path = {
                item["path"]: item
                for item in inventory["files"]
            }

            py_file = files_by_path["api/routes.py"]
            ts_file = files_by_path["src/routes.ts"]

            self.assertIn("def list_sessions(limit, cursor)", py_file["signatures"][0])
            self.assertIn("list_sessions", py_file["exported_symbols"])
            self.assertIn("app.get", py_file["decorators"])
            self.assertIn("GET /sessions", py_file["route_metadata"])
            self.assertIn("Session route handlers", py_file["docstring_excerpt"])

            self.assertTrue(any(sig.startswith("function POST(") for sig in ts_file["signatures"]))
            self.assertIn("POST", ts_file["exported_symbols"])
            self.assertIn("POST [next-route]", ts_file["route_metadata"])
            self.assertIn("Session API route handlers", ts_file["docstring_excerpt"])

    def test_devproject_inventory_derives_artifact_feature_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "packages" / "reccli-core" / "reccli").mkdir(parents=True)
            (project_root / "packages" / "reccli-core" / "backend").mkdir(parents=True)
            (project_root / "packages" / "reccli-core" / "ui" / "src" / "components").mkdir(parents=True)
            (project_root / "packages" / "reccli-core" / "tests").mkdir(parents=True)
            (project_root / "apps" / "web" / "pages" / "api").mkdir(parents=True)
            (project_root / "src" / "ui").mkdir(parents=True)

            (project_root / "packages" / "reccli-core" / "reccli" / "devsession.py").write_text("class DevSession:\n    pass\n", encoding="utf-8")
            (project_root / "packages" / "reccli-core" / "reccli" / "summarizer.py").write_text("class SessionSummarizer:\n    pass\n", encoding="utf-8")
            (project_root / "packages" / "reccli-core" / "reccli" / "search.py").write_text("def hybrid_search():\n    return []\n", encoding="utf-8")
            (project_root / "packages" / "reccli-core" / "reccli" / "devproject.py").write_text("class DevProjectManager:\n    pass\n", encoding="utf-8")
            (project_root / "packages" / "reccli-core" / "backend" / "server.py").write_text("def serve():\n    return True\n", encoding="utf-8")
            (project_root / "packages" / "reccli-core" / "ui" / "src" / "components" / "Chat.tsx").write_text("export function Chat() { return null }\n", encoding="utf-8")
            (project_root / "packages" / "reccli-core" / "tests" / "test_core.py").write_text("def test_core():\n    assert True\n", encoding="utf-8")
            (project_root / "apps" / "web" / "pages" / "api" / "webhook.ts").write_text("export default function handler() {}\n", encoding="utf-8")
            (project_root / "src" / "ui" / "dialogs.py").write_text("def open_dialog():\n    return True\n", encoding="utf-8")

            manager = DevProjectManager(project_root)
            inventory = manager._build_codebase_inventory()
            candidate_titles = [candidate["title"] for candidate in inventory["artifact_candidates"]]

            self.assertIn("Testing", candidate_titles)
            self.assertIn("API Routes", candidate_titles)

    def test_devproject_inventory_extracts_framework_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "apps" / "web" / "app" / "api" / "webhooks" / "stripe").mkdir(parents=True)
            (project_root / "middleware").mkdir()
            (project_root / "jobs").mkdir()
            (project_root / "models").mkdir()
            (project_root / "config").mkdir()

            (project_root / "apps" / "web" / "app" / "api" / "webhooks" / "stripe" / "route.ts").write_text(
                "export async function POST(request: Request) { return Response.json({ ok: true }); }\n",
                encoding="utf-8",
            )
            (project_root / "middleware" / "auth.ts").write_text(
                "export function middleware(request: Request) { return request; }\n",
                encoding="utf-8",
            )
            (project_root / "jobs" / "sync.py").write_text(
                "@app.task\n"
                "def sync_index():\n    return True\n",
                encoding="utf-8",
            )
            (project_root / "models" / "user.py").write_text(
                "class UserModel(BaseModel):\n    pass\n",
                encoding="utf-8",
            )
            (project_root / "config" / "schema.ts").write_text(
                "export const envSchema = z.object({ API_KEY: z.string() });\n",
                encoding="utf-8",
            )

            manager = DevProjectManager(project_root)
            inventory = manager._build_codebase_inventory()
            files_by_path = {
                item["path"]: item
                for item in inventory["files"]
            }

            webhook_artifacts = {(item["kind"], item["label"]) for item in files_by_path["apps/web/app/api/webhooks/stripe/route.ts"]["artifacts"]}
            middleware_artifacts = {(item["kind"], item["label"]) for item in files_by_path["middleware/auth.ts"]["artifacts"]}
            job_artifacts = {(item["kind"], item["label"]) for item in files_by_path["jobs/sync.py"]["artifacts"]}
            model_artifacts = {(item["kind"], item["label"]) for item in files_by_path["models/user.py"]["artifacts"]}
            config_artifacts = {(item["kind"], item["label"]) for item in files_by_path["config/schema.ts"]["artifacts"]}

            self.assertIn(("route", "webhooks/stripe"), webhook_artifacts)
            self.assertIn(("webhook", "webhooks/stripe"), webhook_artifacts)
            self.assertIn(("middleware", "auth"), middleware_artifacts)
            self.assertIn(("job", "sync"), job_artifacts)
            self.assertIn(("model", "UserModel"), model_artifacts)
            self.assertIn(("config_schema", "schema"), config_artifacts)

    def test_devproject_refines_broad_runtime_feature_with_artifact_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "packages" / "reccli-core" / "reccli").mkdir(parents=True)

            for rel in (
                "packages/reccli-core/reccli/devsession.py",
                "packages/reccli-core/reccli/checkpoints.py",
                "packages/reccli-core/reccli/search.py",
                "packages/reccli-core/reccli/vector_index.py",
                "packages/reccli-core/reccli/summarizer.py",
                "packages/reccli-core/reccli/summary_verification.py",
                "packages/reccli-core/reccli/devproject.py",
                "packages/reccli-core/reccli/proposal_queue.py",
            ):
                path = project_root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("def marker():\n    return True\n", encoding="utf-8")

            manager = DevProjectManager(project_root)
            inventory = manager._build_codebase_inventory()
            broad = manager._build_feature_record(
                feature_id="feat_runtime",
                title="Core Runtime",
                description="Broad runtime feature.",
                files=[item["path"] for item in inventory["files"]],
            )
            refined = manager._refine_features_with_artifact_candidates([broad], inventory)
            by_title = {item["title"]: set(item["files_touched"]) for item in refined}

            # With only universal domain rules, project-specific splitting
            # (search, summarization, etc.) is left to the LLM, not hardcoded rules.
            # The refinement pass should preserve the broad feature when no
            # universal artifact candidates match.
            self.assertIn("Core Runtime", by_title)

    def test_devproject_inventory_excludes_archive_and_examples_code_from_graph(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "src").mkdir()
            (project_root / "src" / "live.py").write_text("def live():\n    return True\n", encoding="utf-8")
            (project_root / "archive").mkdir()
            (project_root / "archive" / "old.py").write_text("def old():\n    return False\n", encoding="utf-8")
            (project_root / "examples").mkdir()
            (project_root / "examples" / "demo.py").write_text("print('demo')\n", encoding="utf-8")
            (project_root / "docs").mkdir()
            (project_root / "docs" / "archive-notes.md").write_text("# Notes\n\nHistorical notes.\n", encoding="utf-8")

            manager = DevProjectManager(project_root)
            inventory = manager._build_codebase_inventory()

            self.assertEqual({item["path"] for item in inventory["files"]}, {"src/live.py"})
            self.assertIn("docs/archive-notes.md", {item["path"] for item in inventory["documents"]})

    def test_devproject_sync_is_stable_after_llm_clustered_init(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "README.md").write_text(
                "# Demo Project\n\nA demo auth project.\n",
                encoding="utf-8",
            )
            (project_root / "api").mkdir()
            (project_root / "middleware").mkdir()
            (project_root / "models").mkdir()
            (project_root / "config").mkdir()
            (project_root / "api" / "auth.py").write_text("from middleware.session import Session\n", encoding="utf-8")
            (project_root / "middleware" / "session.py").write_text("from models.user import User\n", encoding="utf-8")
            (project_root / "models" / "user.py").write_text("class User:\n    pass\n", encoding="utf-8")
            (project_root / "config" / "oauth.py").write_text("PROVIDER = 'demo'\n", encoding="utf-8")

            llm_payload = {
                "project": {
                    "name": "Demo Project",
                    "description": "Authentication demo project.",
                },
                "features": [
                    {
                        "title": "Authentication",
                        "description": "Authentication flow across API, session middleware, user model, and OAuth config.",
                        "files": [
                            "api/auth.py",
                            "middleware/session.py",
                            "models/user.py",
                            "config/oauth.py",
                        ],
                        "file_boundaries": [
                            "api/**",
                            "middleware/**",
                            "models/**",
                            "config/**",
                        ],
                        "status": "in-progress",
                    }
                ],
            }

            manager = DevProjectManager(project_root)
            manager.initialize_from_codebase(
                force=True,
                use_llm=True,
                llm_client=_FakeLLMClient(llm_payload),
                model="gpt5",
            )

            document, proposal = manager.generate_sync_proposal_from_codebase()
            self.assertIsNone(proposal)
            self.assertEqual(document["proposals"], [])

    def test_devproject_init_accepts_subsystem_classification_payload(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "engine").mkdir()
            (project_root / "retrieval").mkdir()
            (project_root / "engine" / "summarizer.py").write_text(
                "class SessionSummarizer:\n    pass\n",
                encoding="utf-8",
            )
            (project_root / "engine" / "summary_schema.py").write_text(
                "class SummarySchema:\n    pass\n",
                encoding="utf-8",
            )
            (project_root / "retrieval" / "search.py").write_text(
                "def hybrid_search():\n    return []\n",
                encoding="utf-8",
            )
            (project_root / "orchestrator.py").write_text(
                "from engine.summarizer import SessionSummarizer\nfrom retrieval.search import hybrid_search\n",
                encoding="utf-8",
            )
            (project_root / "infra.py").write_text(
                "def load_settings():\n    return {}\n",
                encoding="utf-8",
            )

            llm_payload = {
                "project": {
                    "name": "Demo Project",
                    "description": "Feature-classified demo project.",
                },
                "features": [
                    {
                        "title": "Summarization",
                        "description": "Builds and validates compact summaries.",
                        "files": [
                            "engine/summarizer.py",
                            "engine/summary_schema.py",
                        ],
                        "suggested_file_boundaries": ["engine/**"],
                        "hub_files_excluded": ["orchestrator.py"],
                        "shared_infra_dependencies": ["infra.py"],
                        "rationale": "This is a stable summarization feature.",
                    },
                    {
                        "title": "Retrieval",
                        "description": "Handles semantic and lexical retrieval.",
                        "files": [
                            "retrieval/search.py",
                        ],
                        "suggested_file_boundaries": ["retrieval/**"],
                        "hub_files_excluded": ["orchestrator.py"],
                        "shared_infra_dependencies": ["infra.py"],
                        "rationale": "This is a stable retrieval feature.",
                    },
                ],
                "hub_files": ["orchestrator.py"],
                "shared_infrastructure": ["infra.py"],
                "unassigned": [],
                "notes": [],
            }

            manager = DevProjectManager(project_root)
            document = manager.initialize_from_codebase(
                force=True,
                use_llm=True,
                llm_client=_FakeLLMClient(llm_payload),
                model="gpt5",
            )

            self.assertEqual(
                [feature["title"] for feature in document["features"]],
                ["Summarization", "Retrieval"],
            )
            self.assertEqual(document["hub_files"], ["orchestrator.py"])
            self.assertEqual(document["shared_infrastructure"], ["infra.py"])
            self.assertCountEqual(
                document["features"][0]["files_touched"],
                ["engine/summary_schema.py", "engine/summarizer.py"],
            )
            self.assertEqual(document["features"][0]["file_boundaries"], ["engine/**"])
            self.assertEqual(document["features"][1]["file_boundaries"], ["retrieval/**"])

    def test_devproject_llm_init_accepts_project_context_override(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "engine").mkdir()
            (project_root / "engine" / "summarizer.py").write_text(
                "class SessionSummarizer:\n    pass\n",
                encoding="utf-8",
            )
            llm_payload = {
                "project": {
                    "name": "Demo Project",
                    "description": "Canonical feature map for the demo project.",
                },
                "features": [
                    {
                        "title": "Summarization",
                        "description": "Builds compact summaries.",
                        "files": [
                            "engine/summarizer.py",
                        ],
                        "suggested_file_boundaries": ["engine/**"],
                        "rationale": "Stable summarization work area.",
                    },
                ],
                "hub_files": [],
                "shared_infrastructure": [],
                "unassigned": [],
                "notes": [],
            }

            manager = DevProjectManager(project_root)
            document = manager.initialize_from_codebase(
                force=True,
                use_llm=True,
                llm_client=_FakeLLMClient(llm_payload),
                model="gpt5",
                project_context="Temporal memory engine for coding sessions.",
            )

            self.assertEqual(document["project"]["description"], "Canonical feature map for the demo project.")
            self.assertEqual([feature["title"] for feature in document["features"]], ["Summarization"])

    def test_devproject_llm_init_does_not_backfill_archive_examples_or_installers(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "README.md").write_text(
                "# Demo Project\n\nA demo auth project.\n",
                encoding="utf-8",
            )
            (project_root / "src").mkdir()
            (project_root / "src" / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
            (project_root / "archive").mkdir()
            (project_root / "archive" / "legacy.py").write_text("def old():\n    return False\n", encoding="utf-8")
            (project_root / "examples").mkdir()
            (project_root / "examples" / "demo.py").write_text("print('demo')\n", encoding="utf-8")
            (project_root / "install.sh").write_text("#!/bin/sh\necho install\n", encoding="utf-8")

            llm_payload = {
                "project": {
                    "name": "Demo Project",
                    "description": "Authentication demo project.",
                },
                "features": [
                    {
                        "title": "Authentication",
                        "description": "Authentication flow for the demo project.",
                        "files": [
                            "src/auth.py",
                            "archive/legacy.py",
                            "examples/demo.py",
                            "install.sh",
                        ],
                        "file_boundaries": ["src/**"],
                        "status": "in-progress",
                    }
                ],
            }

            manager = DevProjectManager(project_root)
            document = manager.initialize_from_codebase(
                force=True,
                use_llm=True,
                llm_client=_FakeLLMClient(llm_payload),
                model="gpt5",
            )

            titles = {feature["title"] for feature in document["features"]}
            self.assertEqual(titles, {"Authentication"})
            feature = document["features"][0]
            self.assertEqual(feature["files_touched"], ["src/auth.py"])

    def test_devproject_llm_init_attaches_support_files_to_nearest_feature(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "apps" / "web" / "pages" / "api").mkdir(parents=True)
            (project_root / "apps" / "web" / "pages" / "index.tsx").write_text("export default function Home() { return null }\n", encoding="utf-8")
            (project_root / "apps" / "web" / "pages" / "api" / "webhook.ts").write_text("export default function handler() {}\n", encoding="utf-8")
            (project_root / "apps" / "web" / "next.config.js").write_text("module.exports = {}\n", encoding="utf-8")
            (project_root / "apps" / "web" / "postcss.config.js").write_text("module.exports = {}\n", encoding="utf-8")

            llm_payload = {
                "project": {
                    "name": "Demo Project",
                    "description": "Web app demo project.",
                },
                "features": [
                    {
                        "title": "Web Landing",
                        "description": "Landing page and API endpoints for the web app.",
                        "files": [
                            "apps/web/pages/index.tsx",
                            "apps/web/pages/api/webhook.ts",
                        ],
                        "file_boundaries": ["apps/web/pages/**"],
                        "status": "in-progress",
                    }
                ],
            }

            manager = DevProjectManager(project_root)
            document = manager.initialize_from_codebase(
                force=True,
                use_llm=True,
                llm_client=_FakeLLMClient(llm_payload),
                model="gpt5",
            )

            self.assertEqual(len(document["features"]), 1)
            feature = document["features"][0]
            self.assertEqual(feature["title"], "Web Landing")
            self.assertIn("apps/web/next.config.js", feature["files_touched"])
            self.assertIn("apps/web/postcss.config.js", feature["files_touched"])

    def test_devproject_llm_init_attaches_legacy_devsession_files_to_core_feature(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "packages" / "reccli-core" / "reccli").mkdir(parents=True)
            (project_root / "src" / "devsession").mkdir(parents=True)
            (project_root / "packages" / "reccli-core" / "reccli" / "devsession.py").write_text("class DevSession:\n    pass\n", encoding="utf-8")
            (project_root / "packages" / "reccli-core" / "reccli" / "embeddings.py").write_text("def embed():\n    return []\n", encoding="utf-8")
            (project_root / "src" / "devsession" / "__init__.py").write_text("from .embeddings import *\n", encoding="utf-8")
            (project_root / "src" / "devsession" / "embeddings.py").write_text("def embed():\n    return []\n", encoding="utf-8")
            (project_root / "src" / "devsession" / "unified_index.py").write_text("from .embeddings import embed\n", encoding="utf-8")

            llm_payload = {
                "project": {
                    "name": "Demo Project",
                    "description": "Memory engine demo project.",
                },
                "features": [
                    {
                        "title": "Core Memory Engine",
                        "description": "The core devsession memory engine and embeddings pipeline.",
                        "files": [
                            "packages/reccli-core/reccli/devsession.py",
                            "packages/reccli-core/reccli/embeddings.py",
                        ],
                        "file_boundaries": ["packages/reccli-core/reccli/**"],
                        "status": "in-progress",
                    }
                ],
            }

            manager = DevProjectManager(project_root)
            document = manager.initialize_from_codebase(
                force=True,
                use_llm=True,
                llm_client=_FakeLLMClient(llm_payload),
                model="gpt5",
            )

            self.assertEqual(len(document["features"]), 1)
            feature = document["features"][0]
            self.assertEqual(feature["title"], "Core Memory Engine")
            self.assertIn("src/devsession/embeddings.py", feature["files_touched"])
            self.assertIn("src/devsession/unified_index.py", feature["files_touched"])

    def test_devproject_llm_init_links_documents_to_features_and_project_scope(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "src").mkdir()
            (project_root / "src" / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
            (project_root / "docs").mkdir()
            (project_root / "docs" / "auth-spec.md").write_text(
                "# Authentication Spec\n\nThis feature uses src/auth.py and describes the authentication flow.\n",
                encoding="utf-8",
            )
            (project_root / "docs" / "vision.md").write_text(
                "# Project Vision\n\nThis project aims to improve developer productivity overall.\n",
                encoding="utf-8",
            )

            llm_payload = {
                "project": {
                    "name": "Demo Project",
                    "description": "Authentication demo project.",
                },
                "features": [
                    {
                        "title": "Authentication",
                        "description": "Authentication flow for the demo project.",
                        "files": ["src/auth.py"],
                        "file_boundaries": ["src/**"],
                        "status": "in-progress",
                    }
                ],
            }

            manager = DevProjectManager(project_root)
            document = manager.initialize_from_codebase(
                force=True,
                use_llm=True,
                llm_client=_FakeLLMClient(llm_payload),
                model="gpt5",
            )

            feature = document["features"][0]
            linked_doc_paths = {item["path"] for item in feature["docs"]}
            project_doc_paths = {item["path"] for item in document["project_docs"]}

            self.assertIn("docs/auth-spec.md", linked_doc_paths)
            self.assertIn("docs/vision.md", project_doc_paths)

    def test_devproject_doc_ownership_links_format_specs_and_preserves_project_plan_scope(self):
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "packages" / "reccli-core" / "reccli").mkdir(parents=True)
            (project_root / "packages" / "reccli-core" / "backend").mkdir(parents=True)
            (project_root / "docs" / "specs").mkdir(parents=True)

            (project_root / "packages" / "reccli-core" / "reccli" / "devproject.py").write_text(
                "class DevProjectManager:\n    pass\n",
                encoding="utf-8",
            )
            (project_root / "packages" / "reccli-core" / "reccli" / "devsession.py").write_text(
                "class DevSession:\n    pass\n",
                encoding="utf-8",
            )
            (project_root / "packages" / "reccli-core" / "backend" / "server.py").write_text(
                "def serve():\n    return True\n",
                encoding="utf-8",
            )
            (project_root / "docs" / "specs" / "DEVPROJECT_FORMAT.md").write_text(
                "# DevProject Format\n\nThe .devproject format defines canonical feature maps, feature ownership, and project proposals.\n",
                encoding="utf-8",
            )
            (project_root / "docs" / "specs" / "DEVSESSION_FORMAT.md").write_text(
                "# DevSession Format\n\nThe devsession format tracks sessions, checkpoints, and temporal span links.\n",
                encoding="utf-8",
            )
            (project_root / "PROJECT_PLAN.md").write_text(
                "# Project Plan\n\nRepository-wide roadmap for releases, migration sequencing, and cross-feature priorities.\n",
                encoding="utf-8",
            )

            llm_payload = {
                "project": {
                    "name": "Demo Project",
                    "description": "RecCli-style runtime demo project.",
                },
                "features": [
                    {
                        "title": "DevProject Runtime",
                        "description": "Project dashboard, feature ownership, and proposal runtime.",
                        "files": ["packages/reccli-core/reccli/devproject.py"],
                        "file_boundaries": ["packages/reccli-core/reccli/devproject.py"],
                        "status": "in-progress",
                    },
                    {
                        "title": "DevSession Runtime",
                        "description": "Session recording, checkpoints, and span linking runtime.",
                        "files": ["packages/reccli-core/reccli/devsession.py"],
                        "file_boundaries": ["packages/reccli-core/reccli/devsession.py"],
                        "status": "in-progress",
                    },
                    {
                        "title": "Python Backend Server",
                        "description": "Backend API and service runtime.",
                        "files": ["packages/reccli-core/backend/server.py"],
                        "file_boundaries": ["packages/reccli-core/backend/**"],
                        "status": "in-progress",
                    },
                ],
            }

            manager = DevProjectManager(project_root)
            document = manager.initialize_from_codebase(
                force=True,
                use_llm=True,
                llm_client=_FakeLLMClient(llm_payload),
                model="gpt5",
            )

            docs_by_title = {
                feature["title"]: {item["path"] for item in feature["docs"]}
                for feature in document["features"]
            }
            project_doc_paths = {item["path"] for item in document["project_docs"]}

            self.assertIn("docs/specs/DEVPROJECT_FORMAT.md", docs_by_title["DevProject Runtime"])
            self.assertIn("docs/specs/DEVSESSION_FORMAT.md", docs_by_title["DevSession Runtime"])
            self.assertIn("PROJECT_PLAN.md", project_doc_paths)
            self.assertNotIn("PROJECT_PLAN.md", docs_by_title["Python Backend Server"])

    def test_next_step_aliases_are_canonicalized_during_delta_updates(self):
        existing_summary = create_summary_skeleton(model="test-model", session_hash="hash")
        existing_spans = []
        conversation = [
            {"role": "user", "content": "We should implement retry handling", "_message_id": "msg_001"},
        ]
        llm_payload = {
            "operations": [
                {
                    "op": "add_item",
                    "category": "next_steps",
                    "item": {
                        "id": "step_retry_impl",
                        "step": "Implement retry handling",
                        "priority": "medium",
                        "references": ["msg_001"],
                        "message_range": {"start": "msg_001", "end": "msg_001"},
                        "span_ids": [],
                    },
                }
            ]
        }
        summarizer = SessionSummarizer(llm_client=_FakeLLMClient(llm_payload))

        updated_state = summarizer.update_summary_state_incrementally(
            conversation=conversation,
            existing_summary=existing_summary,
            existing_spans=existing_spans,
            start_index=0,
            end_index=1,
            redact_secrets=False,
        )

        self.assertEqual(len(updated_state["summary"]["next_steps"]), 1)
        next_step = updated_state["summary"]["next_steps"][0]
        self.assertEqual(next_step["action"], "Implement retry handling")
        self.assertEqual(next_step["message_range"]["start_index"], 0)
        self.assertEqual(next_step["message_range"]["end_index"], 1)

    def test_update_item_can_infer_support_span_for_new_evidence(self):
        existing_summary = create_summary_skeleton(model="test-model", session_hash="hash")
        existing_decision = create_decision_item(
            decision="Use WAL recording",
            reasoning="Append-only capture is durable",
            impact="high",
            references=["msg_001", "msg_002"],
            message_range={"start": "msg_001", "end": "msg_002", "start_index": 0, "end_index": 2},
            span_ids=["spn_old_decision"],
        )
        existing_summary["decisions"] = [existing_decision]
        existing_spans = [
            {
                "id": "spn_old_decision",
                "kind": "decision_discussion",
                "status": "closed",
                "start_message_id": "msg_001",
                "end_message_id": "msg_002",
                "start_index": 0,
                "end_index": 2,
                "message_ids": ["msg_001", "msg_002"],
            }
        ]
        conversation = [
            {"role": "user", "content": "Use WAL recording.", "_message_id": "msg_001"},
            {"role": "assistant", "content": "Append-only capture is durable.", "_message_id": "msg_002"},
            {"role": "user", "content": "WAL also makes replay and redaction easier.", "_message_id": "msg_003"},
            {"role": "assistant", "content": "Update the decision rationale to include replay and redaction.", "_message_id": "msg_004"},
        ]
        llm_payload = {
            "operations": [
                {
                    "op": "update_item",
                    "category": "decisions",
                    "item_id": existing_decision["id"],
                    "changes": {
                        "reasoning": "Append-only capture is durable and makes replay and redaction easier",
                        "references": ["msg_001", "msg_002", "msg_003", "msg_004"],
                        "message_range": {"start": "msg_001", "end": "msg_004"},
                    },
                }
            ]
        }
        summarizer = SessionSummarizer(llm_client=_FakeLLMClient(llm_payload))

        updated_state = summarizer.update_summary_state_incrementally(
            conversation=conversation,
            existing_summary=existing_summary,
            existing_spans=existing_spans,
            start_index=2,
            end_index=4,
            redact_secrets=False,
        )

        updated_decision = updated_state["summary"]["decisions"][0]
        self.assertIn("spn_old_decision", updated_decision["span_ids"])
        self.assertGreaterEqual(len(updated_decision["span_ids"]), 2)
        self.assertEqual(updated_decision["message_range"]["end_index"], 4)
        self.assertEqual(updated_state["operations"][0]["op"], "update_item")

    def test_merge_items_unions_source_spans_and_new_support_span(self):
        existing_summary = create_summary_skeleton(model="test-model", session_hash="hash")
        issue_a = create_open_issue_item(
            issue="Retry handling missing",
            severity="medium",
            references=["msg_001"],
            message_range={"start": "msg_001", "end": "msg_001", "start_index": 0, "end_index": 1},
            span_ids=["spn_retry"],
        )
        issue_b = create_open_issue_item(
            issue="Dead-letter flow missing",
            severity="medium",
            references=["msg_002"],
            message_range={"start": "msg_002", "end": "msg_002", "start_index": 1, "end_index": 2},
            span_ids=["spn_deadletter"],
        )
        existing_summary["open_issues"] = [issue_a, issue_b]
        existing_spans = [
            {
                "id": "spn_retry",
                "kind": "open_issue_discussion",
                "status": "closed",
                "start_message_id": "msg_001",
                "end_message_id": "msg_001",
                "start_index": 0,
                "end_index": 1,
                "message_ids": ["msg_001"],
            },
            {
                "id": "spn_deadletter",
                "kind": "open_issue_discussion",
                "status": "closed",
                "start_message_id": "msg_002",
                "end_message_id": "msg_002",
                "start_index": 1,
                "end_index": 2,
                "message_ids": ["msg_002"],
            },
        ]
        conversation = [
            {"role": "user", "content": "Retry handling missing.", "_message_id": "msg_001"},
            {"role": "user", "content": "Dead-letter flow missing.", "_message_id": "msg_002"},
            {"role": "assistant", "content": "Merge those into one resilience backlog issue.", "_message_id": "msg_003"},
            {"role": "assistant", "content": "Track them together as one resilience workstream.", "_message_id": "msg_004"},
        ]
        llm_payload = {
            "operations": [
                {
                    "op": "merge_items",
                    "category": "open_issues",
                    "source_ids": [issue_a["id"], issue_b["id"]],
                    "target_item": {
                        "issue": "Retry handling and dead-letter flow are missing",
                        "severity": "medium",
                        "references": ["msg_001", "msg_002", "msg_003", "msg_004"],
                        "message_range": {"start": "msg_001", "end": "msg_004"},
                    },
                }
            ]
        }
        summarizer = SessionSummarizer(llm_client=_FakeLLMClient(llm_payload))

        updated_state = summarizer.update_summary_state_incrementally(
            conversation=conversation,
            existing_summary=existing_summary,
            existing_spans=existing_spans,
            start_index=2,
            end_index=4,
            redact_secrets=False,
        )

        self.assertEqual(updated_state["operations"][0]["op"], "merge_items")
        merged_item = next(item for item in updated_state["summary"]["open_issues"] if item["id"] not in {issue_a["id"], issue_b["id"]})
        self.assertIn("spn_retry", merged_item["span_ids"])
        self.assertIn("spn_deadletter", merged_item["span_ids"])
        self.assertGreaterEqual(len(merged_item["span_ids"]), 3)

    def test_post_op_deduplication_folds_sibling_decision_add_into_existing_item(self):
        existing_summary = create_summary_skeleton(model="test-model", session_hash="hash")
        existing_decision = create_decision_item(
            decision="Use WAL recording",
            reasoning="Append-only capture is more reliable",
            impact="high",
            references=["msg_001", "msg_002"],
            message_range={"start": "msg_001", "end": "msg_002", "start_index": 0, "end_index": 2},
            span_ids=["spn_decision"],
        )
        existing_summary["decisions"] = [existing_decision]
        conversation = [
            {"role": "user", "content": "Use WAL recording.", "_message_id": "msg_001"},
            {"role": "assistant", "content": "Append-only capture is more reliable.", "_message_id": "msg_002"},
            {"role": "user", "content": "WAL also makes redaction and replay easier.", "_message_id": "msg_003"},
            {"role": "assistant", "content": "That is further rationale for WAL.", "_message_id": "msg_004"},
        ]
        updated_summary = deepcopy(existing_summary)
        updated_summary["decisions"].append(
            create_decision_item(
                decision="WAL also makes redaction and replay easier",
                reasoning="Further rationale for WAL",
                impact="medium",
                references=["msg_003", "msg_004"],
                message_range={"start": "msg_003", "end": "msg_004", "start_index": 2, "end_index": 4},
                span_ids=["spn_new"],
            )
        )

        summarizer = SessionSummarizer(llm_client=None)
        deduped = summarizer._post_op_deduplicate_summary(
            before_summary=existing_summary,
            summary=updated_summary,
            conversation=conversation,
        )

        self.assertEqual(len(deduped["decisions"]), 1)
        self.assertEqual(deduped["decisions"][0]["id"], existing_decision["id"])
        self.assertIn("msg_003", deduped["decisions"][0]["references"])
        self.assertIn("spn_new", deduped["decisions"][0]["span_ids"])

    def test_post_op_deduplication_folds_reversal_add_into_existing_decision(self):
        existing_summary = create_summary_skeleton(model="test-model", session_hash="hash")
        existing_decision = create_decision_item(
            decision="Use a modal dialog for export",
            reasoning="Focuses the user on one task",
            impact="medium",
            references=["msg_001", "msg_002"],
            message_range={"start": "msg_001", "end": "msg_002", "start_index": 0, "end_index": 2},
            span_ids=["spn_export_modal"],
        )
        existing_summary["decisions"] = [existing_decision]
        conversation = [
            {"role": "user", "content": "Use a modal dialog for export.", "_message_id": "msg_001"},
            {"role": "assistant", "content": "A modal focuses the user.", "_message_id": "msg_002"},
            {"role": "user", "content": "Actually a sidebar is better for export controls.", "_message_id": "msg_003"},
            {"role": "assistant", "content": "Yes, a sidebar keeps controls visible.", "_message_id": "msg_004"},
        ]
        updated_summary = deepcopy(existing_summary)
        updated_summary["decisions"].append(
            create_decision_item(
                decision="Use a sidebar for export controls",
                reasoning="Keeps controls visible while editing",
                impact="medium",
                references=["msg_003", "msg_004"],
                message_range={"start": "msg_003", "end": "msg_004", "start_index": 2, "end_index": 4},
                span_ids=["spn_export_sidebar"],
            )
        )

        summarizer = SessionSummarizer(llm_client=None)
        deduped = summarizer._post_op_deduplicate_summary(
            before_summary=existing_summary,
            summary=updated_summary,
            conversation=conversation,
        )

        self.assertEqual(len(deduped["decisions"]), 1)
        self.assertEqual(deduped["decisions"][0]["id"], existing_decision["id"])
        self.assertEqual(deduped["decisions"][0]["decision"], "Use a sidebar for export controls")
        self.assertIn("spn_export_sidebar", deduped["decisions"][0]["span_ids"])

    def test_filter_deleted_results_removes_stale_index_hits(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            session = DevSession("search_filter_test")
            session.conversation = [
                {"role": "user", "content": "alpha", "_message_id": "msg_001"},
                {"role": "assistant", "content": "secret", "_message_id": "msg_002", "deleted": True},
                {"role": "assistant", "content": "[REDACTED]", "_message_id": "msg_003", "redacted": True},
            ]
            session.save(td_path / "search_filter_test.devsession", skip_validation=True)

            results = [
                {"id": "r1", "session": "search_filter_test", "message_id": "msg_001", "content_preview": "alpha"},
                {"id": "r2", "session": "search_filter_test", "message_id": "msg_002", "content_preview": "secret"},
                {"id": "r3", "session": "search_filter_test", "message_id": "msg_003", "content_preview": "stale"},
            ]
            filtered = filter_deleted_results(td_path, results)

            ids = [result["id"] for result in filtered]
            self.assertEqual(ids, ["r1", "r3"])
            redacted = next(result for result in filtered if result["id"] == "r3")
            self.assertEqual(redacted["content_preview"], "[REDACTED]")
            self.assertTrue(redacted["metadata"]["redacted"])


    def test_devproject_truncated_inventory_includes_documents(self):
        """Documents should appear in the LLM inventory payload so feature
        naming can use spec/format vocabulary.  Project-scope docs should be
        excluded to save tokens."""
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "src").mkdir()
            (project_root / "docs" / "specs").mkdir(parents=True)
            (project_root / "src" / "session.py").write_text(
                "class Session:\n    pass\n", encoding="utf-8",
            )
            (project_root / "docs" / "specs" / "SESSION_FORMAT.md").write_text(
                "# Session Format\n\nThe session format tracks temporal spans.\n",
                encoding="utf-8",
            )
            (project_root / "README.md").write_text(
                "# My Project\n\nOverview of the whole project.\n",
                encoding="utf-8",
            )

            manager = DevProjectManager(project_root)
            inventory = manager._build_codebase_inventory()

            truncated = manager._truncate_inventory_for_llm(inventory)
            self.assertIn("documents", truncated)

            doc_paths = {d["path"] for d in truncated["documents"]}
            # Feature-scope spec should be included
            self.assertIn("docs/specs/SESSION_FORMAT.md", doc_paths)
            # Project-scope README should be excluded
            self.assertNotIn("README.md", doc_paths)

            # Each doc entry carries the expected keys
            spec_doc = next(d for d in truncated["documents"] if d["path"] == "docs/specs/SESSION_FORMAT.md")
            self.assertEqual(spec_doc["doc_kind"], "format")
            self.assertIn("title", spec_doc)
            self.assertIn("excerpt", spec_doc)
            self.assertIn("referenced_paths", spec_doc)

    def test_devproject_truncated_inventory_prioritizes_spec_docs(self):
        """Format/spec docs should sort before generic docs in the truncated
        inventory so they survive any max-doc cap."""
        with tempfile.TemporaryDirectory() as td:
            project_root = Path(td)
            (project_root / ".git").mkdir()
            (project_root / "src").mkdir()
            (project_root / "docs").mkdir()
            (project_root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
            (project_root / "docs" / "GUIDE.md").write_text(
                "# Usage Guide\n\nHow to use the app.\n", encoding="utf-8",
            )
            (project_root / "docs" / "APP_FORMAT.md").write_text(
                "# App Format\n\nThe canonical app format.\n", encoding="utf-8",
            )

            manager = DevProjectManager(project_root)
            inventory = manager._build_codebase_inventory()
            truncated = manager._truncate_inventory_for_llm(inventory)

            doc_paths = [d["path"] for d in truncated["documents"]]
            # Format doc should appear before generic guide
            if "docs/APP_FORMAT.md" in doc_paths and "docs/GUIDE.md" in doc_paths:
                self.assertLess(
                    doc_paths.index("docs/APP_FORMAT.md"),
                    doc_paths.index("docs/GUIDE.md"),
                )


class RetrievalRegressionTests(unittest.TestCase):
    def setUp(self):
        self.session = DevSession("retrieval_test")
        self.session.conversation = [
            {"role": "user", "content": "start", "_message_id": "msg_001"},
            {"role": "assistant", "content": "middle", "_message_id": "msg_002"},
            {"role": "user", "content": "target", "_message_id": "msg_003"},
            {"role": "assistant", "content": "end", "_message_id": "msg_004"},
        ]
        self.session.spans = [
            {
                "id": "spn_test",
                "kind": "decision_discussion",
                "start_message_id": "msg_002",
                "end_message_id": "msg_004",
                "start_index": 1,
                "end_index": 4,
            }
        ]
        self.retriever = ContextRetriever(self.session)

    def test_retrieve_by_reference_uses_message_ids(self):
        messages = self.retriever.retrieve_by_reference("msg_003", context_window=1)
        self.assertEqual(len(messages), 3)
        self.assertTrue(any(msg.get("_is_target") for msg in messages))

    def test_retrieve_full_context_never_reports_negative_range_counts(self):
        summary_item = {
            "message_range": {
                "start": "msg_002",
                "end": "msg_004",
                "start_index": 200,
                "end_index": 400,
            }
        }

        full_context = self.retriever.retrieve_full_context(summary_item, expand_context=1)

        self.assertEqual(full_context["core_range"]["count"], 3)
        self.assertGreaterEqual(full_context["expanded_range"]["count"], 0)
        self.assertEqual(len(full_context["messages"]), 4)

    def test_retrieve_full_context_prefers_span_ids_when_present(self):
        summary_item = {
            "span_ids": ["spn_test"],
            "message_range": {
                "start": "msg_001",
                "end": "msg_001",
                "start_index": 0,
                "end_index": 1,
            },
        }

        full_context = self.retriever.retrieve_full_context(summary_item, expand_context=0)

        self.assertEqual(full_context["core_range"]["start"], 1)
        self.assertEqual(full_context["core_range"]["end"], 4)
        self.assertEqual(full_context["resolved_via"], "span_ids")
        self.assertEqual(full_context["resolved_span_ids"], ["spn_test"])

    def test_retrieve_full_context_from_open_span_uses_latest_index(self):
        self.session.spans = [
            {
                "id": "spn_open",
                "kind": "active_context",
                "status": "open",
                "start_message_id": "msg_002",
                "start_index": 1,
                "latest_message_id": "msg_004",
                "latest_index": 3,
            }
        ]
        self.retriever = ContextRetriever(self.session)

        full_context = self.retriever.retrieve_full_context({"span_ids": ["spn_open"]}, expand_context=0)

        self.assertEqual(full_context["core_range"]["start"], 1)
        self.assertEqual(full_context["core_range"]["end"], 4)
        self.assertEqual(len(full_context["messages"]), 3)

    def test_overlapping_spans_use_explicit_message_membership(self):
        self.session.conversation = [
            {"role": "user", "content": "a", "_message_id": "msg_001"},
            {"role": "assistant", "content": "b", "_message_id": "msg_002"},
            {"role": "user", "content": "c", "_message_id": "msg_003"},
            {"role": "assistant", "content": "d", "_message_id": "msg_004"},
            {"role": "user", "content": "e", "_message_id": "msg_005"},
        ]
        self.session.spans = [
            {
                "id": "spn_overlap_1",
                "kind": "decision_discussion",
                "status": "closed",
                "start_message_id": "msg_002",
                "end_message_id": "msg_005",
                "start_index": 1,
                "end_index": 5,
                "message_ids": ["msg_002", "msg_004"],
            },
            {
                "id": "spn_overlap_2",
                "kind": "problem_solving",
                "status": "closed",
                "start_message_id": "msg_002",
                "end_message_id": "msg_005",
                "start_index": 1,
                "end_index": 5,
                "message_ids": ["msg_003", "msg_005"],
            },
        ]
        self.retriever = ContextRetriever(self.session)

        full_context = self.retriever.retrieve_full_context({"span_ids": ["spn_overlap_1"]}, expand_context=0)
        core_ids = [msg["_message_id"] for msg in full_context["messages"] if msg.get("_in_core_range")]

        self.assertEqual(core_ids, ["msg_002", "msg_004"])
        self.assertEqual(full_context["core_range"]["count"], 2)

    def test_doc_linking_fixture_suite(self):
        fixtures = _load_fixture_suite("doc_linking_fixtures.json")

        for case in fixtures:
            with self.subTest(case=case["name"]):
                with tempfile.TemporaryDirectory() as td:
                    project_root = Path(td)
                    _materialize_repo_fixture(project_root, case["files"])

                    manager = DevProjectManager(project_root)
                    inventory = manager._build_codebase_inventory()
                    document = {
                        "features": [],
                        "project_docs": [],
                    }

                    for spec in case.get("features", []):
                        document["features"].append(
                            manager._build_feature_record(
                                feature_id=f"feat_{spec['title'].lower().replace(' ', '_')}",
                                title=spec["title"],
                                description=spec.get("description", spec["title"]),
                                files=spec.get("files", []),
                                source="manual",
                            )
                        )

                    manager._link_documents_to_document(document, inventory, use_embeddings=False)

                    actual_feature_docs = {
                        feature["title"]: sorted(item["path"] for item in feature.get("docs", []))
                        for feature in document["features"]
                    }
                    actual_project_docs = sorted(item["path"] for item in document.get("project_docs", []))

                    for title, expected_paths in case.get("expected_feature_docs", {}).items():
                        self.assertEqual(
                            sorted(expected_paths),
                            actual_feature_docs.get(title, []),
                            f"{case['name']} feature docs mismatch for {title}",
                        )

                    self.assertEqual(
                        sorted(case.get("expected_project_docs", [])),
                        actual_project_docs,
                        f"{case['name']} project docs mismatch",
                    )

    def test_tiny_feature_boundary_fixture_suite(self):
        fixtures = _load_fixture_suite("tiny_feature_boundary_fixtures.json")

        for case in fixtures:
            with self.subTest(case=case["name"]):
                with tempfile.TemporaryDirectory() as td:
                    project_root = Path(td)
                    _materialize_repo_fixture(project_root, case["files"])

                    manager = DevProjectManager(project_root)
                    inventory = manager._build_codebase_inventory()
                    broad = manager._build_feature_record(
                        feature_id="feat_runtime",
                        title=case["initial_feature"]["title"],
                        description=case["initial_feature"]["description"],
                        files=[item["path"] for item in inventory["files"]],
                    )
                    refined = manager._refine_features_with_artifact_candidates([broad], inventory)
                    refined_titles = [item["title"] for item in refined]
                    refined_by_title = {item["title"]: item for item in refined}

                    for expected_title in case.get("expected_titles", []):
                        self.assertIn(
                            expected_title,
                            refined_titles,
                            f"{case['name']} missing expected feature {expected_title}",
                        )

                    for unexpected_title in case.get("unexpected_titles", []):
                        self.assertNotIn(
                            unexpected_title,
                            refined_titles,
                            f"{case['name']} unexpectedly kept feature {unexpected_title}",
                        )

                    for title, expected_files in case.get("expected_feature_files", {}).items():
                        self.assertTrue(
                            set(expected_files).issubset(set(refined_by_title[title]["files_touched"])),
                            f"{case['name']} files mismatch for {title}",
                        )

    def test_init_feature_map_fixture_suite(self):
        fixtures = _load_fixture_suite("init_feature_map_fixtures.json")

        for case in fixtures:
            with self.subTest(case=case["name"]):
                with tempfile.TemporaryDirectory() as td:
                    project_root = Path(td)
                    _materialize_repo_fixture(project_root, case["files"])

                    manager = DevProjectManager(project_root)
                    document = manager.initialize_from_codebase(
                        force=True,
                        use_llm=True,
                        llm_client=_FakeLLMClient(case["llm_payload"]),
                        model="gpt5",
                    )
                    titles = [feature["title"] for feature in document["features"]]
                    by_title = {feature["title"]: feature for feature in document["features"]}

                    for expected_title in case.get("expected_titles", []):
                        self.assertIn(
                            expected_title,
                            titles,
                            f"{case['name']} missing expected title {expected_title}",
                        )

                    for unexpected_title in case.get("unexpected_titles", []):
                        self.assertNotIn(
                            unexpected_title,
                            titles,
                            f"{case['name']} unexpectedly included title {unexpected_title}",
                        )

                    if case.get("expected_any_titles"):
                        self.assertTrue(
                            any(title in titles for title in case["expected_any_titles"]),
                            f"{case['name']} missing any expected derived title",
                        )

                    for title, expected_paths in case.get("expected_feature_docs", {}).items():
                        self.assertEqual(
                            sorted(expected_paths),
                            sorted(item["path"] for item in by_title[title].get("docs", [])),
                            f"{case['name']} feature docs mismatch for {title}",
                        )

                    for title, expected_files in case.get("expected_feature_files", {}).items():
                        self.assertEqual(
                            sorted(expected_files),
                            sorted(by_title[title].get("files_touched", [])),
                            f"{case['name']} feature files mismatch for {title}",
                        )

    def test_init_feature_map_integration_repo_suite(self):
        cases = _load_fixture_suite("init_feature_map_integration_expected.json")
        payload_by_name = {
            case["name"]: case["llm_payload"]
            for case in _load_fixture_suite("init_feature_map_fixtures.json")
        }
        fixture_root = Path("/Users/will/coding-projects/devproject-init-fixtures")

        for case in cases:
            with self.subTest(case=case["name"]):
                with tempfile.TemporaryDirectory() as td:
                    project_root = Path(td) / case["repo_dir"]
                    _materialize_repo_fixture_dir(project_root, fixture_root / case["repo_dir"])

                    manager = DevProjectManager(project_root)
                    document = manager.initialize_from_codebase(
                        force=True,
                        use_llm=True,
                        llm_client=_FakeLLMClient(payload_by_name[case["name"]]),
                        model="gpt5",
                    )
                    titles = [feature["title"] for feature in document["features"]]
                    by_title = {feature["title"]: feature for feature in document["features"]}

                    self.assertEqual(
                        case["expected_titles"],
                        titles,
                        f"{case['name']} feature titles changed",
                    )

                    for title, expected_files in case.get("expected_feature_files", {}).items():
                        self.assertEqual(
                            sorted(expected_files),
                            sorted(by_title[title].get("files_touched", [])),
                            f"{case['name']} feature files mismatch for {title}",
                        )

                    for title, expected_paths in case.get("expected_feature_docs", {}).items():
                        self.assertEqual(
                            sorted(expected_paths),
                            sorted(item["path"] for item in by_title[title].get("docs", [])),
                            f"{case['name']} feature docs mismatch for {title}",
                        )

                    self.assertEqual(
                        sorted(case.get("expected_project_docs", [])),
                        sorted(item["path"] for item in document.get("project_docs", [])),
                        f"{case['name']} project docs mismatch",
                    )

                    synced_document, proposal = manager.generate_sync_proposal_from_codebase()
                    if case.get("sync_clean", False):
                        self.assertIsNone(proposal, f"{case['name']} sync unexpectedly proposed changes")
                        self.assertEqual(
                            [],
                            synced_document.get("proposals", []),
                            f"{case['name']} sync left residual proposals",
                        )


class CompactionRegressionTests(unittest.TestCase):
    def test_compactor_boundary_is_frontier_aware_and_preserves_open_tail(self):
        with tempfile.TemporaryDirectory() as td:
            session = DevSession("compaction_boundary_test")
            session.conversation = [
                {"role": "user", "content": f"msg {i}", "_message_id": f"msg_{i + 1:03d}"}
                for i in range(100)
            ]
            session.spans = [
                {
                    "id": "spn_closed",
                    "kind": "decision_discussion",
                    "status": "closed",
                    "start_message_id": "msg_001",
                    "end_message_id": "msg_010",
                    "start_index": 0,
                    "end_index": 10,
                }
            ]

            compactor = PreemptiveCompactor(session, Path(td), llm_client=None)
            boundary = compactor._determine_compaction_boundary(session.get_summary_frontier_index())

            # With frontier at 10 (closed span), pending=90 > OPEN_TAIL=40, so boundary = 100-40 = 60
            self.assertEqual(boundary, 60)

            session.spans = []
            boundary = compactor._determine_compaction_boundary(0)
            # No spans, frontier=0, pending=100 > OPEN_TAIL=40, boundary = 100-40 = 60
            self.assertEqual(boundary, 60)


class PackagingRegressionTests(unittest.TestCase):
    def test_ui_backend_server_exists_at_expected_path(self):
        server_path = Path(__file__).resolve().parent.parent / "backend" / "server.py"
        self.assertTrue(server_path.exists(), server_path)


if __name__ == "__main__":
    unittest.main()
