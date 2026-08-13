"""Tests for the project-level organization outcome ledger."""

import json
import tempfile
import unittest
from pathlib import Path

from reccli.organization_outcomes import (
    outcome_ledger_path,
    record_outcome_event,
    summarize_outcomes,
)


def _terminal(root, run_id, status="round_limit", tokens=(100, 10)):
    record_outcome_event(
        root, "run_terminal", run_id,
        terminal_status=status,
        work_class="deployable_artifact",
        consumer="will",
        usage={"input_tokens": tokens[0], "output_tokens": tokens[1]},
        candidate_counts={"implementation": 1, "artifact_only": 0,
                          "identity_only": 0},
        completed_turns=5,
        promotion_readiness="not_ready",
    )


class OutcomeLedgerTests(unittest.TestCase):
    def test_no_ledger_summarizes_to_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(summarize_outcomes(Path(td)))

    def test_unknown_event_kind_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, "unknown outcome event"):
                record_outcome_event(Path(td), "vibes", "run-1")

    def test_waste_rate_counts_unused_terminal_runs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _terminal(root, "run-1", tokens=(1_000, 100))
            _terminal(root, "run-2", tokens=(2_000, 200))
            record_outcome_event(
                root, "promotion_applied", "run-1",
                candidate="a" * 40, decided_by="will",
            )
            summary = summarize_outcomes(root)
            self.assertEqual(summary["terminal_runs"], 2)
            self.assertEqual(summary["productive_runs"], 1)
            self.assertEqual(summary["waste_runs"], 1)
            self.assertEqual(summary["waste_rate"], 0.5)
            self.assertEqual(
                summary["total_tokens"],
                {"input_tokens": 3_000, "output_tokens": 300},
            )
            self.assertEqual(
                summary["waste_tokens"],
                {"input_tokens": 2_000, "output_tokens": 200},
            )

    def test_no_op_runs_are_not_waste(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _terminal(root, "run-1", status="completed_no_op")
            _terminal(root, "run-2")
            summary = summarize_outcomes(root)
            self.assertEqual(summary["no_op_runs"], 1)
            self.assertEqual(summary["waste_runs"], 1)
            self.assertEqual(summary["waste_rate"], 1.0)

    def test_candidate_used_by_successor_credits_the_parent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _terminal(root, "parent")
            record_outcome_event(
                root, "candidate_used", "parent",
                used_by="successor", decided_by="will",
            )
            summary = summarize_outcomes(root)
            self.assertEqual(summary["productive_runs"], 1)
            self.assertEqual(summary["waste_runs"], 0)

    def test_ledger_is_append_only_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _terminal(root, "run-1")
            _terminal(root, "run-2")
            lines = outcome_ledger_path(root).read_text(
                encoding="utf-8",
            ).splitlines()
            self.assertEqual(len(lines), 2)
            for line in lines:
                record = json.loads(line)
                self.assertEqual(
                    record["schema"], "reccli.organization-outcome.v1",
                )


if __name__ == "__main__":
    unittest.main()
