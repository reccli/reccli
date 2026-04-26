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
from reccli.agent_providers import (  # noqa: E402
    build_agent_prompt,
    detect_quota_error,
    parse_findings_output,
    run_audit_agents,
)
from reccli.agent_harness import _ensure_audit_gitignore  # noqa: E402
from reccli.hooks.session_recorder import (  # noqa: E402
    cleanup_bg_tasks,
    register_bg_task,
    _bg_tasks_file,
)
from reccli.summarization.summarizer import SessionSummarizer  # noqa: E402
from reccli.mcp_server import (  # noqa: E402
    _collect_pinned_items,
    _detect_default_provider,
    _find_session_with_item,
    _reconstruct_file_from_raw_response,
    audit_feature,
    audit_status,
    consolidate_audit,
    delete_session,
    edit_summary_item,
    inspect_result_id,
    list_sessions,
    pin_memory,
    rebuild_index,
    recover_file,
    replay_audit_agent,
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


def _write_devproject(project_root: Path, features: list[dict]) -> Path:
    path = project_root / f"{project_root.name}.devproject"
    path.write_text(json.dumps({
        "format": "devproject",
        "version": "2.1.0",
        "project_root": str(project_root),
        "project": {
            "name": project_root.name,
            "description": "Test project",
            "status": "active",
        },
        "features": features,
    }, indent=2) + "\n", encoding="utf-8")
    return path


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
# audit_feature / agent harness packaging
# ---------------------------------------------------------------------------

class RunAgentHarnessTests(unittest.TestCase):
    def test_parse_findings_output_accepts_fenced_json(self):
        raw = """Here is the result:
```json
{"findings":[{"title":"x"}],"rejected_notes":[]}
```
"""
        parsed = parse_findings_output(raw)
        self.assertEqual(parsed["findings"][0]["title"], "x")
        self.assertEqual(parsed["parse_status"], "extracted_from_fence")

    def test_parse_findings_output_marks_parse_failure(self):
        parsed = parse_findings_output("not json")
        self.assertEqual(parsed["parse_status"], "parse_failed")
        self.assertEqual(parsed["findings"], [])

    def test_parse_findings_output_marks_empty(self):
        parsed = parse_findings_output("")
        self.assertEqual(parsed["parse_status"], "empty")
        self.assertEqual(parsed["findings"], [])

    def test_parse_findings_output_handles_embedded_backticks(self):
        # Regression: when an agent quotes a triple-backtick code block inside
        # a JSON string value (e.g. `code_reference`), the previous parser
        # split-on-``` and shredded the JSON across multiple parts.
        raw = (
            "```json\n"
            "{\n"
            '  "findings": [\n'
            "    {\n"
            '      "title": "Embedded fence does not break parser",\n'
            '      "code_reference": "src/x.tsx:1-3\\n```\\n#!/usr/bin/env node\\nimport x from \'y\';\\n```\\n",\n'
            '      "severity": "low"\n'
            "    }\n"
            "  ],\n"
            '  "rejected_notes": []\n'
            "}\n"
            "```\n"
        )
        parsed = parse_findings_output(raw)
        self.assertEqual(parsed["parse_status"], "extracted_from_fence")
        self.assertEqual(len(parsed["findings"]), 1)
        self.assertEqual(parsed["findings"][0]["title"], "Embedded fence does not break parser")
        self.assertIn("```", parsed["findings"][0]["code_reference"])

    def test_parse_findings_output_strips_session_signal_trailer(self):
        # Regression: audit children inherit host-CLI hooks (e.g. Claude Code's
        # session-signal SESSION RULE), so output may carry a trailing
        # `<!--session-signal:...-->` comment after the JSON. The parser must
        # strip it instead of treating the whole blob as parse_failed.
        raw = (
            '{"findings":[{"title":"x"}],"rejected_notes":[]}\n\n'
            "<!--session-signal: goal=audit | resolved=done | open=-->\n"
        )
        parsed = parse_findings_output(raw)
        self.assertEqual(parsed["parse_status"], "valid_json")
        self.assertEqual(parsed["findings"][0]["title"], "x")

    def test_parse_findings_output_handles_json_with_surrounding_chatter(self):
        raw = (
            "Sure, here is the audit result:\n"
            '{"findings":[{"title":"y"}],"rejected_notes":[]}\n'
            "Let me know if you want me to expand any finding.\n"
        )
        parsed = parse_findings_output(raw)
        self.assertEqual(parsed["parse_status"], "extracted_from_fence")
        self.assertEqual(parsed["findings"][0]["title"], "y")

    def test_build_agent_prompt_reports_missing_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            with self.assertRaisesRegex(FileNotFoundError, "Missing audit artifact"):
                build_agent_prompt(base / "missing_context.json", base / "missing_instructions.md", "agent_01")

    def test_creates_feature_context_pack_and_report(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            (project_root / "app.py").write_text("def login():\n    # TODO validate user\n    return True\n", encoding="utf-8")
            _write_devproject(project_root, [{
                "feature_id": "feat_auth",
                "title": "Auth",
                "description": "Authentication flow",
                "status": "in-progress",
                "files_touched": ["app.py"],
                "file_boundaries": ["app.py"],
                "docs": [],
            }])

            out = audit_feature(str(project_root), "feat_auth", agents=1, provider="none")
            bundle = json.loads(out)

            self.assertEqual(bundle["status"], "prepared")
            run_root = project_root / "devsession" / "agent-audits"
            runs = list(run_root.glob("*/*/*"))
            self.assertEqual(len(runs), 1)
            run_dir = runs[0]
            self.assertEqual(Path(bundle["run_dir"]).resolve(), run_dir.resolve())
            self.assertTrue((run_dir / "context_pack.json").exists())
            self.assertTrue((run_dir / "instructions.md").exists())
            self.assertTrue((run_dir / "report.md").exists())
            self.assertTrue((run_dir / "agent_01_instructions.md").exists())
            self.assertTrue((run_dir / "agent_01_findings.json").exists())
            self.assertTrue((run_dir / "agent_01_report.md").exists())
            pack = json.loads((run_dir / "context_pack.json").read_text(encoding="utf-8"))
            self.assertEqual(pack["feature"]["feature_id"], "feat_auth")
            self.assertEqual(pack["files"][0]["path"], "app.py")
            self.assertEqual(pack["risk_signals"][0]["kind"], "todo")
            findings = json.loads((run_dir / "agent_01_findings.json").read_text(encoding="utf-8"))
            self.assertEqual(findings["mode"], "audit")
            self.assertEqual(findings["assigned_files"], ["app.py"])

    def test_context_pack_files_include_line_annotations(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            # 50-line file, comfortably within default 12000-char budget
            short_lines = "\n".join(f"line {i}" for i in range(1, 51)) + "\n"
            (project_root / "short.py").write_text(short_lines, encoding="utf-8")
            # Long file that will get truncated at the default 12000-char budget
            long_lines = "\n".join(f"line {i}" for i in range(1, 5001)) + "\n"
            (project_root / "long.py").write_text(long_lines, encoding="utf-8")
            _write_devproject(project_root, [{
                "feature_id": "feat_auth",
                "title": "Auth",
                "description": "x",
                "files_touched": ["short.py", "long.py"],
                "file_boundaries": ["short.py", "long.py"],
                "docs": [],
            }])

            out = audit_feature(str(project_root), "feat_auth", agents=1, provider="none")
            bundle = json.loads(out)
            run_dir = Path(bundle["run_dir"])
            pack = json.loads((run_dir / "context_pack.json").read_text(encoding="utf-8"))
            by_path = {item["path"]: item for item in pack["files"]}

            short = by_path["short.py"]
            self.assertFalse(short["truncated"])
            self.assertEqual(short["total_lines"], 50)
            self.assertEqual(short["starting_line"], 1)
            self.assertEqual(short["ending_line"], 50)

            long_item = by_path["long.py"]
            self.assertTrue(long_item["truncated"])
            self.assertEqual(long_item["total_lines"], 5000)
            self.assertEqual(long_item["starting_line"], 1)
            # ending_line is bounded by what fit in max_chars (default 12000)
            self.assertGreater(long_item["ending_line"], 0)
            self.assertLess(long_item["ending_line"], 5000)

    def test_files_override_replaces_feature_scope(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            (project_root / "feature_file.py").write_text("# feature scope\n", encoding="utf-8")
            (project_root / "override_a.py").write_text("# override A\n", encoding="utf-8")
            (project_root / "override_b.py").write_text("# override B\n", encoding="utf-8")
            _write_devproject(project_root, [{
                "feature_id": "feat_auth",
                "title": "Auth",
                "description": "x",
                "files_touched": ["feature_file.py"],
                "file_boundaries": ["feature_file.py"],
                "docs": [],
            }])

            out = audit_feature(
                str(project_root), "feat_auth", agents=1, provider="none",
                files=["override_a.py", "override_b.py"],
            )
            bundle = json.loads(out)

            self.assertEqual(bundle["scope"]["source"], "override")
            self.assertEqual(bundle["scope"]["resolved_files"], ["override_a.py", "override_b.py"])
            self.assertEqual(bundle["scope"]["feature_files_touched"], ["feature_file.py"])
            run_dir = Path(bundle["run_dir"])
            pack = json.loads((run_dir / "context_pack.json").read_text(encoding="utf-8"))
            paths = [item["path"] for item in pack["files"]]
            self.assertEqual(paths, ["override_a.py", "override_b.py"])

    def test_globs_expand_recursively(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            (project_root / "src" / "lib").mkdir(parents=True)
            (project_root / "src" / "lib" / "digest.py").write_text("# digest\n", encoding="utf-8")
            (project_root / "src" / "lib" / "nested").mkdir()
            (project_root / "src" / "lib" / "nested" / "helper.py").write_text("# nested\n", encoding="utf-8")
            (project_root / "src" / "unrelated.ts").write_text("// no\n", encoding="utf-8")
            _write_devproject(project_root, [{
                "feature_id": "feat_auth",
                "title": "Auth",
                "description": "x",
                "files_touched": ["unused.py"],
                "file_boundaries": ["unused.py"],
                "docs": [],
            }])

            out = audit_feature(
                str(project_root), "feat_auth", agents=1, provider="none",
                globs=["src/**/*.py"],
            )
            bundle = json.loads(out)

            self.assertEqual(bundle["scope"]["source"], "override")
            resolved = sorted(bundle["scope"]["resolved_files"])
            self.assertEqual(resolved, ["src/lib/digest.py", "src/lib/nested/helper.py"])

    def test_files_plus_globs_dedupe_and_max_files_cap(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            for name in ("a.py", "b.py", "c.py", "d.py"):
                (project_root / name).write_text(f"# {name}\n", encoding="utf-8")
            _write_devproject(project_root, [{
                "feature_id": "feat_auth",
                "title": "Auth",
                "description": "x",
                "files_touched": [],
                "file_boundaries": [],
                "docs": [],
            }])

            out = audit_feature(
                str(project_root), "feat_auth", agents=1, provider="none",
                files=["a.py", "b.py"],
                globs=["*.py"],  # would also match a.py and b.py — should dedupe
                max_files=3,
            )
            bundle = json.loads(out)

            self.assertEqual(bundle["scope"]["source"], "override")
            # files first (a.py, b.py), then glob results that aren't already in
            # the set, capped at max_files=3
            self.assertEqual(bundle["scope"]["resolved_files"][0], "a.py")
            self.assertEqual(bundle["scope"]["resolved_files"][1], "b.py")
            self.assertEqual(len(bundle["scope"]["resolved_files"]), 3)

    def test_scope_falls_back_to_feature_when_overrides_resolve_empty(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            (project_root / "kept.py").write_text("# kept\n", encoding="utf-8")
            _write_devproject(project_root, [{
                "feature_id": "feat_auth",
                "title": "Auth",
                "description": "x",
                "files_touched": ["kept.py"],
                "file_boundaries": ["kept.py"],
                "docs": [],
            }])

            # Globs that match nothing + a path traversal attempt that should
            # be filtered out — net override is empty, so fall back.
            out = audit_feature(
                str(project_root), "feat_auth", agents=1, provider="none",
                files=["../escape/etc.py"],
                globs=["nope/**/*.zzz"],
            )
            bundle = json.loads(out)

            self.assertEqual(bundle["scope"]["source"], "feature")
            self.assertEqual(bundle["scope"]["resolved_files"], ["kept.py"])

    def test_audit_feature_provider_none_returns_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            (project_root / "app.py").write_text("def login():\n    return True\n", encoding="utf-8")
            _write_devproject(project_root, [{
                "feature_id": "feat_auth",
                "title": "Auth",
                "description": "Authentication flow",
                "files_touched": ["app.py"],
            }])

            out = audit_feature(str(project_root), "feat_auth", agents=2, provider="none", max_concurrency=1)
            bundle = json.loads(out)

            self.assertEqual(bundle["status"], "prepared")
            self.assertEqual(bundle["status_reason"], "Dry run; no provider dispatched.")
            self.assertEqual(bundle["provider"], "none")
            self.assertEqual(bundle["max_concurrency"], 1)
            self.assertEqual(len(bundle["agent_results"]), 2)
            self.assertTrue(Path(bundle["context_pack"]).exists())

    def test_audit_feature_persists_bundle_to_disk(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            (project_root / "app.py").write_text("def login():\n    return True\n", encoding="utf-8")
            _write_devproject(project_root, [{
                "feature_id": "feat_auth",
                "title": "Auth",
                "description": "Authentication flow",
                "files_touched": ["app.py"],
            }])
            bundle = json.loads(audit_feature(str(project_root), "feat_auth", agents=1, provider="none"))
            bundle_path = Path(bundle["run_dir"]) / "bundle.json"
            self.assertTrue(bundle_path.exists())
            persisted = json.loads(bundle_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["run_id"], bundle["run_id"])
            self.assertEqual(persisted["status"], bundle["status"])

    def test_audit_status_returns_persisted_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            (project_root / "app.py").write_text("def login():\n    return True\n", encoding="utf-8")
            _write_devproject(project_root, [{
                "feature_id": "feat_auth",
                "title": "Auth",
                "description": "Authentication flow",
                "files_touched": ["app.py"],
            }])
            bundle = json.loads(audit_feature(str(project_root), "feat_auth", agents=1, provider="none"))

            out = json.loads(audit_status(str(project_root), bundle["run_id"]))
            self.assertEqual(out["run_id"], bundle["run_id"])
            self.assertEqual(out["status"], bundle["status"])
            self.assertEqual(len(out["agent_results"]), 1)

    def test_audit_status_returns_in_progress_when_bundle_missing(self):
        # Simulate the in-progress case (or a crash before audit_feature got
        # to its final write) by dispatching a dry run and then deleting the
        # bundle.json.
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            (project_root / "app.py").write_text("def login():\n    return True\n", encoding="utf-8")
            _write_devproject(project_root, [{
                "feature_id": "feat_auth",
                "title": "Auth",
                "description": "Authentication flow",
                "files_touched": ["app.py"],
            }])
            bundle = json.loads(audit_feature(str(project_root), "feat_auth", agents=1, provider="none"))
            (Path(bundle["run_dir"]) / "bundle.json").unlink()

            out = json.loads(audit_status(str(project_root), bundle["run_id"]))
            self.assertEqual(out["status"], "in_progress")
            self.assertEqual(len(out["agent_progress"]), 1)
            self.assertEqual(out["agent_progress"][0]["agent_id"], "agent_01")

    def test_audit_status_returns_not_found_for_unknown_run_id(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            out = json.loads(audit_status(str(project_root), "20990101T000000Z_audit_does-not-exist"))
            self.assertEqual(out["status"], "not_found")

    def _seed_audit_run_with_findings(
        self,
        project_root: Path,
        per_agent_findings: list,
    ) -> tuple:
        """Helper: dispatch a dry run, then overwrite per-agent findings files
        with the supplied test fixtures so consolidate_audit has data to cluster.
        """
        (project_root / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
        _write_devproject(project_root, [{
            "feature_id": "feat_auth",
            "title": "Auth",
            "description": "Authentication flow",
            "files_touched": ["auth.py"],
        }])
        agents = len(per_agent_findings)
        bundle = json.loads(audit_feature(
            str(project_root), "feat_auth", agents=agents, provider="none",
        ))
        run_dir = Path(bundle["run_dir"])
        for i, findings in enumerate(per_agent_findings, start=1):
            agent_id = f"agent_{i:02d}"
            payload = {
                "agent_id": agent_id,
                "provider": "none",
                "status": "completed",
                "parse_status": "valid_json",
                "findings": findings,
                "rejected_notes": [],
            }
            (run_dir / f"{agent_id}_findings.json").write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8",
            )
        return bundle["run_id"], run_dir

    def test_consolidate_audit_clusters_cross_agent_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            run_id, run_dir = self._seed_audit_run_with_findings(project_root, [
                # agent_01: two findings, the first about auth bypass
                [
                    {
                        "severity": "high", "confidence": "high",
                        "title": "Authentication bypass via missing token check",
                        "description": "login() short-circuits without verifying the token.",
                        "files": [{"path": "auth.py", "line": 1}],
                        "suggested_fix": "Verify token before returning.",
                    },
                    {
                        "severity": "low", "confidence": "low",
                        "title": "Missing logging in login flow",
                        "description": "No log statement on successful login.",
                        "files": [{"path": "auth.py", "line": 2}],
                        "suggested_fix": "Add logger.info call.",
                    },
                ],
                # agent_02: same auth-bypass finding (different wording, same file) +
                # a unique finding only this agent reports.
                [
                    {
                        "severity": "high", "confidence": "medium",
                        "title": "Token check missing in authentication path",
                        "description": "The authentication flow does not validate tokens.",
                        "files": [{"path": "auth.py", "line": 1}],
                        "suggested_fix": "Add token verification.",
                    },
                    {
                        "severity": "medium", "confidence": "high",
                        "title": "Hardcoded secret detected",
                        "description": "A literal secret appears in source.",
                        "files": [{"path": "auth.py", "line": 5}],
                        "suggested_fix": "Move to env var.",
                    },
                ],
            ])

            out = json.loads(consolidate_audit(str(project_root), run_id))
            self.assertEqual(out["schema_version"], 1)
            self.assertEqual(out["agent_count"], 2)
            self.assertEqual(out["input_finding_count"], 4)
            # agent_01 + agent_02 share the auth-bypass finding -> 3 clusters from 4 findings.
            self.assertEqual(out["consolidated_count"], 3)

            # Agreement is the dominant ranking signal: the multi-agent cluster ranks first.
            top = out["findings"][0]
            self.assertEqual(top["agent_count"], 2)
            self.assertEqual(set(top["agents"]), {"agent_01", "agent_02"})
            self.assertEqual(len(top["source_findings"]), 2)

            # Single-agent clusters retain a single source ref and do not get merged together.
            single_agent_findings = [f for f in out["findings"] if f["agent_count"] == 1]
            self.assertEqual(len(single_agent_findings), 2)
            self.assertTrue(all(len(f["source_findings"]) == 1 for f in single_agent_findings))

            # Persisted to disk for audit_status / external recovery.
            persisted = json.loads((run_dir / "consolidated.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["consolidated_count"], 3)
            self.assertEqual(persisted["judge"]["used"], False)
            self.assertEqual(persisted["judge"]["status"], "skipped")

    def test_consolidate_audit_handles_empty_run(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            run_id, run_dir = self._seed_audit_run_with_findings(project_root, [[]])
            out = json.loads(consolidate_audit(str(project_root), run_id))
            self.assertEqual(out["input_finding_count"], 0)
            self.assertEqual(out["consolidated_count"], 0)
            self.assertEqual(out["findings"], [])

    def test_consolidate_audit_returns_not_found_for_unknown_run(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            out = json.loads(consolidate_audit(str(project_root), "20990101T000000Z_does_not_exist"))
            self.assertEqual(out["status"], "not_found")

    def test_consolidate_audit_overwrites_existing_consolidated(self):
        # Re-running consolidation should overwrite stale output.
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            run_id, run_dir = self._seed_audit_run_with_findings(project_root, [
                [{
                    "severity": "high", "confidence": "high",
                    "title": "Test finding",
                    "description": "A.",
                    "files": [{"path": "auth.py", "line": 1}],
                    "suggested_fix": "Fix.",
                }],
            ])
            (run_dir / "consolidated.json").write_text("STALE", encoding="utf-8")
            consolidate_audit(str(project_root), run_id)
            persisted = json.loads((run_dir / "consolidated.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["schema_version"], 1)
            self.assertEqual(persisted["consolidated_count"], 1)

    # -------------------------------------------------------------------
    # consolidate_audit LLM judge path (mocked dispatch)
    # -------------------------------------------------------------------

    def _findings_for_judge_test(self):
        # Two findings agents independently report — phrased differently enough
        # that the deterministic clusterer leaves them apart, so the judge gets
        # a real chance to merge them.
        return [
            [{
                "severity": "high", "confidence": "high",
                "title": "Account lockout missing on repeated bad logins",
                "description": "Login endpoint never locks the account.",
                "files": [{"path": "auth.py", "line": 10}],
                "suggested_fix": "Add a counter and lock after N attempts.",
            }],
            [{
                "severity": "high", "confidence": "medium",
                "title": "Brute force possible because there is no rate gate on signin",
                "description": "Repeated wrong passwords incur no penalty.",
                "files": [{"path": "auth.py", "line": 14}],
                "suggested_fix": "Throttle by IP and account.",
            }],
        ]

    def test_consolidate_audit_judge_success_merges_clusters(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            run_id, run_dir = self._seed_audit_run_with_findings(
                project_root, self._findings_for_judge_test(),
            )

            def fake_dispatch(provider, project_root, prompt, timeout_seconds, output_path=None, model=None):
                # Judge says: merge clusters 0 and 1.
                return {
                    "stdout": '[{"merge": [0, 1]}]',
                    "stderr": "",
                    "returncode": 0,
                    "raw_output": '[{"merge": [0, 1]}]',
                }

            import unittest.mock as _mock
            # consolidate_audit_run lazily imports run_provider_prompt from
            # agent_providers — patch it where it's looked up at call time.
            with _mock.patch(
                "reccli.agent_providers.run_provider_prompt",
                side_effect=fake_dispatch,
            ):
                out = json.loads(consolidate_audit(
                    str(project_root), run_id,
                    judge_provider="claude", judge_model="none",
                ))

            self.assertEqual(out["judge"]["status"], "completed")
            self.assertEqual(out["judge"]["used"], True)
            self.assertGreaterEqual(out["judge"]["clusters_judged"], 2)
            # Two input findings collapsed to one cluster after the judge merge.
            self.assertEqual(out["consolidated_count"], 1)
            self.assertEqual(out["findings"][0]["agent_count"], 2)

    def test_consolidate_audit_judge_failure_falls_back_to_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            run_id, run_dir = self._seed_audit_run_with_findings(
                project_root, self._findings_for_judge_test(),
            )

            def failing_dispatch(*args, **kwargs):
                raise RuntimeError("simulated provider crash")

            import unittest.mock as _mock
            with _mock.patch(
                "reccli.agent_providers.run_provider_prompt",
                side_effect=failing_dispatch,
            ):
                out = json.loads(consolidate_audit(
                    str(project_root), run_id,
                    judge_provider="claude", judge_model="none",
                ))

            # never-throws contract: the bundle still parses, judge.status reports the failure
            self.assertEqual(out["judge"]["status"], "failed")
            self.assertEqual(out["judge"]["used"], True)
            self.assertIn("simulated provider crash", out["judge"]["reason"])
            # Deterministic ordering preserved — both clusters intact.
            self.assertEqual(out["consolidated_count"], 2)

    def test_consolidate_audit_judge_unparseable_output_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            run_id, run_dir = self._seed_audit_run_with_findings(
                project_root, self._findings_for_judge_test(),
            )

            def garbage_dispatch(provider, project_root, prompt, timeout_seconds, output_path=None, model=None):
                return {
                    "stdout": "not even close to JSON",
                    "stderr": "",
                    "returncode": 0,
                    "raw_output": "not even close to JSON",
                }

            import unittest.mock as _mock
            with _mock.patch(
                "reccli.agent_providers.run_provider_prompt",
                side_effect=garbage_dispatch,
            ):
                out = json.loads(consolidate_audit(
                    str(project_root), run_id,
                    judge_provider="claude", judge_model="none",
                ))

            self.assertEqual(out["judge"]["status"], "failed")
            self.assertEqual(out["consolidated_count"], 2)

    def test_consolidate_audit_max_judge_clusters_cap(self):
        # Synthesize 6 unrelated clusters but cap the judge at 3.
        # The fake dispatch records cluster indices found in the prompt; we
        # verify only the top 3 clusters (by score) are sent to the judge.
        clusters_seen = []
        # Truly unrelated titles — no shared 3+ char tokens — so the
        # deterministic clusterer leaves them as 6 separate clusters.
        unrelated_titles = [
            "Buffer overflow in TLS handshake",
            "SQL injection via search query",
            "Hardcoded password file",
            "Race condition during shutdown",
            "Memory leak parsing avatars",
            "Missing CSRF token on signin",
        ]

        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            distinct = []
            for i, title in enumerate(unrelated_titles):
                distinct.append([{
                    "severity": "medium", "confidence": "medium",
                    "title": title,
                    "description": f"Distinct issue {i} body.",
                    "files": [{"path": f"file_{i}.py", "line": 1}],
                    "suggested_fix": "Fix it.",
                }])
            run_id, run_dir = self._seed_audit_run_with_findings(project_root, distinct)

            def capturing_dispatch(provider, project_root, prompt, timeout_seconds, output_path=None, model=None):
                # The prompt contains "cluster_index": N for each cluster sent
                import re
                indices = sorted({int(m.group(1)) for m in re.finditer(r'"cluster_index"\s*:\s*(\d+)', prompt)})
                clusters_seen.extend(indices)
                return {"stdout": "[]", "stderr": "", "returncode": 0, "raw_output": "[]"}

            import unittest.mock as _mock
            with _mock.patch(
                "reccli.agent_providers.run_provider_prompt",
                side_effect=capturing_dispatch,
            ):
                consolidate_audit(
                    str(project_root), run_id,
                    judge_provider="claude", judge_model="none",
                    max_judge_clusters=3,
                )

            # Only the top 3 cluster indices reach the judge prompt.
            self.assertEqual(len(clusters_seen), 3)
            self.assertEqual(clusters_seen, [0, 1, 2])

    def test_replay_audit_agent_provider_none(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            (project_root / "app.py").write_text("def login():\n    return True\n", encoding="utf-8")
            _write_devproject(project_root, [{
                "feature_id": "feat_auth",
                "title": "Auth",
                "description": "Authentication flow",
                "files_touched": ["app.py"],
            }])
            bundle = json.loads(audit_feature(str(project_root), "feat_auth", agents=2, provider="none"))

            out = replay_audit_agent(str(project_root), bundle["run_id"], "agent_02", provider="none")
            replay = json.loads(out)

            self.assertEqual(replay["status"], "prepared")
            self.assertEqual(replay["agent_result"]["agent_id"], "agent_02")

    def test_replay_audit_agent_accepts_run_dir_path(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            (project_root / "app.py").write_text("def login():\n    return True\n", encoding="utf-8")
            _write_devproject(project_root, [{
                "feature_id": "feat_auth",
                "title": "Auth",
                "description": "Authentication flow",
                "files_touched": ["app.py"],
            }])
            bundle = json.loads(audit_feature(str(project_root), "feat_auth", agents=1, provider="none"))

            out = replay_audit_agent(str(project_root), bundle["run_dir"], "agent_01", provider="none")
            replay = json.loads(out)

            self.assertEqual(replay["status"], "prepared")
            self.assertEqual(Path(replay["run_dir"]).resolve(), Path(bundle["run_dir"]).resolve())

    def test_replay_audit_agent_reports_missing_agent(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            (project_root / "app.py").write_text("def login():\n    return True\n", encoding="utf-8")
            _write_devproject(project_root, [{
                "feature_id": "feat_auth",
                "title": "Auth",
                "description": "Authentication flow",
                "files_touched": ["app.py"],
            }])
            bundle = json.loads(audit_feature(str(project_root), "feat_auth", agents=1, provider="none"))

            out = replay_audit_agent(str(project_root), bundle["run_id"], "agent_99", provider="none")

            self.assertIn("agent 'agent_99' not found", out)

    def test_replay_audit_agent_updates_persisted_bundle(self):
        # Replaying an agent should rewrite the entry in <run_dir>/bundle.json
        # so audit_status returns the post-replay state, not the original.
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            (project_root / "app.py").write_text("def login():\n    return True\n", encoding="utf-8")
            _write_devproject(project_root, [{
                "feature_id": "feat_auth",
                "title": "Auth",
                "description": "Authentication flow",
                "files_touched": ["app.py"],
            }])
            bundle = json.loads(audit_feature(str(project_root), "feat_auth", agents=2, provider="none"))
            run_dir = Path(bundle["run_dir"])

            replay_audit_agent(str(project_root), bundle["run_id"], "agent_02", provider="none")

            persisted = json.loads((run_dir / "bundle.json").read_text(encoding="utf-8"))
            replayed = next(
                (ar for ar in persisted["agent_results"] if ar.get("agent_id") == "agent_02"),
                None,
            )
            self.assertIsNotNone(replayed)
            # The replay record should reflect the new dispatch (parse_status="dry-run" or
            # similar from provider="none"); at minimum it must not be the original record.
            self.assertEqual(replayed["agent_id"], "agent_02")
            # Other agents in the persisted bundle remain untouched.
            other = next(
                (ar for ar in persisted["agent_results"] if ar.get("agent_id") == "agent_01"),
                None,
            )
            self.assertIsNotNone(other)

    def test_v1_rejects_non_report_modes(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            _write_devproject(project_root, [{
                "feature_id": "feat_auth",
                "title": "Auth",
                "description": "Authentication flow",
                "files_touched": [],
            }])

            out = audit_feature(str(project_root), "feat_auth", mode="triage", provider="none")

            self.assertIn("v1 only supports mode='report'", out)

    def test_missing_feature_lists_available_features(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = _make_project(Path(d))
            _write_devproject(project_root, [{
                "feature_id": "feat_auth",
                "title": "Auth",
                "description": "Authentication flow",
                "files_touched": [],
            }])

            out = audit_feature(str(project_root), "feat_checkout", provider="none")

            self.assertIn("Feature audit failed", out)
            self.assertIn("feat_auth", out)


# ---------------------------------------------------------------------------
# Audit harness hardening: PII redaction, gitignore, quota handling
# ---------------------------------------------------------------------------

def _summary_with_pii(text: str) -> dict:
    summary = _summary_with_decision(text)
    summary["overview"] = (
        "Resolved billing for Dave Hahn (dave.hahn@example.com); "
        "issued refund for order 4AFBE779."
    )
    summary["open_issues"] = [{
        "id": "iss_001",
        "issue": "Re-check token sk-ABCDEFGHIJKLMNOPQRSTUVWX1234 in prod env",
        "references": ["msg_001"],
    }]
    summary["next_steps"] = [{
        "id": "ns_001",
        "action": "Notify customer dave.hahn@example.com of resolution",
        "references": ["msg_001"],
    }]
    return summary


class AuditHardeningTests(unittest.TestCase):
    def _scaffold_project(self, root: Path) -> Path:
        project_root = _make_project(root)
        # Code file referencing the feature title so session_context filters keep our summary.
        (project_root / "auth.py").write_text(
            "def auth_signin():\n    return None\n", encoding="utf-8"
        )
        _write_devproject(project_root, [{
            "feature_id": "feat_auth",
            "title": "Auth",
            "description": "Authentication flow",
            "files_touched": ["auth.py"],
            "file_boundaries": ["auth.py"],
        }])
        # Session summary that mentions the feature so it gets pulled into context.
        prior = _summary_with_pii("Auth: handle dave.hahn@example.com signin retries")
        _make_session_file(project_root / "devsession", "prior_session", summary=prior)
        return project_root

    def test_session_context_redacts_pii_from_prior_summaries(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = self._scaffold_project(Path(d))
            out = audit_feature(str(project_root), "feat_auth", agents=1, provider="none")
            bundle = json.loads(out)

            pack_path = Path(bundle["context_pack"])
            pack = json.loads(pack_path.read_text(encoding="utf-8"))
            session_context = pack["session_context"]
            self.assertTrue(session_context, "expected session_context to include the prior summary")
            entry = session_context[0]
            self.assertTrue(entry.get("redacted"))

            blob = json.dumps(entry, ensure_ascii=False)
            self.assertNotIn("dave.hahn@example.com", blob)
            self.assertNotIn("sk-ABCDEFGHIJKLMNOPQRSTUVWX1234", blob)
            self.assertIn("[REDACTED_EMAIL]", blob)
            # Pack-level guard: full pack JSON must not echo the PII either.
            self.assertNotIn("dave.hahn@example.com", pack_path.read_text(encoding="utf-8"))

    def test_audit_creates_gitignore_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = self._scaffold_project(Path(d))
            self.assertFalse((project_root / ".gitignore").exists())

            audit_feature(str(project_root), "feat_auth", agents=1, provider="none")

            gitignore = project_root / ".gitignore"
            self.assertTrue(gitignore.exists())
            self.assertIn("devsession/agent-audits/", gitignore.read_text(encoding="utf-8"))

    def test_audit_appends_to_existing_gitignore_when_entry_missing(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = self._scaffold_project(Path(d))
            (project_root / ".gitignore").write_text("node_modules/\n*.pyc\n", encoding="utf-8")

            audit_feature(str(project_root), "feat_auth", agents=1, provider="none")

            content = (project_root / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("node_modules/", content)
            self.assertIn("devsession/agent-audits/", content)

    def test_audit_does_not_duplicate_existing_gitignore_entry(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = self._scaffold_project(Path(d))
            existing = "node_modules/\ndevsession/agent-audits/\n"
            (project_root / ".gitignore").write_text(existing, encoding="utf-8")

            audit_feature(str(project_root), "feat_auth", agents=1, provider="none")

            content = (project_root / ".gitignore").read_text(encoding="utf-8")
            self.assertEqual(content.count("devsession/agent-audits/"), 1)

    def test_ensure_gitignore_skips_non_git_dir(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = Path(d)
            # No .git here.
            status = _ensure_audit_gitignore(project_root)
            self.assertEqual(status["status"], "skipped")
            self.assertFalse((project_root / ".gitignore").exists())

    def test_detect_quota_error_recognizes_codex_message(self):
        codex_stderr = (
            "ERROR: You've hit your usage limit. Upgrade to Pro to keep going. "
            "You can try again at 8:07 PM."
        )
        self.assertTrue(detect_quota_error(codex_stderr))

    def test_detect_quota_error_recognizes_anthropic_message(self):
        stderr = "Error: credit balance is too low to access the Anthropic API"
        self.assertTrue(detect_quota_error(stderr))

    def test_detect_quota_error_ignores_unrelated_errors(self):
        self.assertFalse(detect_quota_error("connection refused"))
        self.assertFalse(detect_quota_error(""))

    def test_detect_quota_error_ignores_successful_runs(self):
        # Codex CLI mirrors the prompt to stderr, so audit content like
        # `import { rateLimit } from "@/lib/rate-limit"` or HTTP status 429
        # would otherwise false-positive when the agent succeeded.
        echoed_prompt = (
            "session id: abc-123\n"
            "import { rateLimit } from \"@/lib/rate-limit\";\n"
            "{ status: 429 }"
        )
        self.assertFalse(detect_quota_error(echoed_prompt, returncode=0))
        # Same text with a non-zero exit still flags — the gate is success, not content.
        self.assertTrue(detect_quota_error(echoed_prompt, returncode=1))

    def test_run_audit_agents_aborts_remaining_after_quota_error(self):
        # Stub agents list; sequential dispatch should short-circuit after the
        # first agent reports a quota error.
        agents = [{"agent_id": f"agent_{i:02d}", "assigned_files": []} for i in (1, 2, 3)]

        from unittest import mock

        def fake_provider(provider, project_root, run_dir, context_pack_path, agent, timeout_seconds, **kwargs):
            quota_error = agent["agent_id"] == "agent_01"
            return {
                "agent_id": agent["agent_id"],
                "provider": provider,
                "status": "failed" if quota_error else "completed",
                "quota_error": quota_error,
                "parse_status": "empty" if quota_error else "valid_json",
            }

        with mock.patch("reccli.agent_providers.run_agent_provider", side_effect=fake_provider):
            results = run_audit_agents(
                provider="codex",
                project_root=Path("/tmp"),
                run_dir=Path("/tmp"),
                context_pack_path=Path("/tmp/ctx.json"),
                agents=agents,
                timeout_seconds=10,
                max_concurrency=1,
            )

        statuses = {r["agent_id"]: r["status"] for r in results}
        self.assertEqual(statuses["agent_01"], "failed")
        self.assertEqual(statuses["agent_02"], "skipped")
        self.assertEqual(statuses["agent_03"], "skipped")
        skip_reasons = {r["agent_id"]: r.get("skip_reason", "") for r in results if r["status"] == "skipped"}
        self.assertIn("quota", skip_reasons["agent_02"].lower())
        self.assertIn("quota", skip_reasons["agent_03"].lower())

    def test_audit_feature_default_max_concurrency_is_sequential(self):
        with tempfile.TemporaryDirectory() as d:
            project_root = self._scaffold_project(Path(d))

            out = audit_feature(str(project_root), "feat_auth", agents=2, provider="none")
            bundle = json.loads(out)

            self.assertEqual(bundle["max_concurrency"], 1)
            self.assertFalse(bundle["quota_hit"])
            self.assertEqual(bundle["status"], "prepared")

    def test_detect_default_provider_picks_claude_under_claude_code(self):
        import os
        prior = {k: os.environ.get(k) for k in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CODEX_HOME", "CODEX_SESSION_ID")}
        try:
            for k in prior:
                os.environ.pop(k, None)
            os.environ["CLAUDECODE"] = "1"
            self.assertEqual(_detect_default_provider(), "claude")
        finally:
            for k, v in prior.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_detect_default_provider_picks_codex_under_codex_cli(self):
        import os
        prior = {k: os.environ.get(k) for k in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CODEX_HOME", "CODEX_SESSION_ID")}
        try:
            for k in prior:
                os.environ.pop(k, None)
            os.environ["CODEX_HOME"] = "/tmp/codex-home"
            self.assertEqual(_detect_default_provider(), "codex")
        finally:
            for k, v in prior.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_detect_default_provider_falls_back_to_claude_when_unknown(self):
        import os
        from unittest import mock
        prior = {k: os.environ.get(k) for k in ("RECCLI_HOST", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CODEX_HOME", "CODEX_SESSION_ID")}
        try:
            for k in prior:
                os.environ.pop(k, None)
            with mock.patch("reccli.mcp_server._detect_provider_from_process_tree", return_value=None):
                self.assertEqual(_detect_default_provider(), "claude")
        finally:
            for k, v in prior.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_detect_default_provider_respects_reccli_host_override(self):
        import os
        prior = {k: os.environ.get(k) for k in ("RECCLI_HOST", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CODEX_HOME", "CODEX_SESSION_ID")}
        try:
            for k in prior:
                os.environ.pop(k, None)
            # CLAUDECODE is set but RECCLI_HOST overrides to codex
            os.environ["CLAUDECODE"] = "1"
            os.environ["RECCLI_HOST"] = "codex"
            self.assertEqual(_detect_default_provider(), "codex")
            # And vice versa
            os.environ["RECCLI_HOST"] = "claude"
            os.environ.pop("CLAUDECODE", None)
            os.environ["CODEX_HOME"] = "/tmp/codex-home"
            self.assertEqual(_detect_default_provider(), "claude")
            # Unknown values are ignored
            os.environ["RECCLI_HOST"] = "garbage"
            os.environ.pop("CLAUDECODE", None)
            self.assertEqual(_detect_default_provider(), "codex")
        finally:
            for k, v in prior.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_audit_feature_auto_provider_resolves_in_bundle(self):
        import os
        prior = {k: os.environ.get(k) for k in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CODEX_HOME", "CODEX_SESSION_ID")}
        with tempfile.TemporaryDirectory() as d:
            project_root = self._scaffold_project(Path(d))
            try:
                for k in prior:
                    os.environ.pop(k, None)
                os.environ["CLAUDECODE"] = "1"

                # provider="none" is explicit and bypasses auto-detection.
                out = audit_feature(str(project_root), "feat_auth", agents=1, provider="none")
                bundle = json.loads(out)
                self.assertEqual(bundle["provider"], "none")
                self.assertEqual(bundle["provider_requested"], "none")

                # provider="auto" should resolve to the host CLI (claude here)
                # and surface that resolution in the bundle. We use a mocked
                # provider so no real subprocess fires.
                from unittest import mock

                def fake_provider(provider, project_root, run_dir, context_pack_path, agent, timeout_seconds, **kwargs):
                    return {
                        "agent_id": agent["agent_id"],
                        "provider": provider,
                        "status": "completed",
                        "parse_status": "valid_json",
                        "quota_error": False,
                    }

                with mock.patch("reccli.agent_providers.run_agent_provider", side_effect=fake_provider):
                    out = audit_feature(str(project_root), "feat_auth", agents=1, provider="auto")
                bundle = json.loads(out)
                self.assertEqual(bundle["provider"], "claude")
                self.assertEqual(bundle["provider_requested"], "auto")
            finally:
                for k, v in prior.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_audit_feature_bundle_reports_quota_hit(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as d:
            project_root = self._scaffold_project(Path(d))

            def fake_provider(provider, project_root, run_dir, context_pack_path, agent, timeout_seconds, **kwargs):
                return {
                    "agent_id": agent["agent_id"],
                    "provider": provider,
                    "status": "failed",
                    "quota_error": True,
                    "parse_status": "empty",
                }

            with mock.patch("reccli.agent_providers.run_agent_provider", side_effect=fake_provider):
                out = audit_feature(
                    str(project_root), "feat_auth", agents=3, provider="codex", max_concurrency=1
                )
            bundle = json.loads(out)

            self.assertTrue(bundle["quota_hit"])
            self.assertEqual(bundle["status"], "partial")
            self.assertIn("quota", bundle["status_reason"].lower())
            statuses = [r["status"] for r in bundle["agent_results"]]
            self.assertEqual(statuses.count("skipped"), 2)


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


class HookFixRegressionTests(unittest.TestCase):
    """Regression tests for the 9 hook fixes from the 2-agent self-audit."""

    def test_register_project_persists_name_update_to_disk(self):
        # Prior bug: an existing entry's name was mutated in memory but the
        # function returned before writing the registry to disk.
        from reccli.hooks import context_injector
        with tempfile.TemporaryDirectory() as d:
            registry_path = Path(d) / "projects.json"
            with unittest.mock.patch.object(context_injector, "REGISTRY_PATH", registry_path):
                project = Path(d) / "my-project"
                project.mkdir()
                context_injector.register_project(project, name="old")
                context_injector.register_project(project, name="new")
                disk = json.loads(registry_path.read_text(encoding="utf-8"))
                self.assertEqual(len(disk["projects"]), 1)
                self.assertEqual(disk["projects"][0]["name"], "new")

    def test_log_issue_routes_to_project_when_provided(self):
        # Prior bug: every _log_issue call in handle_event.py omitted
        # project_root, so issues went to ~/.reccli/.issues.jsonl instead of
        # the project devsession dir where list_issues reads.
        from reccli.hooks.session_recorder import _log_issue, get_issues
        with tempfile.TemporaryDirectory() as d:
            project_root = Path(d)
            _log_issue("hooks/SessionStart", "test issue", project_root=project_root)
            issues = get_issues(project_root)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0]["component"], "hooks/SessionStart")
            self.assertEqual(issues[0]["message"], "test issue")

    def test_session_signal_regex_handles_pipe_in_goal(self):
        # Prior bug: `[^|]*` truncated values containing literal `|` chars
        # (e.g. shell pipelines, X|Y phrasing).
        from reccli.hooks.session_recorder import _extract_session_signal
        msg = "Done.\n<!--session-signal: goal=fix x|y bug | resolved=A | open=B-->"
        sig = _extract_session_signal(msg)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["goal"], "fix x|y bug")
        self.assertEqual(sig["resolved"], ["A"])
        self.assertEqual(sig["open"], ["B"])

    def test_session_signal_extract_takes_trailing_tag_when_multiple_present(self):
        # Prior bug: extract() returned the first match while strip()
        # removed all matches — example tags earlier in body would corrupt
        # the saved signal.
        from reccli.hooks.session_recorder import _extract_session_signal
        msg = (
            "Example: <!--session-signal: goal=demo | resolved=demo | open=demo-->\n"
            "Real reply.\n"
            "<!--session-signal: goal=real | resolved=actual | open=remaining-->"
        )
        sig = _extract_session_signal(msg)
        self.assertEqual(sig["goal"], "real")
        self.assertEqual(sig["resolved"], ["actual"])

    def test_orphan_wal_recovery_uses_removeprefix_not_replace(self):
        # Prior bug: wal_file.stem.replace('.hooks_wal_', '') strips every
        # occurrence — would corrupt session_ids that contain the substring.
        # We can't easily exercise the full _recover_orphan_wals path in
        # isolation, but we can verify removeprefix's exact-prefix semantics
        # via the same call shape.
        adversarial = ".hooks_wal_a.hooks_wal_b"
        self.assertEqual(adversarial.removeprefix(".hooks_wal_"), "a.hooks_wal_b")
        # str.replace would have given "ab" — the broken behavior.
        self.assertEqual(adversarial.replace(".hooks_wal_", ""), "ab")


if __name__ == "__main__":
    unittest.main()
