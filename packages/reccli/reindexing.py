"""
Reindexing - Update message_range after compaction/transforms

When messages are removed (compaction, redaction, edits), array indices shift
but message_range structures become invalid. This module provides reindexing
to update all indices while preserving stable ID anchors.

Example:
    Before compaction (100 messages):
        message_range: {start: "msg_042", end: "msg_050", start_index: 41, end_index: 50}

    After compaction (removed messages 1-30, now 70 messages):
        msg_042 is now at index 11 (was 41)
        msg_050 is now at index 19 (was 49)

    Reindexed:
        message_range: {start: "msg_042", end: "msg_050", start_index: 11, end_index: 20}
"""

from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path


class ReindexingError(Exception):
    """Raised when reindexing fails"""
    pass


def build_id_to_index_mapping(conversation: List[Dict]) -> Dict[str, int]:
    """
    Build mapping from message IDs to current array indices

    Args:
        conversation: Current conversation (after compaction/transform)

    Returns:
        {msg_id: array_index} mapping

    Example:
        conversation = [{...}, {...}, ...]  # 70 messages
        mapping = {
            "msg_031": 0,   # msg_031 is now at index 0 (was 30)
            "msg_032": 1,   # msg_032 is now at index 1 (was 31)
            ...
            "msg_042": 11,  # msg_042 is now at index 11 (was 41)
            ...
        }
    """
    mapping = {}

    for idx, msg in enumerate(conversation):
        # Try to get message ID from message metadata
        msg_id = msg.get("_message_id")

        # If not present, reconstruct from original position
        # This assumes messages still have their original IDs
        # (which they should - we only remove messages, not renumber them)
        if not msg_id:
            # Messages might not have _message_id yet
            # We'll need to identify them by content or timestamp
            # For now, we'll need the ID to be present
            continue

        mapping[msg_id] = idx

    return mapping


def extract_message_id_from_conversation(conversation: List[Dict], index: int) -> Optional[str]:
    """
    Extract message ID at given index

    Tries multiple methods to identify the message:
    1. Check _message_id field
    2. Check metadata
    3. Reconstruct from position if messages are numbered

    Args:
        conversation: Conversation list
        index: Array index

    Returns:
        Message ID or None
    """
    if index < 0 or index >= len(conversation):
        return None

    msg = conversation[index]

    # Method 1: Check _message_id field
    if "_message_id" in msg:
        return msg["_message_id"]

    # Method 2: Check if we can reconstruct
    # This requires knowing the original message number
    # For now, return None - caller should ensure messages have IDs
    return None


def reindex_message_range(
    message_range: Dict[str, Any],
    id_to_index: Dict[str, int]
) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Reindex a single message_range after compaction

    Args:
        message_range: Original message_range with old indices
        id_to_index: Mapping from message IDs to new indices

    Returns:
        (reindexed_message_range, error_message)

    Raises:
        ReindexingError: If message IDs no longer exist (messages were deleted)
    """
    start_id = message_range.get("start")
    end_id = message_range.get("end")

    if not start_id or not end_id:
        return message_range, "Missing start or end ID"

    # Look up new indices
    if start_id not in id_to_index:
        return message_range, f"Start message {start_id} no longer exists (was deleted during compaction)"

    if end_id not in id_to_index:
        return message_range, f"End message {end_id} no longer exists (was deleted during compaction)"

    new_start_index = id_to_index[start_id]
    new_end_index_exclusive = id_to_index[end_id] + 1  # Convert to exclusive end

    # Create reindexed range
    reindexed = message_range.copy()
    reindexed["start_index"] = new_start_index
    reindexed["end_index"] = new_end_index_exclusive

    # Validate new range
    if new_end_index_exclusive < new_start_index:
        return message_range, f"Invalid reindexed range: [{new_start_index}, {new_end_index_exclusive})"

    return reindexed, None


def reindex_summary_item(
    item: Dict[str, Any],
    id_to_index: Dict[str, int]
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Reindex all message_range structures in a summary item

    Args:
        item: Summary item (decision, code_change, etc.)
        id_to_index: Mapping from message IDs to new indices

    Returns:
        (reindexed_item, warnings)
    """
    reindexed = item.copy()
    warnings = []

    # Reindex main message_range
    if "message_range" in item:
        reindexed_range, error = reindex_message_range(item["message_range"], id_to_index)

        if error:
            warnings.append(f"Item {item.get('id', 'unknown')}: {error}")
            # Keep old range but mark as invalid
            reindexed["_reindex_failed"] = True
            reindexed["_reindex_error"] = error
        else:
            reindexed["message_range"] = reindexed_range

    # TODO: If we implement multi-span support (Safeguard #3), reindex message_ranges array here

    return reindexed, warnings


