"""
Tests for auto-reason, MMC, session-signal, and expanded search query expansion.
"""

import unittest


class TestAutoReasonIntentDetection(unittest.TestCase):
    """Test intent detection heuristic."""

    def setUp(self):
        from reccli.hooks.auto_reason import detect_intent, get_reasoning_scaffold, get_mmc_protocol
        self.detect_intent = detect_intent
        self.get_reasoning_scaffold = get_reasoning_scaffold
        self.get_mmc_protocol = get_mmc_protocol

    # --- Debug intent ---

    def test_debug_error_keywords(self):
        self.assertEqual(self.detect_intent("This function throws an error when I pass None"), "debug")

    def test_debug_not_working(self):
        self.assertEqual(self.detect_intent("The search is not working after the last change"), "debug")

    def test_debug_traceback(self):
        self.assertEqual(self.detect_intent("I'm getting a traceback in the summarizer"), "debug")

    def test_debug_why_pattern(self):
        self.assertEqual(self.detect_intent("Why is the test failing with a 500 error?"), "debug")

    def test_debug_regression(self):
        self.assertEqual(self.detect_intent("There's a regression in the BM25 scoring"), "debug")

    # --- Planning intent (merged design + refactor) ---

    def test_planning_architecture(self):
        self.assertEqual(self.detect_intent("How should we design the authentication system?"), "planning")

    def test_planning_trade_offs(self):
        self.assertEqual(self.detect_intent("What are the trade-offs between these approaches?"), "planning")

    def test_planning_should_we(self):
        self.assertEqual(self.detect_intent("Should we use WebSockets or SSE for real-time updates?"), "planning")

    def test_planning_refactor(self):
        self.assertEqual(self.detect_intent("Let's refactor the search module to be more modular"), "planning")

    def test_planning_migrate(self):
        self.assertEqual(self.detect_intent("We need to migrate from the old auth to the new system"), "planning")

    def test_planning_lets_figure_out(self):
        self.assertEqual(self.detect_intent("let's figure out how to implement caching"), "planning")

    def test_planning_lets_plan(self):
        self.assertEqual(self.detect_intent("let's plan the new retrieval system"), "planning")

    # --- No intent ---

    def test_no_intent_simple_task(self):
        self.assertIsNone(self.detect_intent("Add a button to the settings page"))

    def test_no_intent_question(self):
        self.assertIsNone(self.detect_intent("What files handle routing?"))

    def test_no_intent_greeting(self):
        self.assertIsNone(self.detect_intent("hey, let's work on reccli"))

    # --- Scaffold output ---

    def test_scaffold_returns_text_for_debug(self):
        scaffold = self.get_reasoning_scaffold("This bug crashes the app")
        self.assertIsNotNone(scaffold)
        self.assertIn("Debug Mode", scaffold)
        self.assertIn("5-7", scaffold)

    def test_scaffold_returns_text_for_planning(self):
        scaffold = self.get_reasoning_scaffold("How should we architect the caching layer?")
        self.assertIsNotNone(scaffold)
        self.assertIn("Planning Mode", scaffold)

    def test_scaffold_returns_none_for_no_intent(self):
        self.assertIsNone(self.get_reasoning_scaffold("Read the README"))

    def test_ambiguous_prefers_highest_score(self):
        # "debug the failing test" has debug keywords (debug, failing)
        result = self.detect_intent("debug the failing test")
        self.assertEqual(result, "debug")

    # --- MMC protocol ---

    def test_mmc_protocol_debug(self):
        protocol = self.get_mmc_protocol("This bug crashes the app with a traceback")
        self.assertIsNotNone(protocol)
        self.assertIn("MMC", protocol)
        self.assertIn("Debug Mode", protocol)
        self.assertIn("Agent 1", protocol)
        self.assertIn("Agent 2", protocol)
        self.assertIn("Agent 3", protocol)
        self.assertIn("RECENT CHANGES", protocol)
        self.assertIn("DATA FLOW", protocol)
        self.assertIn("ASSUMPTIONS", protocol)
        self.assertIn("convergent", protocol.lower())

    def test_mmc_protocol_planning(self):
        protocol = self.get_mmc_protocol("How should we design the auth system?")
        self.assertIsNotNone(protocol)
        self.assertIn("Planning Mode", protocol)
        self.assertIn("SIMPLICITY", protocol)
        self.assertIn("ROBUSTNESS", protocol)
        self.assertIn("PERFORMANCE", protocol)

    def test_mmc_protocol_none_for_no_intent(self):
        self.assertIsNone(self.get_mmc_protocol("Add a button"))

    def test_mmc_includes_scaffold_in_each_agent(self):
        protocol = self.get_mmc_protocol("Why is the search broken?")
        self.assertIsNotNone(protocol)
        # The scaffold text should appear for each agent
        self.assertIn("5-7 different possible sources", protocol)
        # Should instruct parallel execution
        self.assertIn("IN PARALLEL", protocol)

    def test_mmc_includes_consensus_extraction(self):
        protocol = self.get_mmc_protocol("Let's plan the new retrieval system")
        self.assertIsNotNone(protocol)
        self.assertIn("2+ agents converged", protocol)
        self.assertIn("confidence", protocol.lower())


