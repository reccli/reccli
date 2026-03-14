"""
Summary Schema - Enhanced JSON schema for session summaries
Includes determinism, provenance, verification, and audit trails
"""

import hashlib
import json
from datetime import datetime
from typing import Dict, List, Optional, Any


# Schema version for backwards compatibility
SUMMARY_SCHEMA_VERSION = "1.1"
SUMMARY_CATEGORIES = ["decisions", "code_changes", "problems_solved", "open_issues", "next_steps"]
SPAN_KIND_BY_CATEGORY = {
    "decisions": "decision_discussion",
    "code_changes": "code_change_discussion",
    "problems_solved": "problem_solving",
    "open_issues": "open_issue_discussion",
    "next_steps": "next_step_planning",
}
SUMMARY_TEXT_FIELDS = {
    "decisions": "decision",
    "code_changes": "description",
    "problems_solved": "problem",
    "open_issues": "issue",
    "next_steps": "action",
}


def _normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def generate_item_id(
    references: List[str],
    text: str,
    span_ids: Optional[List[str]] = None,
) -> str:
    """
    Generate stable ID for summary item using linked provenance and text.

    Args:
        references: List of message IDs referenced
        text: Item text content
        span_ids: Optional list of source span IDs

    Returns:
        Stable ID like "dec_7a1e..." or "chg_b2f0..."
    """
    anchors = sorted(span_ids or []) + sorted(references)
    content = "".join(anchors) + _normalize_text(text)
    # Use blake2b (stdlib) since blake3 requires external dependency
    hash_bytes = hashlib.blake2b(content.encode('utf-8'), digest_size=16).digest()
    return hash_bytes.hex()[:8]


def generate_span_id(
    kind: str,
    start_message_id: str,
    end_message_id: str,
    topic: str = "",
) -> str:
    """Generate a stable semantic span ID."""
    content = "|".join([kind, start_message_id, end_message_id, _normalize_text(topic)])
    hash_bytes = hashlib.blake2b(content.encode("utf-8"), digest_size=16).digest()
    return "spn_" + hash_bytes.hex()[:8]


