"""Tests for the integrity diagnostics.

Each test builds a project exhibiting one real failure mode observed in the wild
and asserts the corresponding check catches it. The negative cases matter as much
as the positive ones: a doctor that cries wolf gets ignored, and an ignored doctor
is no better than the silent failures it exists to replace.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from reccli.doctor import run_diagnostics, format_report, FAIL, WARN, OK
from reccli.session.devsession import DevSession


def _status(result, check_id):
    for check in result["checks"]:
        if check["id"] == check_id:
            return check["status"]
    raise AssertionError(f"check {check_id} not present in {[c['id'] for c in result['checks']]}")


def _findings(result, check_id):
    for check in result["checks"]:
        if check["id"] == check_id:
            return check["findings"]
    return []


class DoctorTests(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.sessions = self.root / "devsession"
        self.sessions.mkdir()
        (self.root / "t.devproject").write_text(json.dumps({
            "format": "devproject", "version": "2.1.0", "project_root": str(self.root),
            "updated_at": "2026-01-01T00:00:00Z", "last_updated_session": None,
            "project": {"name": "t", "description": "t", "status": "active", "source": "manual"},
            "features": [], "project_docs": [], "session_index": [], "proposals": [],
        }))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _session(self, stem, n_messages=6, summary=True, claude_session_id=None, save=True):
        s = DevSession(session_id=stem)
        s.metadata["project_root"] = str(self.root)
        if claude_session_id:
            s.metadata["claude_session_id"] = claude_session_id
        s.conversation = [
            {"id": f"msg_{i + 1:03d}", "index": i + 1, "role": "user",
             "content": f"message {i}", "timestamp": "2026-01-01T00:00:00"}
            for i in range(n_messages)
        ]
        if summary:
            s.summary = {"overview": f"summary of {stem}", "decisions": [], "code_changes": [],
                         "problems_solved": [], "open_issues": [], "next_steps": []}
        path = self.sessions / f"{stem}.devsession"
        if save:
            s.save(path, skip_validation=True)
        return path

    def _index(self, session_stems):
        (self.sessions / "index.json").write_text(json.dumps({
            "format": "devsession-index", "version": "1.1.0",
            "total_sessions": len(session_stems), "total_vectors": 0,
            "unified_vectors": [],
            "session_manifest": [{"session_id": stem} for stem in session_stems],
        }))

    # -- clean baseline ---------------------------------------------------

    def test_healthy_project_reports_no_problems(self):
        self._session("01012026_0000")
        self._index(["01012026_0000"])
        result = run_diagnostics(self.root)
        self.assertEqual(_status(result, "sessions.unreadable"), OK)
        self.assertEqual(_status(result, "index.coverage"), OK)
        self.assertEqual(_status(result, "links.out_of_bounds"), OK)
        self.assertEqual(result["counts"][FAIL], 0)

    # -- the silent-exclusion class ---------------------------------------

    def test_checksum_mismatch_is_reported_not_skipped(self):
        """A hand-edited file fails to load and vanishes from every rebuild."""
        path = self._session("01012026_0000")
        raw = json.loads(path.read_text())
        raw["conversation"].append({"id": "msg_099", "index": 99, "role": "user",
                                    "content": "edited in by hand", "timestamp": "x"})
        path.write_text(json.dumps(raw))  # written without refreshing checksums
        self._index([])

        result = run_diagnostics(self.root)
        self.assertEqual(_status(result, "sessions.unreadable"), FAIL)
        self.assertIn("checksum", _findings(result, "sessions.unreadable")[0]["detail"].lower())

    def test_unreadable_session_coverage_hint_says_rebuild_will_not_help(self):
        path = self._session("01012026_0000")
        raw = json.loads(path.read_text())
        raw["conversation"][0]["content"] = "tampered"
        path.write_text(json.dumps(raw))
        self._index([])

        result = run_diagnostics(self.root)
        hint = _findings(result, "index.coverage")[0]["hint"]
        self.assertIn("NOT", hint)

    def test_zero_byte_session_is_reported(self):
        (self.sessions / "01012026_0000.devsession").write_text("")
        self._index([])
        result = run_diagnostics(self.root)
        self.assertEqual(_status(result, "sessions.empty"), FAIL)

    def test_session_absent_from_index_is_reported(self):
        self._session("01012026_0000")
        self._index([])
        result = run_diagnostics(self.root)
        self.assertEqual(_status(result, "index.coverage"), FAIL)

    # -- link integrity ---------------------------------------------------

    def test_summary_link_past_end_of_conversation_is_reported(self):
        s = DevSession(session_id="01012026_0000")
        s.conversation = [{"id": "msg_001", "index": 1, "role": "user",
                           "content": "only message", "timestamp": "x"}]
        s.summary = {"overview": "s", "decisions": [
            {"id": "dec_001", "decision": "d", "reasoning": "",
             "message_range": {"start": "msg_001", "end": "msg_050",
                               "start_index": 0, "end_index": 50}}
        ], "code_changes": [], "problems_solved": [], "open_issues": [], "next_steps": []}
        s.save(self.sessions / "01012026_0000.devsession", skip_validation=True)
        self._index(["01012026_0000"])

        result = run_diagnostics(self.root)
        self.assertEqual(_status(result, "links.out_of_bounds"), FAIL)

    def test_null_message_range_does_not_crash_the_check(self):
        """message_range present but null is the exact shape that crashed the indexer."""
        s = DevSession(session_id="01012026_0000")
        s.conversation = [{"id": "msg_001", "index": 1, "role": "user", "content": "m", "timestamp": "x"}]
        s.summary = {"overview": "s", "decisions": [
            {"id": "dec_001", "decision": "d", "reasoning": "", "message_range": None}
        ], "code_changes": [], "problems_solved": [], "open_issues": [], "next_steps": []}
        s.save(self.sessions / "01012026_0000.devsession", skip_validation=True)
        self._index(["01012026_0000"])

        result = run_diagnostics(self.root)  # must not raise
        self.assertEqual(_status(result, "links.out_of_bounds"), OK)

    # -- checksum verification hole ---------------------------------------

    def test_checksum_stranded_on_emptied_structure_is_reported(self):
        """Emptying a structure leaves its checksum permanently unverified."""
        path = self._session("01012026_0000")
        raw = json.loads(path.read_text())
        raw["spans"] = [{"id": "spn_001", "kind": "note", "start_message_id": "msg_001",
                         "start_index": 0, "end_index": 1}]
        path.write_text(json.dumps(raw))
        s = DevSession.load(path)
        s.save(path, skip_validation=True)          # now carries a real spans checksum
        raw = json.loads(path.read_text())
        self.assertIn("spans", raw["checksums"])
        raw["spans"] = []                            # delete the data, leave the checksum
        path.write_text(json.dumps(raw))
        self._index(["01012026_0000"])

        result = run_diagnostics(self.root)
        self.assertEqual(_status(result, "checksums.orphaned"), WARN)
        self.assertIn("spans", _findings(result, "checksums.orphaned")[0]["detail"])

    # -- superseded snapshots ---------------------------------------------

    def test_superseded_partial_snapshot_is_reported(self):
        self._session("01012026_0000", n_messages=3, summary=False, claude_session_id="abc")
        self._session("01012026_0100", n_messages=40, summary=True, claude_session_id="abc")
        self._index(["01012026_0000", "01012026_0100"])

        result = run_diagnostics(self.root)
        self.assertEqual(_status(result, "sessions.superseded"), WARN)
        self.assertIn("01012026_0000", _findings(result, "sessions.superseded")[0]["target"])

    def test_distinct_sessions_sharing_a_claude_session_id_are_not_flagged(self):
        """One Claude session legitimately spans several devsessions."""
        self._session("01012026_0000", n_messages=40, summary=False, claude_session_id="abc")
        self._session("01012026_0100", n_messages=40, summary=True, claude_session_id="abc")
        self._index(["01012026_0000", "01012026_0100"])

        result = run_diagnostics(self.root)
        self.assertEqual(_status(result, "sessions.superseded"), OK)

    def test_shorter_session_with_different_content_is_not_flagged(self):
        """The false-positive shape: shorter and summarized-sibling, but NOT a prefix.

        A resumed session shares its claude_session_id with an earlier one without
        sharing any content. Length alone flagged three real sessions here and told
        the user to archive them, so the prefix confirmation is load-bearing.
        """
        short = self._session("01012026_0000", n_messages=5, summary=False, claude_session_id="abc")
        long_ = self._session("01012026_0100", n_messages=40, summary=True, claude_session_id="abc")
        for path, marker in ((short, "session one"), (long_, "session two")):
            raw = json.loads(path.read_text())
            for i, msg in enumerate(raw["conversation"]):
                msg["content"] = f"{marker} message {i}"
            path.write_text(json.dumps(raw))
        self._index(["01012026_0000", "01012026_0100"])

        result = run_diagnostics(self.root)
        self.assertEqual(_status(result, "sessions.superseded"), OK)

    def test_genuine_prefix_is_still_flagged(self):
        """The true positive must survive the stricter rule."""
        long_ = self._session("01012026_0100", n_messages=40, summary=True, claude_session_id="abc")
        short = self._session("01012026_0000", n_messages=5, summary=False, claude_session_id="abc")
        long_raw = json.loads(long_.read_text())
        short_raw = json.loads(short.read_text())
        short_raw["conversation"] = long_raw["conversation"][:5]   # a real truncated prefix
        short.write_text(json.dumps(short_raw))
        self._index(["01012026_0000", "01012026_0100"])

        result = run_diagnostics(self.root)
        self.assertEqual(_status(result, "sessions.superseded"), WARN)
        self.assertIn("01012026_0000", _findings(result, "sessions.superseded")[0]["target"])

    # -- feature map ------------------------------------------------------

    def test_colliding_feature_boundaries_are_reported(self):
        doc = json.loads((self.root / "t.devproject").read_text())
        doc["features"] = [
            {"feature_id": "feat_a", "title": "A", "file_boundaries": ["src/**"], "session_ids": ["s1"]},
            {"feature_id": "feat_b", "title": "B", "file_boundaries": ["src/**"], "session_ids": ["s1"]},
        ]
        (self.root / "t.devproject").write_text(json.dumps(doc))
        self._session("01012026_0000")
        self._index(["01012026_0000"])

        result = run_diagnostics(self.root)
        self.assertEqual(_status(result, "devproject.boundaries"), WARN)

    def test_subsuming_boundary_is_reported(self):
        doc = json.loads((self.root / "t.devproject").read_text())
        doc["features"] = [
            {"feature_id": "feat_all", "title": "All", "file_boundaries": ["src/**"], "session_ids": ["s1"]},
            {"feature_id": "feat_part", "title": "Part", "file_boundaries": ["src/charts/**"], "session_ids": ["s1"]},
        ]
        (self.root / "t.devproject").write_text(json.dumps(doc))
        self._session("01012026_0000")
        self._index(["01012026_0000"])

        result = run_diagnostics(self.root)
        findings = _findings(result, "devproject.boundaries")
        self.assertTrue(any("subsumes" in f["detail"] for f in findings))

    def test_dangling_session_index_entry_is_reported(self):
        doc = json.loads((self.root / "t.devproject").read_text())
        doc["session_index"] = [{"session_id": "gone", "path": "devsession/gone.devsession",
                                 "feature_ids": []}]
        (self.root / "t.devproject").write_text(json.dumps(doc))
        self._session("01012026_0000")
        self._index(["01012026_0000"])

        result = run_diagnostics(self.root)
        self.assertEqual(_status(result, "devproject.session_links"), FAIL)

    def test_feature_map_with_no_session_links_is_reported(self):
        doc = json.loads((self.root / "t.devproject").read_text())
        doc["features"] = [{"feature_id": "feat_a", "title": "A", "file_boundaries": ["src/**"],
                            "session_ids": []}]
        (self.root / "t.devproject").write_text(json.dumps(doc))
        self._session("01012026_0000")
        self._index(["01012026_0000"])

        result = run_diagnostics(self.root)
        self.assertEqual(_status(result, "devproject.unlinked"), WARN)

    # -- contract ---------------------------------------------------------

    def test_diagnostics_do_not_mutate_the_project(self):
        self._session("01012026_0000")
        self._index(["01012026_0000"])
        before = {p: p.stat().st_mtime_ns for p in sorted(self.root.rglob("*")) if p.is_file()}
        run_diagnostics(self.root)
        after = {p: p.stat().st_mtime_ns for p in sorted(self.root.rglob("*")) if p.is_file()}
        self.assertEqual(before, after, "doctor must be read-only")

    def test_missing_sessions_dir_does_not_raise(self):
        shutil.rmtree(self.sessions)
        result = run_diagnostics(self.root)   # must not raise
        self.assertEqual(result["sessions_scanned"], 0)

    def test_report_renders_and_hides_passing_checks_by_default(self):
        self._session("01012026_0000")
        self._index([])
        result = run_diagnostics(self.root)
        terse = format_report(result, verbose=False)
        full = format_report(result, verbose=True)
        self.assertIn("index.json" if "index.json" in terse else "absent from the index", terse)
        self.assertGreater(len(full), len(terse))


if __name__ == "__main__":
    unittest.main()
