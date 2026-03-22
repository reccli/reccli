#!/usr/bin/env python3
"""
Manual provider-backed regression harness for constrained delta summarization.

This script is intentionally non-CI:
- it requires real provider credentials
- provider output is nondeterministic
- it is meant for periodic audit runs, not blocking automation
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reccli.config import Config
from reccli.summarizer import SessionSummarizer


def build_client(model: str):
    if model.startswith("claude"):
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("anthropic package not installed") from exc

        api_key = os.environ.get("ANTHROPIC_API_KEY") or Config().get_api_key("anthropic")
        if not api_key:
            raise RuntimeError("Anthropic API key not configured")
        return anthropic.Anthropic(api_key=api_key)

    if model.startswith("gpt"):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("openai package not installed") from exc

        api_key = os.environ.get("OPENAI_API_KEY") or Config().get_api_key("openai")
        if not api_key:
            raise RuntimeError("OpenAI API key not configured")
        return OpenAI(api_key=api_key)

    raise RuntimeError(f"Unsupported model '{model}'")


def evaluate_case(
    summarizer: SessionSummarizer,
    case: Dict[str, Any],
) -> Dict[str, Any]:
    conversation = case["conversation"]
    existing_summary = case["existing_summary"]
    existing_spans = case.get("existing_spans", [])
    start_index = case["start_index"]
    end_index = case.get("end_index", len(conversation))

    raw_ops = summarizer.generate_delta_operations(
        current_summary=existing_summary,
        conversation_slice=conversation[start_index:end_index],
        open_spans=[span for span in existing_spans if span.get("status") == "open"],
        base_index=start_index,
    )
    validated_ops, warnings = summarizer._validate_delta_operations(
        operations=raw_ops,
        current_summary=existing_summary,
        current_spans=existing_spans,
        conversation=conversation,
    )
    updated_state = summarizer.update_summary_state_incrementally(
        conversation=conversation,
        existing_summary=existing_summary,
        existing_spans=existing_spans,
        start_index=start_index,
        end_index=end_index,
        redact_secrets=False,
    )

    expected = case.get("expected", {})
    op_types = [op.get("op") for op in validated_ops]
    failures: List[str] = []
    allowed_types = set(expected.get("allowed_op_types", []))
    required_types = set(expected.get("required_op_types", []))

    if allowed_types:
        invalid_types = [op_type for op_type in op_types if op_type not in allowed_types]
        if invalid_types:
            failures.append(f"unexpected op types: {invalid_types}")

    missing_required = sorted(required_types - set(op_types))
    if missing_required:
        failures.append(f"missing required op types: {missing_required}")

    min_operation_count = expected.get("min_operation_count")
    if min_operation_count is not None and len(validated_ops) < min_operation_count:
        failures.append(f"expected at least {min_operation_count} validated ops, got {len(validated_ops)}")

    expected_decision_count = expected.get("expected_decision_count")
    if expected_decision_count is not None:
        actual_decision_count = len(updated_state["summary"].get("decisions", []))
        if actual_decision_count != expected_decision_count:
            failures.append(
                f"expected decisions count {expected_decision_count}, got {actual_decision_count}"
            )

    expected_open_issue_count = expected.get("expected_open_issue_count")
    if expected_open_issue_count is not None:
        actual_open_issue_count = len(updated_state["summary"].get("open_issues", []))
        if actual_open_issue_count != expected_open_issue_count:
            failures.append(
                f"expected open_issues count {expected_open_issue_count}, got {actual_open_issue_count}"
            )

    expected_next_step_count = expected.get("expected_next_step_count")
    if expected_next_step_count is not None:
        actual_next_step_count = len(updated_state["summary"].get("next_steps", []))
        if actual_next_step_count != expected_next_step_count:
            failures.append(
                f"expected next_steps count {expected_next_step_count}, got {actual_next_step_count}"
            )

    min_next_step_count = expected.get("min_next_step_count")
    if min_next_step_count is not None:
        actual_next_step_count = len(updated_state["summary"].get("next_steps", []))
        if actual_next_step_count < min_next_step_count:
            failures.append(
                f"expected at least {min_next_step_count} next_steps, got {actual_next_step_count}"
            )

    min_decision_count = expected.get("min_decision_count")
    if min_decision_count is not None:
        actual_decision_count = len(updated_state["summary"].get("decisions", []))
        if actual_decision_count < min_decision_count:
            failures.append(
                f"expected at least {min_decision_count} decisions, got {actual_decision_count}"
            )

    return {
        "name": case["name"],
        "raw_operations": raw_ops,
        "validated_operations": validated_ops,
        "validation_warnings": warnings,
        "applied_operations": updated_state["operations"],
        "decision_count_after": len(updated_state["summary"].get("decisions", [])),
        "open_issue_count_after": len(updated_state["summary"].get("open_issues", [])),
        "next_step_count_after": len(updated_state["summary"].get("next_steps", [])),
        "decision_ids_after": [item.get("id") for item in updated_state["summary"].get("decisions", [])],
        "open_issue_ids_after": [item.get("id") for item in updated_state["summary"].get("open_issues", [])],
        "next_step_ids_after": [item.get("id") for item in updated_state["summary"].get("next_steps", [])],
        "failures": failures,
        "passed": not failures,
    }


def main():
    parser = argparse.ArgumentParser(description="Run manual provider-backed delta-op regression cases.")
    parser.add_argument("--model", help="Model to use. Defaults to configured default model.")
    parser.add_argument(
        "--cases",
        default=str(Path(__file__).with_name("provider_delta_cases.json")),
        help="Path to JSON cases file.",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).with_name("provider_delta_results.json")),
        help="Path to write JSON results.",
    )
    args = parser.parse_args()

    config = Config()
    model = args.model or config.get_default_model()
    client = build_client(model)
    summarizer = SessionSummarizer(llm_client=client, model=model)

    with open(args.cases, "r", encoding="utf-8") as handle:
        cases = json.load(handle)

    results = {
        "model": model,
        "generated_at": datetime.now().isoformat(),
        "cases": [evaluate_case(summarizer, case) for case in cases],
    }

    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2)

    failed = [case["name"] for case in results["cases"] if not case["passed"]]
    print(f"Wrote provider delta results to {args.output}")
    if failed:
        print(f"Cases with expectation failures: {', '.join(failed)}")
    else:
        print("All cases satisfied their configured expectations.")


if __name__ == "__main__":
    main()