def create_summary_skeleton(
    model: str = "claude-sonnet-4.5",
    model_version: str = "2025-10-01",
    session_hash: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create empty summary structure with metadata

    Args:
        model: Model name used for summarization
        model_version: Model version identifier
        session_hash: Hash of session for provenance

    Returns:
        Summary skeleton with metadata
    """
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "model": model,
        "model_version": model_version,
        "created_at": datetime.now().isoformat(),
        "session_hash": session_hash,

        "overview": "",
        "decisions": [],
        "code_changes": [],
        "problems_solved": [],
        "open_issues": [],
        "next_steps": [],

        # Causal edges (optional - for future graph-based retrieval)
        "causal_edges": [],

        # Audit trail for pins, locks, edits
        "audit_trail": []
    }


def create_span(
    kind: str,
    start_message_id: str,
    start_index: int,
    end_message_id: Optional[str] = None,
    end_index: Optional[int] = None,
    topic: str = "",
    references: Optional[List[str]] = None,
    t_first: Optional[str] = None,
    t_last: Optional[str] = None,
    episode_id: Optional[str] = None,
    parent_span_ids: Optional[List[str]] = None,
    status: str = "closed",
    latest_message_id: Optional[str] = None,
    latest_index: Optional[int] = None,
) -> Dict[str, Any]:
    """Create a first-class semantic span over the full conversation."""
    resolved_end_message_id = end_message_id
    resolved_end_index = end_index
    resolved_latest_message_id = latest_message_id
    resolved_latest_index = latest_index

    if status == "closed":
        resolved_end_message_id = resolved_end_message_id or start_message_id
        resolved_end_index = resolved_end_index if resolved_end_index is not None else start_index + 1
    else:
        resolved_latest_message_id = resolved_latest_message_id or resolved_end_message_id or start_message_id
        resolved_latest_index = resolved_latest_index if resolved_latest_index is not None else resolved_end_index

    span_anchor_end = resolved_end_message_id or resolved_latest_message_id or start_message_id

    return {
        "id": generate_span_id(kind, start_message_id, span_anchor_end, topic),
        "kind": kind,
        "topic": topic,
        "status": status,
        "start_message_id": start_message_id,
        "end_message_id": resolved_end_message_id,
        "start_index": start_index,
        "end_index": resolved_end_index,
        "latest_message_id": resolved_latest_message_id,
        "latest_index": resolved_latest_index,
        "t_first": t_first,
        "t_last": t_last,
        "references": references or [],
        "episode_id": episode_id,
        "parent_span_ids": parent_span_ids or [],
    }


def sort_spans(spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort spans deterministically while keeping open spans near their current position."""
    def _sort_key(span: Dict[str, Any]):
        status = span.get("status", "closed")
        end_index = span.get("end_index")
        latest_index = span.get("latest_index")
        resolved_end = end_index if isinstance(end_index, int) else latest_index
        return (
            span.get("start_index", 0),
            resolved_end if isinstance(resolved_end, int) else 10**9,
            1 if status == "open" else 0,
            span.get("id", ""),
        )

    return sorted(spans, key=_sort_key)


def _iter_summary_items(summary: Dict[str, Any]):
    for category in SUMMARY_CATEGORIES:
        for item in summary.get(category, []):
            yield category, item


def _summary_item_topic(category: str, item: Dict[str, Any]) -> str:
    field = SUMMARY_TEXT_FIELDS.get(category)
    return item.get(field) or item.get("id") or category


def ensure_summary_span_links(
    summary: Dict[str, Any],
    spans: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Ensure summary items have semantic span IDs and return the normalized span list.

    This is the single shared place that synthesizes span links from summary items,
    so writers, validators, and retrievers do not each invent their own span logic.
    """
    span_lookup: Dict[str, Dict[str, Any]] = {}
    preserved_open_spans: List[Dict[str, Any]] = []
    for span in spans or []:
        span_id = span.get("id")
        if span_id:
            span_lookup[span_id] = dict(span)
            if span.get("status") == "open":
                preserved_open_spans.append(dict(span))

    for category, item in _iter_summary_items(summary):
        existing_span_ids = [
            span_id for span_id in item.get("span_ids", [])
            if isinstance(span_id, str) and span_id in span_lookup
        ]

        msg_range = item.get("message_range")
        if not msg_range:
            item["span_ids"] = existing_span_ids
            continue

        start_id = msg_range.get("start")
        end_id = msg_range.get("end")
        start_index = msg_range.get("start_index")
        end_index = msg_range.get("end_index")
        if not all(isinstance(value, str) for value in [start_id, end_id]):
            item["span_ids"] = existing_span_ids
            continue
        if not all(isinstance(value, int) for value in [start_index, end_index]):
            item["span_ids"] = existing_span_ids
            continue

        kind = SPAN_KIND_BY_CATEGORY.get(category, "discussion")
        topic = _summary_item_topic(category, item)
        synthesized_span = create_span(
            kind=kind,
            start_message_id=start_id,
            start_index=start_index,
            end_message_id=end_id,
            end_index=end_index,
            topic=topic,
            references=item.get("references"),
            t_first=item.get("t_first"),
            t_last=item.get("t_last"),
        )
        span_id = synthesized_span["id"]
        if span_id not in span_lookup:
            span_lookup[span_id] = synthesized_span

        if span_id not in existing_span_ids:
            existing_span_ids.append(span_id)
        item["span_ids"] = existing_span_ids

    for span in preserved_open_spans:
        span_lookup[span["id"]] = span

    return sort_spans(list(span_lookup.values()))


def create_decision_item(
    decision: str,
    reasoning: str,
    impact: str,
    references: List[str],
    message_range: Dict[str, Any],
    span_ids: Optional[List[str]] = None,
    alternatives_considered: Optional[List[str]] = None,
    confidence: str = "high",
    quote: Optional[str] = None,
    t_first: Optional[str] = None,
    t_last: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a decision summary item with full metadata

    Args:
        decision: Clear statement of what was decided
        reasoning: Why this approach was chosen
        impact: "low" | "medium" | "high"
        references: Key message IDs
        message_range: Full discussion span
        alternatives_considered: Other options discussed
        confidence: "low" | "medium" | "high"
        quote: Supporting quote from referenced message
        t_first: ISO timestamp of first message
        t_last: ISO timestamp of last message

    Returns:
        Decision item dict
    """
    item_id = "dec_" + generate_item_id(references, decision, span_ids=span_ids)

    return {
        "id": item_id,
        "decision": decision,
        "reasoning": reasoning,
        "impact": impact,
        "alternatives_considered": alternatives_considered or [],
        "span_ids": span_ids or [],
        "references": references,
        "message_range": message_range,
        "t_first": t_first,
        "t_last": t_last,
        "confidence": confidence,
        "quote": quote,
        "pinned": False,
        "locked": False
    }


def create_code_change_item(
    files: List[str],
    description: str,
    change_type: str,
    references: List[str],
    message_range: Dict[str, Any],
    span_ids: Optional[List[str]] = None,
    lines_added: Optional[int] = None,
    lines_removed: Optional[int] = None,
    source_of_truth: str = "llm_inferred",
    confidence: str = "medium",
    t_first: Optional[str] = None,
    t_last: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a code change summary item

    Args:
        files: List of file paths changed
        description: What was changed and why
        change_type: "feature" | "bugfix" | "refactor" | "test" | "docs"
        references: Key message IDs
        message_range: Full discussion span
        lines_added: Lines added (from git/events or None)
        lines_removed: Lines removed (from git/events or None)
        source_of_truth: "git" | "file_events" | "llm_inferred"
        confidence: "low" | "medium" | "high"
        t_first: ISO timestamp of first message
        t_last: ISO timestamp of last message

    Returns:
        Code change item dict
    """
    item_id = "chg_" + generate_item_id(references, description, span_ids=span_ids)

    return {
        "id": item_id,
        "files": files,
        "description": description,
        "type": change_type,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "source_of_truth": source_of_truth,
        "span_ids": span_ids or [],
        "references": references,
        "message_range": message_range,
        "t_first": t_first,
        "t_last": t_last,
        "confidence": confidence,
        "pinned": False,
        "locked": False
    }


def create_problem_solved_item(
    problem: str,
    solution: str,
    references: List[str],
    message_range: Dict[str, Any],
    span_ids: Optional[List[str]] = None,
    confidence: str = "high",
    t_first: Optional[str] = None,
    t_last: Optional[str] = None
) -> Dict[str, Any]:
    """Create a problem solved summary item"""
    item_id = "prb_" + generate_item_id(references, problem, span_ids=span_ids)

    return {
        "id": item_id,
        "problem": problem,
        "solution": solution,
        "span_ids": span_ids or [],
        "references": references,
        "message_range": message_range,
        "t_first": t_first,
        "t_last": t_last,
        "confidence": confidence,
        "pinned": False,
        "locked": False
    }


def create_open_issue_item(
    issue: str,
    severity: str,
    references: List[str],
    message_range: Dict[str, Any],
    span_ids: Optional[List[str]] = None,
    confidence: str = "medium",
    t_first: Optional[str] = None,
    t_last: Optional[str] = None
) -> Dict[str, Any]:
    """Create an open issue summary item"""
    item_id = "iss_" + generate_item_id(references, issue, span_ids=span_ids)

    return {
        "id": item_id,
        "issue": issue,
        "severity": severity,
        "span_ids": span_ids or [],
        "references": references,
        "message_range": message_range,
        "t_first": t_first,
        "t_last": t_last,
        "confidence": confidence,
        "pinned": False,
        "locked": False
    }


def create_next_step_item(
    action: str,
    priority: int,
    references: List[str],
    message_range: Dict[str, Any],
    span_ids: Optional[List[str]] = None,
    estimated_time: Optional[str] = None,
    confidence: str = "medium",
    t_first: Optional[str] = None,
    t_last: Optional[str] = None
) -> Dict[str, Any]:
    """Create a next step summary item"""
    item_id = "nxt_" + generate_item_id(references, action, span_ids=span_ids)

    return {
        "id": item_id,
        "action": action,
        "priority": priority,
        "estimated_time": estimated_time,
        "span_ids": span_ids or [],
        "references": references,
        "message_range": message_range,
        "t_first": t_first,
        "t_last": t_last,
        "confidence": confidence,
        "pinned": False,
        "locked": False
    }


def add_causal_edge(
    summary: Dict[str, Any],
    from_id: str,
    to_id: str,
    relation: str
) -> None:
    """
    Add causal relationship between summary items

    Args:
        summary: Summary dict to modify
        from_id: Source item ID
        to_id: Target item ID
        relation: "supports" | "blocks" | "derived_from" | "follows"
    """
    edge = {
        "from": from_id,
        "to": to_id,
        "rel": relation
    }

    if "causal_edges" not in summary:
        summary["causal_edges"] = []

    summary["causal_edges"].append(edge)


def add_audit_entry(
    summary: Dict[str, Any],
    action: str,
    target_id: str,
    actor: str = "user",
    metadata: Optional[Dict] = None
) -> None:
    """
    Add audit trail entry

    Args:
        summary: Summary dict to modify
        action: "pin" | "unpin" | "lock" | "unlock" | "edit" | "delete"
        target_id: ID of item being modified
        actor: "user" | "system"
        metadata: Optional additional data (e.g., previous value for edits)
    """
    entry = {
        "ts": datetime.now().isoformat(),
        "actor": actor,
        "action": action,
        "target": target_id
    }

    if metadata:
        entry["metadata"] = metadata

    if "audit_trail" not in summary:
        summary["audit_trail"] = []

    summary["audit_trail"].append(entry)


def validate_summary_schema(summary: Dict[str, Any]) -> List[str]:
    """
    Validate summary against schema

    Args:
        summary: Summary dict to validate

    Returns:
        List of validation errors (empty if valid)
    """
    errors = []

    # Check required top-level fields
    required_fields = ["schema_version", "model", "created_at", "overview"]
    for field in required_fields:
        if field not in summary:
            errors.append(f"Missing required field: {field}")

    # Check required arrays
    required_arrays = SUMMARY_CATEGORIES
    for field in required_arrays:
        if field not in summary:
            errors.append(f"Missing required array: {field}")
        elif not isinstance(summary[field], list):
            errors.append(f"{field} must be an array")

    # Validate decision items
    for i, decision in enumerate(summary.get("decisions", [])):
        if "id" not in decision:
            errors.append(f"Decision {i} missing 'id'")
        if "decision" not in decision or not decision["decision"]:
            errors.append(f"Decision {i} missing 'decision' text")
        if "impact" not in decision or decision["impact"] not in ["low", "medium", "high"]:
            errors.append(f"Decision {i} has invalid 'impact' (must be low/medium/high)")
        if "span_ids" not in decision or not isinstance(decision["span_ids"], list):
            errors.append(f"Decision {i} missing or invalid 'span_ids'")
        if "references" not in decision or not isinstance(decision["references"], list):
            errors.append(f"Decision {i} missing or invalid 'references'")
        if "message_range" not in decision:
            errors.append(f"Decision {i} missing 'message_range'")

    # Validate code change items
    for i, change in enumerate(summary.get("code_changes", [])):
        if "id" not in change:
            errors.append(f"Code change {i} missing 'id'")
        if "files" not in change or not isinstance(change["files"], list):
            errors.append(f"Code change {i} missing or invalid 'files'")
        if "type" not in change or change["type"] not in ["feature", "bugfix", "refactor", "test", "docs"]:
            errors.append(f"Code change {i} has invalid 'type'")
        if "span_ids" not in change or not isinstance(change["span_ids"], list):
            errors.append(f"Code change {i} missing or invalid 'span_ids'")

    for category in ["problems_solved", "open_issues", "next_steps"]:
        for i, item in enumerate(summary.get(category, [])):
            if "id" not in item:
                errors.append(f"{category} item {i} missing 'id'")
            if "span_ids" not in item or not isinstance(item["span_ids"], list):
                errors.append(f"{category} item {i} missing or invalid 'span_ids'")
            if "references" not in item or not isinstance(item["references"], list):
                errors.append(f"{category} item {i} missing or invalid 'references'")
            if "message_range" not in item:
                errors.append(f"{category} item {i} missing 'message_range'")

    return errors