def reindex_summary_after_compaction(
    summary: Dict[str, Any],
    conversation: List[Dict]
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Reindex all message_range structures in summary after compaction

    This is the main entry point for reindexing. Call this after:
    - Compaction (removing old messages)
    - Redaction (removing sensitive messages)
    - Any transform that changes conversation array

    Args:
        summary: Summary dict to reindex
        conversation: New conversation (after compaction/transform)

    Returns:
        (reindexed_summary, warnings)

    Example:
        # After compaction
        reindexed_summary, warnings = reindex_summary_after_compaction(
            old_summary,
            compacted_conversation
        )

        if warnings:
            print("Reindexing warnings:")
            for warning in warnings:
                print(f"  - {warning}")

        session.summary = reindexed_summary
        session.save()
    """
    # Build ID to index mapping
    id_to_index = build_id_to_index_mapping(conversation)

    if not id_to_index:
        # No message IDs found - need to tag messages first
        return summary, ["No message IDs found in conversation - cannot reindex"]

    # Reindex all categories
    reindexed = summary.copy()
    all_warnings = []

    categories = ["decisions", "code_changes", "problems_solved", "open_issues", "next_steps"]

    for category in categories:
        if category not in summary:
            continue

        reindexed_items = []
        for item in summary[category]:
            reindexed_item, warnings = reindex_summary_item(item, id_to_index)
            reindexed_items.append(reindexed_item)
            all_warnings.extend(warnings)

        reindexed[category] = reindexed_items

    return reindexed, all_warnings


def tag_messages_with_ids(conversation: List[Dict]) -> None:
    """
    Tag all messages with _message_id field for reindexing

    Call this during session initialization or before compaction
    to ensure messages can be tracked through transforms.

    Args:
        conversation: Conversation to tag (modified in place)

    Example:
        session = DevSession.load(path)
        tag_messages_with_ids(session.conversation)
        session.save()
    """
    for idx, msg in enumerate(conversation):
        if "_message_id" not in msg:
            msg_num = idx + 1  # 1-based
            msg["_message_id"] = f"msg_{msg_num:03d}"


def validate_reindexing(
    summary: Dict[str, Any],
    conversation: List[Dict]
) -> Tuple[bool, List[str]]:
    """
    Validate that reindexing was successful

    Checks that all message_range structures point to correct messages

    Args:
        summary: Reindexed summary
        conversation: Current conversation

    Returns:
        (is_valid, errors)
    """
    from .summarization.summary_verification import SummaryVerifier

    from .summarization.summary_schema import ensure_summary_span_links

    spans = ensure_summary_span_links(summary)
    verifier = SummaryVerifier(conversation, spans)
    is_valid, errors_by_category = verifier.verify_summary(summary)

    # Flatten errors
    all_errors = []
    for category, category_errors in errors_by_category.items():
        all_errors.extend(category_errors)

    return is_valid, all_errors


def auto_remove_invalid_items(
    summary: Dict[str, Any],
    conversation: List[Dict]
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Remove summary items that reference deleted messages

    After compaction, some messages may be deleted. This removes
    summary items that reference those messages.

    Args:
        summary: Summary with potentially invalid items
        conversation: Current conversation

    Returns:
        (cleaned_summary, removed_items)
    """
    from .summarization.summary_verification import SummaryVerifier

    from .summarization.summary_schema import ensure_summary_span_links

    spans = ensure_summary_span_links(summary)
    verifier = SummaryVerifier(conversation, spans)
    cleaned_summary = summary.copy()
    removed = []

    categories = ["decisions", "code_changes", "problems_solved", "open_issues", "next_steps"]

    for category in categories:
        if category not in summary:
            continue

        valid_items = []
        for item in summary[category]:
            # Check if message_range is valid
            if "message_range" in item:
                is_valid, error = verifier.verify_message_range(item["message_range"])

                if not is_valid:
                    removed.append(f"{category}/{item.get('id', 'unknown')}: {error}")
                    continue

            valid_items.append(item)

        cleaned_summary[category] = valid_items

    return cleaned_summary, removed


def create_reindexing_report(
    old_summary: Dict[str, Any],
    new_summary: Dict[str, Any],
    warnings: List[str],
    removed_items: List[str]
) -> str:
    """
    Create human-readable reindexing report

    Args:
        old_summary: Summary before reindexing
        new_summary: Summary after reindexing
        warnings: Warnings from reindexing
        removed_items: Items that were removed

    Returns:
        Report string
    """
    lines = ["# Reindexing Report", ""]

    # Count items before/after
    categories = ["decisions", "code_changes", "problems_solved", "open_issues", "next_steps"]

    for category in categories:
        old_count = len(old_summary.get(category, []))
        new_count = len(new_summary.get(category, []))

        if old_count != new_count:
            lines.append(f"- {category}: {old_count} → {new_count} ({new_count - old_count:+d})")

    if warnings:
        lines.append("")
        lines.append("## Warnings:")
        for warning in warnings:
            lines.append(f"  - {warning}")

    if removed_items:
        lines.append("")
        lines.append("## Removed Items:")
        for item in removed_items:
            lines.append(f"  - {item}")

    if not warnings and not removed_items:
        lines.append("")
        lines.append("✅ No issues - all items reindexed successfully")

    return "\n".join(lines)
