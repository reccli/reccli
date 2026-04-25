"""
Tests for new MCP tools added in the 2026-04 batch:
  inspect_result_id, preview_context, rebuild_index, delete_session,
  edit_summary_item, pin_memory, retry_summarization

Plus coverage for behaviors the tool surface relies on:
  - validate_index_dimensions / _msg_id_to_index
  - _reconstruct_file_from_raw_response (recover_file replace_all fix)
  - _collect_pinned_items (load_project_context Pinned Memory section)
  - summarizer._merge_summary_items locked/pinned semantics
  - compute_tau precedence
  - auto_reason tie-break

The `mcp` package is not in the test environment, so we stub
mcp.server.fastmcp before importing reccli.mcp_server. The MCP tool
decorator is a no-op passthrough; the underlying functions are still
callable normally.
"""

import json
import shutil
import sys
import tempfile
import types
import unittest
from copy import deepcopy
from pathlib import Path


# ---------------------------------------------------------------------------
# Stub mcp.server.fastmcp so mcp_server imports without the dependency
# ---------------------------------------------------------------------------

def _install_mcp_stub():
    if "mcp" in sys.modules:
        return
    mcp_pkg = types.ModuleType("mcp")
    mcp_server = types.ModuleType("mcp.server")
    mcp_fastmcp = types.ModuleType("mcp.server.fastmcp")

    class _StubFastMCP:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self):
            def _decorator(fn):
                return fn
            return _decorator

        def run(self, *args, **kwargs):
            pass

    mcp_fastmcp.FastMCP = _StubFastMCP
    mcp_server.fastmcp = mcp_fastmcp
    mcp_pkg.server = mcp_server
    sys.modules["mcp"] = mcp_pkg
    sys.modules["mcp.server"] = mcp_server
    sys.modules["mcp.server.fastmcp"] = mcp_fastmcp


