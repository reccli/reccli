#!/usr/bin/env python3
"""
Focused regression tests for core persistence and retrieval paths.
"""

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reccli.devsession import DevSession
from reccli.retrieval import ContextRetriever


class DevSessionRegressionTests(unittest.TestCase):
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


class PackagingRegressionTests(unittest.TestCase):
    def test_ui_backend_server_exists_at_expected_path(self):
        server_path = Path(__file__).resolve().parent.parent / "backend" / "server.py"
        self.assertTrue(server_path.exists(), server_path)


if __name__ == "__main__":
    unittest.main()
