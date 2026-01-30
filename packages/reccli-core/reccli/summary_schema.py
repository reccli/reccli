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


def generate_item_id(references: List[str], text: str) -> str:
    """
    Generate stable ID for summary item using blake3 hash

    Args:
        references: List of message IDs referenced
        text: Item text content

    Returns:
        Stable ID like "dec_7a1e..." or "chg_b2f0..."
    """
    # Concatenate references and text for deterministic hash
    content = "".join(sorted(references)) + text
    # Use blake2b (stdlib) since blake3 requires external dependency
    hash_bytes = hashlib.blake2b(content.encode('utf-8'), digest_size=16).digest()
    return hash_bytes.hex()[:8]


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


def create_decision_item(
    decision: str,
    reasoning: str,
    impact: str,
    references: List[str],
    message_range: Dict[str, Any],
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
    item_id = "dec_" + generate_item_id(references, decision)

    return {
        "id": item_id,
        "decision": decision,
        "reasoning": reasoning,
        "impact": impact,
        "alternatives_considered": alternatives_considered or [],
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
    item_id = "chg_" + generate_item_id(references, description)

    return {
        "id": item_id,
        "files": files,
        "description": description,
        "type": change_type,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "source_of_truth": source_of_truth,
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
    confidence: str = "high",
    t_first: Optional[str] = None,
    t_last: Optional[str] = None
) -> Dict[str, Any]:
    """Create a problem solved summary item"""
    item_id = "prb_" + generate_item_id(references, problem)

    return {
        "id": item_id,
        "problem": problem,
        "solution": solution,
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
    confidence: str = "medium",
    t_first: Optional[str] = None,
    t_last: Optional[str] = None
) -> Dict[str, Any]:
    """Create an open issue summary item"""
    item_id = "iss_" + generate_item_id(references, issue)

    return {
        "id": item_id,
        "issue": issue,
        "severity": severity,
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
    estimated_time: Optional[str] = None,
    confidence: str = "medium",
    t_first: Optional[str] = None,
    t_last: Optional[str] = None
) -> Dict[str, Any]:
    """Create a next step summary item"""
    item_id = "nxt_" + generate_item_id(references, action)

    return {
        "id": item_id,
        "action": action,
        "priority": priority,
        "estimated_time": estimated_time,
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
    required_arrays = ["decisions", "code_changes", "problems_solved", "open_issues", "next_steps"]
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

    return errors