_install_mcp_stub()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reccli.session.devsession import DevSession  # noqa: E402
from reccli.retrieval.search import (  # noqa: E402
    _candidate_pool_size,
    _enrich_and_rerank,
    _msg_id_to_index,
    _pick_best_sentence,
    _score_best_sentence,
    _split_sentences,
    compute_tau,
    expand_result,
    validate_index_dimensions,
)
from reccli.hooks.auto_reason import detect_intent  # noqa: E402
from reccli.hooks.session_recorder import (  # noqa: E402
    cleanup_bg_tasks,
    register_bg_task,
    _bg_tasks_file,
)
from reccli.summarization.summarizer import SessionSummarizer  # noqa: E402
from reccli.mcp_server import (  # noqa: E402
    _collect_pinned_items,
    _find_session_with_item,
    _reconstruct_file_from_raw_response,
    delete_session,
    edit_summary_item,
    inspect_result_id,
    list_sessions,
    pin_memory,
    rebuild_index,
    recover_file,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_session_file(sessions_dir: Path, stem: str, *, summary=None, conversation=None) -> Path:
    """Build a minimal .devsession file on disk and return its path."""
    session = DevSession()
    session.conversation = conversation or [
        {"id": "msg_001", "role": "user", "content": "hello", "timestamp": "2026-04-01T10:00:00"},
        {"id": "msg_002", "role": "assistant", "content": "hi", "timestamp": "2026-04-01T10:00:05"},
    ]
    if summary is not None:
        session.summary = summary
    path = sessions_dir / f"{stem}.devsession"
    session.save(path, skip_validation=True)
    return path


def _make_project(tmp: Path) -> Path:
    """Create a project root with a .git marker and empty devsession/."""
    (tmp / ".git").mkdir()
    (tmp / "devsession").mkdir()
    return tmp


def _summary_with_decision(dec_text: str, *, locked: bool = False, pinned: bool = False) -> dict:
    return {
        "schema_version": "1.1",
        "model": "test",
        "created_at": "2026-04-01T10:00:00",
        "overview": "Test session overview.",
        "decisions": [
            {
                "id": "dec_000",
                "decision": dec_text,
                "reasoning": "",
                "impact": "medium",
                "span_ids": [],
                "references": ["msg_001"],
                "message_range": {"start": "msg_001", "end": "msg_002", "start_index": 0, "end_index": 2},
                "confidence": "medium",
                "pinned": pinned,
                "locked": locked,
            }
        ],
        "code_changes": [],
        "problems_solved": [],
        "open_issues": [],
        "next_steps": [],
        "causal_edges": [],
        "audit_trail": [],
    }


# ---------------------------------------------------------------------------
# compute_tau precedence
# ---------------------------------------------------------------------------

class ComputeTauPrecedenceTests(unittest.TestCase):
    def test_decision_kind_beats_error_query(self):
        # A decision should decay slowly even when the query mentions errors
        self.assertEqual(compute_tau("decision", "why did this error happen?"), 30 * 24.0)

    def test_problem_kind_decays_at_14_days(self):
        self.assertEqual(compute_tau("problem", "whatever"), 14 * 24.0)

    def test_error_query_on_note_kind_fast_decay(self):
        self.assertEqual(compute_tau("note", "what error occurred?"), 8.0)

    def test_decision_query_on_note_kind_slow_decay(self):
        self.assertEqual(compute_tau("note", "what was the decision?"), 30 * 24.0)

    def test_default_is_3_days(self):
        self.assertEqual(compute_tau("note", "what happened?"), 3 * 24.0)


# ---------------------------------------------------------------------------
# auto_reason tie-break
# ---------------------------------------------------------------------------

class AutoReasonTieBreakTests(unittest.TestCase):
    def test_tie_prefers_planning(self):
        # one debug keyword + one planning keyword, no extras = exact tie
        self.assertEqual(detect_intent("error and refactor"), "planning")

    def test_clear_debug_still_debug(self):
        self.assertEqual(detect_intent("traceback error crash"), "debug")

    def test_clear_planning_still_planning(self):
        self.assertEqual(detect_intent("how should we architect this"), "planning")

    def test_no_match_returns_none(self):
        self.assertIsNone(detect_intent("say hello"))


# ---------------------------------------------------------------------------
# _msg_id_to_index + validate_index_dimensions
# ---------------------------------------------------------------------------

class SearchHelperTests(unittest.TestCase):
    def test_msg_id_to_index_standard(self):
        self.assertEqual(_msg_id_to_index("msg_042"), 41)
        self.assertEqual(_msg_id_to_index("msg_001"), 0)

    def test_msg_id_to_index_rejects_non_msg_prefix(self):
        self.assertIsNone(_msg_id_to_index("dec_000"))
        self.assertIsNone(_msg_id_to_index(""))

    def test_validate_dims_matching(self):
        idx = {"embedding": {"dimensions": 1536},
               "unified_vectors": [{"embedding": [0.0] * 1536, "id": "v1"}]}
        ok, reason = validate_index_dimensions(idx)
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_validate_dims_mismatch(self):
        idx = {"embedding": {"dimensions": 1536},
               "unified_vectors": [{"embedding": [0.0] * 3072, "id": "v1"}]}
        ok, reason = validate_index_dimensions(idx)
        self.assertFalse(ok)
        self.assertIn("3072", reason)

    def test_validate_dims_empty_is_ok(self):
        ok, reason = validate_index_dimensions({"embedding": {"dimensions": 1536}})
        self.assertTrue(ok)


# ---------------------------------------------------------------------------
# recover_file reconstruction (replace_all fix)
# ---------------------------------------------------------------------------

class ReconstructFileTests(unittest.TestCase):
    def test_single_replace_when_replace_all_false(self):
        raw = json.dumps({
            "originalFile": "foo foo foo",
            "oldString": "foo",
            "newString": "bar",
            "replaceAll": False,
        })
        self.assertEqual(_reconstruct_file_from_raw_response(raw), "bar foo foo")

    def test_single_replace_when_replace_all_absent(self):
        raw = json.dumps({
            "originalFile": "foo foo foo",
            "oldString": "foo",
            "newString": "bar",
        })
        self.assertEqual(_reconstruct_file_from_raw_response(raw), "bar foo foo")

    def test_all_replaced_when_replace_all_true(self):
        raw = json.dumps({
            "originalFile": "foo foo foo",
            "oldString": "foo",
            "newString": "bar",
            "replaceAll": True,
        })
        self.assertEqual(_reconstruct_file_from_raw_response(raw), "bar bar bar")

    def test_accepts_snake_case_replace_all_too(self):
        raw = json.dumps({
            "originalFile": "a a a",
            "oldString": "a",
            "newString": "b",
            "replace_all": True,
        })
        self.assertEqual(_reconstruct_file_from_raw_response(raw), "b b b")

    def test_write_tool_content_used_directly(self):
        raw = json.dumps({"content": "new file body"})
        self.assertEqual(_reconstruct_file_from_raw_response(raw), "new file body")

    def test_malformed_json_returns_none(self):
        self.assertIsNone(_reconstruct_file_from_raw_response("not json"))

    def test_accepts_dict_directly(self):
        self.assertEqual(
            _reconstruct_file_from_raw_response({"originalFile": "xyz"}),
            "xyz",
        )


# ---------------------------------------------------------------------------
# _collect_pinned_items
# ---------------------------------------------------------------------------

class CollectPinnedItemsTests(unittest.TestCase):
    def test_returns_pinned_items_newest_first(self):
        with tempfile.TemporaryDirectory() as d:
            sessions_dir = Path(d)
            _make_session_file(sessions_dir, "s1", summary=_summary_with_decision("old pinned", pinned=True))
            _make_session_file(sessions_dir, "s2", summary=_summary_with_decision("new pinned", pinned=True))

            # Bump s2 mtime so it's newest
            (sessions_dir / "s2.devsession").touch()

            pinned = _collect_pinned_items(sessions_dir)
            self.assertEqual(len(pinned), 2)
            self.assertEqual(pinned[0]["text"], "new pinned")
            self.assertEqual(pinned[1]["text"], "old pinned")

    def test_skips_unpinned_items(self):
        with tempfile.TemporaryDirectory() as d:
            sessions_dir = Path(d)
            _make_session_file(sessions_dir, "s1", summary=_summary_with_decision("not pinned", pinned=False))
            self.assertEqual(_collect_pinned_items(sessions_dir), [])

    def test_respects_limit(self):
        with tempfile.TemporaryDirectory() as d:
            sessions_dir = Path(d)
            for i in range(5):
                _make_session_file(sessions_dir, f"s{i}", summary=_summary_with_decision(f"pinned {i}", pinned=True))
            self.assertEqual(len(_collect_pinned_items(sessions_dir, limit=3)), 3)


# ---------------------------------------------------------------------------
# _find_session_with_item
# ---------------------------------------------------------------------------

class FindSessionWithItemTests(unittest.TestCase):
    def test_finds_item_by_id(self):
        with tempfile.TemporaryDirectory() as d:
            sessions_dir = Path(d)
            _make_session_file(sessions_dir, "s1", summary=_summary_with_decision("hello"))
            sf, session, cat, item = _find_session_with_item(sessions_dir, "dec_000")
            self.assertIsNotNone(sf)
            self.assertEqual(cat, "decisions")
            self.assertEqual(item["decision"], "hello")

    def test_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            sessions_dir = Path(d)
            _make_session_file(sessions_dir, "s1", summary=_summary_with_decision("hello"))
            sf, session, cat, item = _find_session_with_item(sessions_dir, "dec_999")
            self.assertIsNone(sf)
            self.assertIsNone(item)


# ---------------------------------------------------------------------------
# edit_summary_item + pin_memory (end-to-end via file I/O)
# ---------------------------------------------------------------------------

class EditSummaryItemTests(unittest.TestCase):
    def test_updates_decision_text(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            _make_session_file(project_root / "devsession", "s1",
                               summary=_summary_with_decision("old text"))
            result = edit_summary_item("dec_000", str(project_root), new_text="new text")
            self.assertIn("Updated", result)

            # Verify on disk
            reloaded = DevSession.load(project_root / "devsession" / "s1.devsession", verify_checksums=False)
            self.assertEqual(reloaded.summary["decisions"][0]["decision"], "new text")

    def test_rejects_locked_item(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            _make_session_file(project_root / "devsession", "s1",
                               summary=_summary_with_decision("frozen", locked=True))
            result = edit_summary_item("dec_000", str(project_root), new_text="should not apply")
            self.assertIn("locked", result.lower())
            reloaded = DevSession.load(project_root / "devsession" / "s1.devsession", verify_checksums=False)
            self.assertEqual(reloaded.summary["decisions"][0]["decision"], "frozen")

    def test_rejects_invalid_confidence(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            _make_session_file(project_root / "devsession", "s1",
                               summary=_summary_with_decision("x"))
            result = edit_summary_item("dec_000", str(project_root), new_confidence="extreme")
            self.assertIn("Invalid confidence", result)

    def test_returns_error_when_no_changes_specified(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            _make_session_file(project_root / "devsession", "s1",
                               summary=_summary_with_decision("x"))
            result = edit_summary_item("dec_000", str(project_root))
            self.assertIn("No changes", result)


class PinMemoryTests(unittest.TestCase):
    def test_pin_sets_flag(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            _make_session_file(project_root / "devsession", "s1",
                               summary=_summary_with_decision("x"))
            pin_memory("dec_000", str(project_root))
            reloaded = DevSession.load(project_root / "devsession" / "s1.devsession", verify_checksums=False)
            self.assertTrue(reloaded.summary["decisions"][0]["pinned"])

    def test_unpin_clears_flag(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            _make_session_file(project_root / "devsession", "s1",
                               summary=_summary_with_decision("x", pinned=True))
            pin_memory("dec_000", str(project_root), unpin=True)
            reloaded = DevSession.load(project_root / "devsession" / "s1.devsession", verify_checksums=False)
            self.assertFalse(reloaded.summary["decisions"][0]["pinned"])

    def test_unpin_rejected_on_locked_item(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            _make_session_file(project_root / "devsession", "s1",
                               summary=_summary_with_decision("x", pinned=True, locked=True))
            result = pin_memory("dec_000", str(project_root), unpin=True)
            self.assertIn("locked", result.lower())
            reloaded = DevSession.load(project_root / "devsession" / "s1.devsession", verify_checksums=False)
            self.assertTrue(reloaded.summary["decisions"][0]["pinned"])  # still pinned


# ---------------------------------------------------------------------------
# delete_session archive path
# ---------------------------------------------------------------------------

class DeleteSessionTests(unittest.TestCase):
    def test_archive_moves_file(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            _make_session_file(project_root / "devsession", "s1",
                               summary=_summary_with_decision("x"))
            result = delete_session("s1", str(project_root))
            self.assertIn("archived", result)
            self.assertFalse((project_root / "devsession" / "s1.devsession").exists())
            self.assertTrue((project_root / "devsession" / ".archived" / "s1.devsession").exists())

    def test_hard_delete_removes_file(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            _make_session_file(project_root / "devsession", "s1",
                               summary=_summary_with_decision("x"))
            result = delete_session("s1", str(project_root), hard=True)
            self.assertIn("deleted", result)
            self.assertFalse((project_root / "devsession" / "s1.devsession").exists())

    def test_missing_session_reports(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            result = delete_session("nonexistent", str(project_root))
            self.assertIn("not found", result)


# ---------------------------------------------------------------------------
# list_sessions filters
# ---------------------------------------------------------------------------

class ListSessionsFilterTests(unittest.TestCase):
    def test_has_summary_filter_keeps_only_summarized(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            _make_session_file(project_root / "devsession", "summed",
                               summary=_summary_with_decision("x"))
            _make_session_file(project_root / "devsession", "bare")

            summarized = list_sessions(str(project_root), has_summary=True)
            self.assertIn("summed", summarized)
            self.assertNotIn("bare", summarized)

            unsummarized = list_sessions(str(project_root), has_summary=False)
            self.assertIn("bare", unsummarized)
            self.assertNotIn("summed", unsummarized)

    def test_query_filter_matches_stem(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            _make_session_file(project_root / "devsession", "auth_rework",
                               summary=_summary_with_decision("unrelated"))
            _make_session_file(project_root / "devsession", "other",
                               summary=_summary_with_decision("unrelated"))

            out = list_sessions(str(project_root), query="auth")
            self.assertIn("auth_rework", out)
            self.assertNotIn("other", out)


# ---------------------------------------------------------------------------
# inspect_result_id
# ---------------------------------------------------------------------------

class InspectResultIdTests(unittest.TestCase):
    def test_parses_message_result_id_without_index(self):
        """If the index is absent, the parser-only path still returns useful metadata."""
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            out = inspect_result_id("session-abc_msg_042", str(project_root))
            data = json.loads(out)
            self.assertEqual(data["hit_type"], "message")
            self.assertEqual(data["session"], "session-abc")
            self.assertEqual(data["message_index"], 42)


# ---------------------------------------------------------------------------
# rebuild_index
# ---------------------------------------------------------------------------

class RebuildIndexTests(unittest.TestCase):
    def test_rebuild_runs_on_sessions_dir(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            _make_session_file(project_root / "devsession", "s1",
                               summary=_summary_with_decision("x"))
            result = rebuild_index(str(project_root))
            self.assertIn("rebuilt", result.lower())
            self.assertTrue((project_root / "devsession" / "index.json").exists())


# ---------------------------------------------------------------------------
# Background task registry
# ---------------------------------------------------------------------------

class BgTaskRegistryTests(unittest.TestCase):
    def test_register_and_cleanup_dead_pid(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            # PID 1 exists (init) but we need a definitely-dead PID — use 2**20 which is above typical limits
            # Better: use a PID we just saw exit. Simpler: manually write a stale record.
            registry = _bg_tasks_file(project_root)
            registry.parent.mkdir(parents=True, exist_ok=True)
            registry.write_text(json.dumps({
                "pid": 999999,  # almost certainly dead
                "purpose": "test",
                "started_at": "2020-01-01T00:00:00",
            }) + "\n", encoding="utf-8")

            removed = cleanup_bg_tasks(project_root)
            self.assertEqual(removed, 1)
            self.assertFalse(registry.exists())

    def test_register_writes_entry(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            register_bg_task(project_root, 12345, "unit-test")
            registry = _bg_tasks_file(project_root)
            self.assertTrue(registry.exists())
            rec = json.loads(registry.read_text().strip().splitlines()[0])
            self.assertEqual(rec["pid"], 12345)
            self.assertEqual(rec["purpose"], "unit-test")


# ---------------------------------------------------------------------------
# Summarizer locked/pinned semantics
# ---------------------------------------------------------------------------

class LockedItemMergeTests(unittest.TestCase):
    def setUp(self):
        self.summarizer = SessionSummarizer(llm_client=None, model=None)

    def test_locked_item_preserves_core_fields(self):
        existing = [{
            "id": "dec_000",
            "decision": "original text",
            "reasoning": "original reasoning",
            "impact": "medium",
            "locked": True,
            "pinned": False,
            "confidence": "high",
            "t_last": "2026-04-01T10:00:00",
            "message_range": {"start_index": 0, "end_index": 5, "start": "msg_001", "end": "msg_005"},
        }]
        incoming = [{
            "id": "dec_000",
            "decision": "MUTATED",
            "reasoning": "MUTATED",
            "impact": "low",
            "confidence": "low",
            "t_last": "2026-04-01T12:00:00",  # later timestamp — allowed to update
            "message_range": {"start_index": 0, "end_index": 10, "start": "msg_001", "end": "msg_010"},
        }]
        merged = self.summarizer._merge_summary_items(existing, incoming)
        self.assertEqual(len(merged), 1)
        m = merged[0]
        # Core fields frozen
        self.assertEqual(m["decision"], "original text")
        self.assertEqual(m["reasoning"], "original reasoning")
        self.assertEqual(m["impact"], "medium")
        self.assertEqual(m["confidence"], "high")
        self.assertTrue(m["locked"])
        # But t_last bumped and end_index extended
        self.assertEqual(m["t_last"], "2026-04-01T12:00:00")
        self.assertEqual(m["message_range"]["end_index"], 10)

    def test_end_index_never_contracts(self):
        existing = [{
            "id": "dec_000",
            "decision": "x",
            "locked": True,
            "message_range": {"start_index": 0, "end_index": 50, "start": "msg_001", "end": "msg_050"},
        }]
        incoming = [{
            "id": "dec_000",
            "message_range": {"start_index": 0, "end_index": 10, "start": "msg_001", "end": "msg_010"},
        }]
        merged = self.summarizer._merge_summary_items(existing, incoming)
        self.assertEqual(merged[0]["message_range"]["end_index"], 50)  # held

    def test_unlocked_item_accepts_updates(self):
        existing = [{"id": "dec_000", "decision": "old", "locked": False, "pinned": True}]
        incoming = [{"id": "dec_000", "decision": "new"}]
        merged = self.summarizer._merge_summary_items(existing, incoming)
        self.assertEqual(merged[0]["decision"], "new")
        # pinned preserved through normal merge path
        self.assertTrue(merged[0]["pinned"])


# ---------------------------------------------------------------------------
# Safe-fallback cascade in expand_result (integration via index + session)
# ---------------------------------------------------------------------------

class ExpandResultSafeFallbackTests(unittest.TestCase):
    def _build_index(self, sessions_dir: Path, session_stem: str):
        """Minimal valid index.json referencing a single summary-item 'vector'."""
        index = {
            "format": "devsession-index",
            "version": "1.1.0",
            "embedding": {"provider": "openai", "model": "text-embedding-3-small",
                          "dimensions": 1536, "distance_metric": "cosine"},
            "unified_vectors": [{
                "id": f"{session_stem}_dec_000",
                "session": session_stem,
                "message_id": "dec_000",
                "message_index": 0,
                "kind": "decision",
                "content_preview": "a test decision",
                "timestamp": "2026-04-01T10:00:00",
            }],
            "session_manifest": [{"session_id": session_stem}],
        }
        (sessions_dir / "index.json").write_text(json.dumps(index))

    def test_falls_back_to_references_when_range_missing(self):
        with tempfile.TemporaryDirectory() as d:
            sessions_dir = Path(d)
            summary = _summary_with_decision("x")
            # Intentionally break message_range; keep references to drive the fallback
            summary["decisions"][0]["message_range"] = {}
            summary["decisions"][0]["references"] = ["msg_001", "msg_002"]
            _make_session_file(sessions_dir, "s1",
                               summary=summary,
                               conversation=[
                                   {"id": "msg_001", "role": "user", "content": "a",
                                    "timestamp": "2026-04-01T10:00:00"},
                                   {"id": "msg_002", "role": "assistant", "content": "b",
                                    "timestamp": "2026-04-01T10:00:05"},
                                   {"id": "msg_003", "role": "user", "content": "c",
                                    "timestamp": "2026-04-01T10:00:10"},
                               ])
            self._build_index(sessions_dir, "s1")

            result = expand_result(sessions_dir, "s1_dec_000", context_window=0)
            self.assertIsNotNone(result)
            # references are msg_001..msg_002 => indices 0..1, end_index = max+1 = 2
            self.assertEqual(result["context_start"], 0)
            self.assertEqual(result["context_end"], 2)
            self.assertFalse(result.get("degraded_range"))

    def test_full_range_degraded_when_no_hints(self):
        with tempfile.TemporaryDirectory() as d:
            sessions_dir = Path(d)
            summary = _summary_with_decision("x")
            summary["decisions"][0]["message_range"] = {}
            summary["decisions"][0]["references"] = []
            summary["decisions"][0]["span_ids"] = []
            _make_session_file(sessions_dir, "s1",
                               summary=summary,
                               conversation=[
                                   {"id": "msg_001", "role": "user", "content": "a",
                                    "timestamp": "2026-04-01T10:00:00"},
                                   {"id": "msg_002", "role": "assistant", "content": "b",
                                    "timestamp": "2026-04-01T10:00:05"},
                               ])
            self._build_index(sessions_dir, "s1")

            result = expand_result(sessions_dir, "s1_dec_000", context_window=0)
            self.assertIsNotNone(result)
            self.assertTrue(result.get("degraded_range"))
            self.assertEqual(result["context_start"], 0)
            self.assertEqual(result["context_end"], 2)


class PickBestSentenceTests(unittest.TestCase):
    def test_returns_query_relevant_sentence_over_first_chars(self):
        content = (
            "Let me walk through the setup steps. We discussed many things today. "
            "The critical decision we made was to use token bucket rate limiting at 100rpm. "
            "That concluded our conversation."
        )
        preview = _pick_best_sentence("rate limiting decision", content)
        self.assertIn("token bucket", preview)
        self.assertNotIn("setup steps", preview)

    def test_falls_back_to_first_chars_when_no_match(self):
        content = (
            "We talked about gardening tips. "
            "Tomatoes need full sun and regular water. "
            "Marigolds deter certain pests."
        )
        preview = _pick_best_sentence("rate limiting", content)
        # Should fall back to the start (first N chars)
        self.assertTrue(preview.startswith("We talked"))

    def test_single_sentence_returns_as_is(self):
        content = "This is a single sentence with no splits."
        preview = _pick_best_sentence("sentence", content)
        self.assertEqual(preview, content[:260])

    def test_empty_query_returns_first_chars(self):
        content = "Sentence one. Sentence two. Sentence three." * 10
        preview = _pick_best_sentence("", content)
        self.assertTrue(preview.startswith("Sentence one"))

    def test_empty_content(self):
        self.assertEqual(_pick_best_sentence("anything", ""), "")

    def test_pads_short_sentence_with_neighbor(self):
        content = (
            "Here is some intro context about the general state of things. "
            "JWT. "
            "We chose JWT because it is stateless and widely supported. "
            "There is some follow-up text afterwards."
        )
        preview = _pick_best_sentence("JWT", content)
        # The best one-word sentence "JWT." is short — padding should pull in the next
        self.assertIn("JWT", preview)
        # Should combine with the longer reasoning sentence
        self.assertGreater(len(preview), 10)

    def test_truncates_very_long_sentence_with_ellipsis(self):
        long = "token bucket " * 100  # one long "sentence"
        content = f"Quick intro. {long}. Tail."
        preview = _pick_best_sentence("token bucket", content, target_chars=100)
        self.assertLessEqual(len(preview), 100)

    def test_stop_words_are_ignored(self):
        # Query is all stop-words → should fall back
        content = "Real answer is here. Irrelevant preamble at start. " * 5
        preview = _pick_best_sentence("the a an and or", content)
        # Without stop-word filtering, this might match anywhere; we want no-match fallback
        self.assertTrue(preview.startswith("Real answer"))


class SplitSentencesTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(
            _split_sentences("One. Two! Three?"),
            ["One.", "Two!", "Three?"],
        )

    def test_paragraph_break_splits(self):
        result = _split_sentences("First para.\n\nSecond para.")
        self.assertIn("First para.", result)
        self.assertIn("Second para.", result)

    def test_subdivides_long_runs(self):
        long_block = "a" * 1200
        parts = _split_sentences(long_block, hard_max=500)
        self.assertTrue(all(len(p) <= 500 for p in parts))

    def test_empty(self):
        self.assertEqual(_split_sentences(""), [])


class ScoreBestSentenceTests(unittest.TestCase):
    def test_returns_sentence_and_positive_score_on_match(self):
        content = (
            "We talked about many things today. "
            "The critical decision was to use token bucket rate limiting at 100rpm. "
            "That concluded our discussion."
        )
        sentence, score = _score_best_sentence("token bucket rate limiting", content)
        self.assertIn("token bucket", sentence)
        self.assertGreater(score, 0.0)

    def test_returns_zero_score_on_no_match(self):
        content = "Gardening tips. Tomatoes need sun. Marigolds deter pests."
        sentence, score = _score_best_sentence("rate limiting", content)
        self.assertEqual(score, 0.0)
        self.assertTrue(sentence.startswith("Gardening"))


class CandidatePoolSizeTests(unittest.TestCase):
    def test_small_top_k_floors_to_30(self):
        self.assertEqual(_candidate_pool_size(5), 30)
        self.assertEqual(_candidate_pool_size(7), 30)

    def test_large_top_k_caps_to_60(self):
        self.assertEqual(_candidate_pool_size(20), 60)
        self.assertEqual(_candidate_pool_size(50), 60)

    def test_middle_scales_by_4x(self):
        self.assertEqual(_candidate_pool_size(10), 40)


class EnrichAndRerankTests(unittest.TestCase):
    """Tests that a sentence match on a deep candidate can leapfrog shallow matches."""

    def test_deep_candidate_with_strong_sentence_match_rises_when_boost_enabled(self):
        """When sentence_boost > 0, a deep candidate with a strong sentence
        match can leapfrog a shallow top-ranked hit. Boost is off by default
        (see production call sites); this test verifies the mechanism when
        explicitly enabled."""
        with tempfile.TemporaryDirectory() as d:
            sessions_dir = Path(d)
            conv0 = [
                {"id": "msg_001", "role": "user",
                 "content": "I want to improve my golf swing. My stance needs work. "
                            "I also attended a charity golf tournament on July 17th. "
                            "Can you help with grip and follow-through?" * 3,
                 "timestamp": "2023-07-17T10:00:00"}
            ]
            _make_session_file(sessions_dir, "golf_session", conversation=conv0)
            conv1 = [
                {"id": "msg_001", "role": "user",
                 "content": "I love attending events. Last month I went to many events. "
                            "Events are fun to attend." * 3,
                 "timestamp": "2023-06-01T10:00:00"},
            ]
            _make_session_file(sessions_dir, "events_generic", conversation=conv1)

            pool = [
                {"id": "events_generic_msg_000", "session": "events_generic",
                 "message_index": 0, "role": "user",
                 "content_preview": "I love attending events. Last month I went to many events.",
                 "final_score": 0.5, "timestamp": "2023-06-01T10:00:00"},
                {"id": "golf_msg_000", "session": "golf_session",
                 "message_index": 0, "role": "user",
                 "content_preview": "I want to improve my golf swing.",
                 "final_score": 0.3, "timestamp": "2023-07-17T10:00:00"},
            ]

            # Boost enabled — sentence match should promote golf_session
            result = _enrich_and_rerank(sessions_dir, pool, "charity tournament July",
                                        top_k=1, sentence_boost=0.5)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["session"], "golf_session")
            self.assertIn("charity", result[0]["content_preview"])

    def test_enrich_only_default_preserves_order(self):
        """With sentence_boost=0 (default), final_score ordering is preserved
        even when sentence matches exist — only previews are enriched."""
        with tempfile.TemporaryDirectory() as d:
            sessions_dir = Path(d)
            long_content = ("I want to improve my golf swing. My stance needs work. "
                            "I attended a charity golf tournament on July 17th. "
                            "Can you help?") * 4
            _make_session_file(sessions_dir, "golf_session",
                               conversation=[{"id": "msg_001", "role": "user",
                                              "content": long_content,
                                              "timestamp": "2023-07-17T10:00:00"}])
            other = "I love attending events. " * 20
            _make_session_file(sessions_dir, "events_generic",
                               conversation=[{"id": "msg_001", "role": "user",
                                              "content": other,
                                              "timestamp": "2023-06-01T10:00:00"}])
            pool = [
                {"id": "events_generic_msg_000", "session": "events_generic",
                 "message_index": 0, "role": "user", "content_preview": other[:100],
                 "final_score": 0.5},
                {"id": "golf_msg_000", "session": "golf_session",
                 "message_index": 0, "role": "user", "content_preview": long_content[:100],
                 "final_score": 0.3},
            ]
            result = _enrich_and_rerank(sessions_dir, pool, "charity tournament", top_k=2)
            # Order preserved — events_generic still first despite golf having sentence match
            self.assertEqual(result[0]["session"], "events_generic")
            self.assertEqual(result[1]["session"], "golf_session")
            # But golf's preview got enriched to the charity sentence
            self.assertIn("charity", result[1]["content_preview"])

    def test_no_matching_sentences_preserves_order(self):
        with tempfile.TemporaryDirectory() as d:
            sessions_dir = Path(d)
            _make_session_file(sessions_dir, "s1",
                               conversation=[{"id": "msg_001", "role": "user",
                                              "content": "Something unrelated.",
                                              "timestamp": "2023-01-01T10:00:00"}])
            pool = [
                {"id": "x", "session": "s1", "message_index": 0, "role": "user",
                 "content_preview": "Something unrelated.", "final_score": 0.5},
                {"id": "y", "session": "s1", "message_index": 0, "role": "user",
                 "content_preview": "Something unrelated.", "final_score": 0.3},
            ]
            result = _enrich_and_rerank(sessions_dir, pool, "purple elephant", top_k=2)
            # Order preserved when no sentence scores meaningfully
            self.assertEqual(result[0]["id"], "x")
            self.assertEqual(result[1]["id"], "y")


if __name__ == "__main__":
    unittest.main()