class TestSessionSignalExtraction(unittest.TestCase):
    """Test session-signal parsing and stripping."""

    def setUp(self):
        from reccli.hooks.session_recorder import _extract_session_signal, _strip_session_signal
        self.extract = _extract_session_signal
        self.strip = _strip_session_signal

    def test_valid_signal(self):
        msg = "Here's the fix.\n<!--session-signal: resolved=BM25 range fix | open=index rebuild-->"
        signal = self.extract(msg)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["resolved"], ["BM25 range fix"])
        self.assertEqual(signal["open"], ["index rebuild"])

    def test_multiple_items(self):
        msg = "Done.\n<!--session-signal: resolved=auth design, schema fix | open=rate limiting, testing-->"
        signal = self.extract(msg)
        self.assertEqual(signal["resolved"], ["auth design", "schema fix"])
        self.assertEqual(signal["open"], ["rate limiting", "testing"])

    def test_no_signal(self):
        msg = "Just a normal response without any tags."
        self.assertIsNone(self.extract(msg))

    def test_malformed_signal(self):
        msg = "<!--session-signal: this is broken-->"
        self.assertIsNone(self.extract(msg))

    def test_strip_removes_tag(self):
        msg = "Here's the fix.\n<!--session-signal: resolved=done | open=nothing-->"
        stripped = self.strip(msg)
        self.assertEqual(stripped, "Here's the fix.")
        self.assertNotIn("session-signal", stripped)

    def test_strip_preserves_content_without_tag(self):
        msg = "No tag here."
        stripped = self.strip(msg)
        self.assertEqual(stripped, "No tag here.")

    def test_empty_resolved(self):
        msg = "<!--session-signal: resolved= | open=something-->"
        signal = self.extract(msg)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["resolved"], [])
        self.assertEqual(signal["open"], ["something"])

    def test_goal_field_present(self):
        msg = "Done.\n<!--session-signal: goal=fix auth bug | resolved=identified root cause | open=write patch, add test-->"
        signal = self.extract(msg)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["goal"], "fix auth bug")
        self.assertEqual(signal["resolved"], ["identified root cause"])
        self.assertEqual(signal["open"], ["write patch", "add test"])

    def test_goal_field_absent_backward_compat(self):
        msg = "<!--session-signal: resolved=A | open=B-->"
        signal = self.extract(msg)
        self.assertIsNotNone(signal)
        self.assertNotIn("goal", signal)
        self.assertEqual(signal["resolved"], ["A"])
        self.assertEqual(signal["open"], ["B"])

    def test_goal_with_angle_brackets(self):
        msg = "Hi.\n<!--session-signal: goal=<pending> | resolved=<loaded> | open=<waiting>-->"
        signal = self.extract(msg)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["goal"], "<pending>")
        self.assertEqual(signal["resolved"], ["<loaded>"])
        self.assertEqual(signal["open"], ["<waiting>"])

    def test_strip_with_goal(self):
        msg = "Result.\n<!--session-signal: goal=test | resolved=done | open=next-->"
        stripped = self.strip(msg)
        self.assertEqual(stripped, "Result.")
        self.assertNotIn("session-signal", stripped)

    def test_open_items_with_angle_brackets(self):
        """Regression: [^>]* broke when open items contained > characters."""
        msg = "<!--session-signal: goal=test signals | resolved=<context loaded> | open=<verify WAL>-->"
        signal = self.extract(msg)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["open"], ["<verify WAL>"])


class TestQueryExpansion(unittest.TestCase):
    """Test expanded search query expansion."""

    def setUp(self):
        from reccli.retrieval.query_expansion import expand_query
        self.expand_query = expand_query

    def test_basic_expansion(self):
        results = self.expand_query("auth middleware")
        self.assertGreater(len(results), 1)
        self.assertEqual(results[0], "auth middleware")

    def test_original_always_first(self):
        results = self.expand_query("database schema")
        self.assertEqual(results[0], "database schema")

    def test_no_expansion_for_unknown_terms(self):
        results = self.expand_query("hello world")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], "hello world")

    def test_max_variations_respected(self):
        results = self.expand_query("auth middleware error", max_variations=2)
        self.assertLessEqual(len(results), 3)  # original + max 2

    def test_no_duplicates(self):
        results = self.expand_query("test error")
        self.assertEqual(len(results), len(set(results)))

    def test_single_term_expansion(self):
        results = self.expand_query("refactor")
        self.assertGreater(len(results), 1)
        all_text = " ".join(results)
        self.assertTrue(
            "restructure" in all_text or "cleanup" in all_text or "reorganize" in all_text,
            f"Expected synonym in expansions: {results}"
        )

    def test_empty_query(self):
        results = self.expand_query("")
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
