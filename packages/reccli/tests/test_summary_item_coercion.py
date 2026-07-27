"""Regression tests for bare-string summary items.

A plain string persisted in a summary category (written by an external editor
or a misbehaving producer) used to crash every reader: load_project_context,
the background embed scripts, and the summarizer merge path. Strings must be
coerced to canonical dicts at save time, and read paths must tolerate any
non-dict items in files written before the coercion existed.
"""

import json
import tempfile
import unittest
from pathlib import Path

from reccli.session.devsession import DevSession
from reccli.summarization.summary_schema import (
    SUMMARY_CATEGORIES,
    coerce_summary_items,
    validate_summary_schema,
)


def _summary_with_string_item():
    return {
        "schema_version": "1.1",
        "model": "test",
        "created_at": "2026-06-09T00:00:00Z",
        "overview": "Test session.",
        "decisions": [],
        "code_changes": [],
        "problems_solved": [
            {
                "id": "prb_000", "problem": "a real item", "solution": "",
                "span_ids": [], "references": [],
                "message_range": {"start": "msg_001", "end": "msg_002",
                                  "start_index": 0, "end_index": 2},
                "confidence": "high", "pinned": False, "locked": False,
            },
            "bare string appended by an external editor",
        ],
        "open_issues": [],
        "next_steps": [],
        "causal_edges": [],
        "audit_trail": [],
    }


class TestCoerceSummaryItems(unittest.TestCase):
    def test_string_item_becomes_canonical_dict(self):
        summary = _summary_with_string_item()
        warnings = coerce_summary_items(summary, conversation_len=4)

        self.assertEqual(len(warnings), 1)
        coerced = summary["problems_solved"][1]
        self.assertIsInstance(coerced, dict)
        self.assertTrue(coerced["id"].startswith("prb_"))
        self.assertEqual(coerced["problem"], "bare string appended by an external editor")
        self.assertEqual(coerced["confidence"], "low")
        # Spec safe-fallback: degraded full-conversation range, never a narrow one
        self.assertEqual(coerced["message_range"]["start_index"], 0)
        self.assertEqual(coerced["message_range"]["end_index"], 4)
        self.assertTrue(coerced["message_range"]["degraded"])

    def test_dict_items_untouched(self):
        summary = _summary_with_string_item()
        original = json.loads(json.dumps(summary["problems_solved"][0]))
        coerce_summary_items(summary, conversation_len=4)
        self.assertEqual(summary["problems_solved"][0], original)

    def test_no_conversation_len_yields_null_range(self):
        summary = _summary_with_string_item()
        coerce_summary_items(summary)
        self.assertIsNone(summary["problems_solved"][1]["message_range"])

    def test_validate_reports_non_dict_items(self):
        errors = validate_summary_schema(_summary_with_string_item())
        self.assertTrue(any("must be an object" in e for e in errors))


class TestSaveCoercesStringItems(unittest.TestCase):
    def test_skip_validation_save_still_coerces(self):
        session = DevSession(session_id="test-coerce")
        session.conversation = [
            {"role": "user", "content": "hi", "timestamp": "2026-06-09T00:00:00Z"},
            {"role": "assistant", "content": "hello", "timestamp": "2026-06-09T00:00:01Z"},
        ]
        session.summary = _summary_with_string_item()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "coerce.devsession"
            session.save(path, skip_validation=True)

            loaded = DevSession.load(path, verify_checksums=True)
            for category in SUMMARY_CATEGORIES:
                for item in loaded.summary.get(category, []):
                    self.assertIsInstance(item, dict)


if __name__ == "__main__":
    unittest.main()
