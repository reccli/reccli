"""Tests for session-signal capture, persistence, drift detection and continuation.

Session-signal was shipped as an autonomy governor and measured as something else.
Across 87,379 stored messages, zero carried the field the spec documents; the
`evaluate_continuation` tool the SESSION RULE mandated after every reasoning chain
was called zero times in a full session, because the Stop hook already did the work
without being asked; and the zoom-out that shares that hook fired on every turn once
tripped, starving the branch beside it.

What survived that measurement is narrower and more defensible: `goal` compared
across turns is the only mechanically-computed signal in the autonomy path, and the
only one not produced by the agent it constrains. These tests protect that, and the
persistence it depends on.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from reccli.hooks import session_recorder as SR


class SignalExtractionTests(unittest.TestCase):
    """The tag parser is the single point where every signal is created."""

    def test_parses_goal_resolved_and_open(self):
        sig = SR._extract_session_signal(
            "text <!--session-signal: goal=fix parser | resolved=a, b | open=c-->"
        )
        self.assertEqual(sig["goal"], "fix parser")
        self.assertEqual(sig["resolved"], ["a", "b"])
        self.assertEqual(sig["open"], ["c"])

    def test_goal_is_optional(self):
        sig = SR._extract_session_signal("<!--session-signal: resolved=a | open=b-->")
        self.assertNotIn("goal", sig)
        self.assertEqual(sig["open"], ["b"])

    def test_values_containing_pipes_are_not_truncated(self):
        sig = SR._extract_session_signal(
            "<!--session-signal: goal=run a | b pipeline | resolved=x | open=y-->"
        )
        self.assertEqual(sig["goal"], "run a | b pipeline")

    def test_trailing_tag_wins_over_an_example_in_the_body(self):
        sig = SR._extract_session_signal(
            "example: <!--session-signal: goal=old | resolved=o | open=o-->\n"
            "real: <!--session-signal: goal=new | resolved=n | open=n-->"
        )
        self.assertEqual(sig["goal"], "new")

    def test_no_tag_returns_none(self):
        self.assertIsNone(SR._extract_session_signal("no tag here"))

    # -- the cap -------------------------------------------------------

    def test_small_signals_are_untouched(self):
        """The cap must not perturb the common case."""
        sig = SR._extract_session_signal(
            "<!--session-signal: goal=fix parser | resolved=a, b | open=c-->"
        )
        self.assertNotIn("open_truncated", sig)
        self.assertNotIn("resolved_truncated", sig)

    def test_open_list_is_bounded(self):
        raw = ", ".join(f"item {i}" for i in range(12))
        sig = SR._extract_session_signal(
            f"<!--session-signal: goal=g | resolved=r | open={raw}-->"
        )
        self.assertEqual(len(sig["open"]), SR._SIGNAL_MAX_ITEMS)
        self.assertEqual(sig["open_truncated"], 12 - SR._SIGNAL_MAX_ITEMS)

    def test_truncation_is_never_silent(self):
        """Every dropped item is counted. Silent loss is the defect class this
        whole subsystem exists to avoid; the cap must not reintroduce it."""
        raw = ", ".join(f"r{i}" for i in range(9))
        sig = SR._extract_session_signal(
            f"<!--session-signal: goal=g | resolved={raw} | open=o-->"
        )
        self.assertEqual(sig["resolved_truncated"], 9 - SR._SIGNAL_MAX_ITEMS)

    def test_leading_items_are_the_ones_kept(self):
        """Consumers take the first actionable item, so order must be preserved."""
        raw = ", ".join(f"item {i}" for i in range(10))
        sig = SR._extract_session_signal(
            f"<!--session-signal: goal=g | resolved=r | open={raw}-->"
        )
        self.assertEqual(sig["open"][0], "item 0")

    def test_individual_items_and_goal_are_length_capped(self):
        long_item = "x" * 500
        sig = SR._extract_session_signal(
            f"<!--session-signal: goal={'g' * 500} | resolved=r | open={long_item}-->"
        )
        self.assertEqual(len(sig["goal"]), SR._SIGNAL_MAX_GOAL_CHARS)
        self.assertEqual(len(sig["open"][0]), SR._SIGNAL_MAX_ITEM_CHARS)

    def test_cap_leaves_the_goal_usable_for_drift_detection(self):
        """Drift compares goal[:80]; the cap must not cut below that."""
        self.assertGreaterEqual(SR._SIGNAL_MAX_GOAL_CHARS, 80)


class SignalPersistenceTests(unittest.TestCase):
    """The spec documents session_signal on assistant messages. It was never written."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.sessions = self.root / "devsession"
        self.sessions.mkdir()
        (self.root / "t.devproject").write_text(json.dumps({
            "format": "devproject", "version": "2.1.0", "project_root": str(self.root),
            "updated_at": "x", "last_updated_session": None,
            "project": {"name": "t", "description": "t", "status": "active", "source": "manual"},
            "features": [], "project_docs": [], "session_index": [], "proposals": [],
        }))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _wal(self, session_id, turns, goal="ship the parser"):
        wal = self.sessions / f".hooks_wal_{session_id}.jsonl"
        with open(wal, "w") as fh:
            fh.write(json.dumps({
                "format": "reccli-hooks-wal", "version": 1, "session_id": session_id,
                "started_at": "2026-01-01T00:00:00", "working_directory": str(self.root),
                "project_root": str(self.root),
            }) + "\n")
            for i in range(turns):
                fh.write(json.dumps({
                    "type": "assistant_response", "role": "assistant",
                    "content": f"turn {i}", "timestamp": "x",
                    "session_signal": {"goal": goal, "resolved": [f"s{i}"], "open": ["rest"]},
                }) + "\n")
        return wal

    def test_end_session_persists_the_signal(self):
        self._wal("S", 6)
        out = SR.end_session("S", str(self.root))
        conversation = json.loads(Path(out).read_text())["conversation"]
        carried = [m for m in conversation if m.get("session_signal")]
        self.assertEqual(len(carried), 6)
        self.assertEqual(carried[0]["session_signal"]["goal"], "ship the parser")

    def test_live_snapshot_persists_the_signal(self):
        """Mid-session flush writes a .live_ snapshot through its own code path.

        This is the copy that exists during a session, so it is what a compaction
        leaves behind if the session never ends cleanly.
        """
        self._wal("S", 5)
        SR.flush_active_wals(self.root)
        snapshots = list(self.sessions.glob(".live_*.devsession"))
        self.assertTrue(snapshots, "expected a live snapshot")
        conversation = json.loads(snapshots[0].read_text())["conversation"]
        self.assertTrue(any(m.get("session_signal") for m in conversation))

    def test_messages_without_a_signal_do_not_gain_an_empty_field(self):
        wal = self.sessions / ".hooks_wal_T.jsonl"
        with open(wal, "w") as fh:
            fh.write(json.dumps({
                "format": "reccli-hooks-wal", "version": 1, "session_id": "T",
                "started_at": "2026-01-01T00:00:00", "working_directory": str(self.root),
                "project_root": str(self.root),
            }) + "\n")
            for i in range(5):
                fh.write(json.dumps({"role": "user", "content": f"m{i}", "timestamp": "x"}) + "\n")
        out = SR.end_session("T", str(self.root))
        conversation = json.loads(Path(out).read_text())["conversation"]
        self.assertFalse(any("session_signal" in m for m in conversation))


class DriftAndContinuationTests(unittest.TestCase):
    """The zoom-out and the continuation hint share one function and one return."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.sessions = self.root / "devsession"
        self.sessions.mkdir()
        (self.root / "t.devproject").write_text(json.dumps({
            "format": "devproject", "version": "2.1.0", "project_root": str(self.root),
            "updated_at": "x", "last_updated_session": None,
            "project": {"name": "t", "description": "t", "status": "active", "source": "manual"},
            "features": [], "project_docs": [], "session_index": [], "proposals": [],
        }))
        self.wal = self.sessions / ".hooks_wal_S.jsonl"
        with open(self.wal, "w") as fh:
            fh.write(json.dumps({
                "format": "reccli-hooks-wal", "version": 1, "session_id": "S",
                "started_at": "2026-01-01T00:00:00", "working_directory": str(self.root),
                "project_root": str(self.root),
            }) + "\n")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _turn(self, goal, prompt="fix the parser bug", open_items=("parser bug in tokenizer",)):
        with open(self.wal, "a") as fh:
            fh.write(json.dumps({"type": "user_prompt", "role": "user",
                                 "content": prompt, "timestamp": "x"}) + "\n")
            fh.write(json.dumps({"type": "assistant_response", "role": "assistant",
                                 "content": "working", "timestamp": "x",
                                 "session_signal": {"goal": goal, "resolved": ["a"],
                                                    "open": list(open_items)}}) + "\n")
        SR._continuation_hint_path(self.wal).unlink(missing_ok=True)
        SR.compute_continuation_hint("S", str(self.root))
        path = SR._continuation_hint_path(self.wal)
        return json.loads(path.read_text())["kind"] if path.exists() else None

    def test_zoomout_fires_once_per_run_not_every_turn(self):
        """`streak >= threshold` stays true forever once tripped."""
        kinds = [self._turn("one steady goal") for _ in range(2 * SR._DRIFT_TURN_THRESHOLD)]
        fired = [i + 1 for i, k in enumerate(kinds) if k == "zoomout"]
        self.assertEqual(fired, [SR._DRIFT_TURN_THRESHOLD, 2 * SR._DRIFT_TURN_THRESHOLD])

    def test_continuation_is_not_starved_past_the_threshold(self):
        """The regression that mattered: the zoom-out returned before continuation."""
        kinds = [self._turn("fix the parser bug") for _ in range(2 * SR._DRIFT_TURN_THRESHOLD)]
        after = kinds[SR._DRIFT_TURN_THRESHOLD:]
        self.assertTrue(any(k == "continue" for k in after),
                        "continuation must still reach turns past the drift threshold")

    def test_changing_the_goal_resets_the_streak(self):
        """A goal change must clear the streak, or the zoom-out fires on unrelated work."""
        for _ in range(SR._DRIFT_TURN_THRESHOLD - 1):
            self._turn("goal one")
        # Goal must still match the open items, or the continuation filter (correctly)
        # declines and we would be asserting on the wrong mechanism.
        self._turn("fix the parser bug")
        self.assertEqual(SR._consecutive_same_goal_turns(self.wal), 1)

    def test_empty_goals_do_not_accumulate_a_streak(self):
        kinds = [self._turn("") for _ in range(SR._DRIFT_TURN_THRESHOLD + 2)]
        self.assertNotIn("zoomout", kinds)


class RemovedToolTests(unittest.TestCase):

    def test_evaluate_continuation_is_gone(self):
        """It asked the agent to judge its own progress against its own goal, and
        was called zero times in a full session where a SESSION RULE mandated it."""
        import reccli.mcp_server as mcp_server
        self.assertFalse(hasattr(mcp_server, "evaluate_continuation"))

    def test_the_hook_filter_survives(self):
        """The filtering logic is still live; only the on-request tool went away."""
        self.assertTrue(hasattr(SR, "_filter_open_items_by_goal"))
        result = SR._filter_open_items_by_goal("fix the parser", ["parser tokenizer bug"])
        self.assertEqual(result["action"], "continue")


if __name__ == "__main__":
    unittest.main()
