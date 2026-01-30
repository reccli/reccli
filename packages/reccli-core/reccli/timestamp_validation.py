"""
Timestamp Validation - Ensure timestamps are monotonic and in UTC

Safeguard #6: Verify that conversation timestamps always increase
and are stored in a consistent timezone (UTC).
"""

from typing import List, Dict, Tuple, Optional
from datetime import datetime, timezone


def validate_monotonic_timestamps(conversation: List[Dict]) -> Tuple[bool, List[str]]:
    """
    Validate that timestamps are monotonic (always increasing)

    Args:
        conversation: Conversation with timestamp fields

    Returns:
        (is_valid, errors)

    Example:
        >>> conversation = [
        ...     {"role": "user", "content": "hello", "timestamp": 1.0},
        ...     {"role": "assistant", "content": "hi", "timestamp": 2.0},
        ...     {"role": "user", "content": "how are you", "timestamp": 1.5},  # ERROR!
        ... ]
        >>> validate_monotonic_timestamps(conversation)
        (False, ["Message 2: timestamp 1.5 < previous 2.0"])
    """
    errors = []
    last_timestamp = None

    for i, msg in enumerate(conversation):
        timestamp = msg.get("timestamp")

        if timestamp is None:
            errors.append(f"Message {i}: missing timestamp")
            continue

        # Check type
        if not isinstance(timestamp, (int, float, str)):
            errors.append(f"Message {i}: invalid timestamp type {type(timestamp)}")
            continue

        # Convert string timestamps to float if needed
        if isinstance(timestamp, str):
            try:
                timestamp = float(timestamp)
            except ValueError:
                # Try ISO format
                try:
                    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    timestamp = dt.timestamp()
                except ValueError:
                    errors.append(f"Message {i}: cannot parse timestamp {timestamp}")
                    continue

        # Check monotonicity
        if last_timestamp is not None and timestamp < last_timestamp:
            errors.append(
                f"Message {i}: timestamp {timestamp} < previous {last_timestamp} "
                f"(non-monotonic - messages out of order)"
            )

        last_timestamp = timestamp

    return len(errors) == 0, errors


def validate_timezone_utc(conversation: List[Dict]) -> Tuple[bool, List[str]]:
    """
    Validate that ISO timestamps are in UTC (end with 'Z')

    Args:
        conversation: Conversation with timestamp fields

    Returns:
        (is_valid, warnings)

    Note: This only checks ISO string timestamps. Unix timestamps
    are timezone-agnostic (always UTC by definition).
    """
    warnings = []

    for i, msg in enumerate(conversation):
        timestamp = msg.get("timestamp")

        # Only check string timestamps (ISO format)
        if isinstance(timestamp, str):
            # Check if it looks like ISO format
            if 'T' in timestamp or '-' in timestamp:
                # Should end with 'Z' for UTC
                if not timestamp.endswith('Z'):
                    # Try to parse and check timezone
                    try:
                        dt = datetime.fromisoformat(timestamp)

                        if dt.tzinfo is None:
                            warnings.append(
                                f"Message {i}: timestamp {timestamp} has no timezone "
                                f"(should be UTC with 'Z' suffix)"
                            )
                        elif dt.tzinfo != timezone.utc:
                            warnings.append(
                                f"Message {i}: timestamp {timestamp} is not UTC "
                                f"(timezone: {dt.tzinfo})"
                            )
                    except ValueError:
                        # Not a valid ISO timestamp, skip
                        pass

    return len(warnings) == 0, warnings


def normalize_timestamps_to_utc(conversation: List[Dict]) -> None:
    """
    Normalize all timestamps to UTC Unix timestamps (float)

    This converts any string timestamps to Unix timestamps in UTC.
    Modifies conversation in place.

    Args:
        conversation: Conversation to normalize
    """
    for msg in conversation:
        timestamp = msg.get("timestamp")

        if timestamp is None:
            continue

        # Already a Unix timestamp (int/float)
        if isinstance(timestamp, (int, float)):
            continue

        # String timestamp - convert to Unix timestamp
        if isinstance(timestamp, str):
            try:
                # Try ISO format
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))

                # Convert to UTC if not already
                if dt.tzinfo is None:
                    # Assume UTC if no timezone
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    # Convert to UTC
                    dt = dt.astimezone(timezone.utc)

                # Store as Unix timestamp
                msg["timestamp"] = dt.timestamp()

            except ValueError:
                # Try as plain float
                try:
                    msg["timestamp"] = float(timestamp)
                except ValueError:
                    # Cannot parse - leave as is
                    pass


def add_monotonic_validation_to_verifier():
    """
    Add monotonic timestamp check to SummaryVerifier

    This extends the SummaryVerifier class to check timestamps
    during validation.
    """
    from .summary_verification import SummaryVerifier

    # Monkey-patch to add timestamp validation
    original_verify_summary = SummaryVerifier.verify_summary

    def verify_summary_with_timestamps(self, summary: Dict) -> Tuple[bool, Dict]:
        # Run original validation
        is_valid, errors = original_verify_summary(self, summary)

        # Add timestamp validation
        timestamps_valid, timestamp_errors = validate_monotonic_timestamps(self.conversation)

        if not timestamps_valid:
            errors["timestamps"] = timestamp_errors
            is_valid = False

        # Add UTC check (warnings only)
        utc_valid, utc_warnings = validate_timezone_utc(self.conversation)

        if not utc_valid:
            # Add as warnings, not errors (won't fail validation)
            if "warnings" not in errors:
                errors["warnings"] = []
            errors["warnings"].extend(utc_warnings)

        return is_valid, errors

    SummaryVerifier.verify_summary = verify_summary_with_timestamps


def repair_non_monotonic_timestamps(conversation: List[Dict]) -> Tuple[List[Dict], List[str]]:
    """
    Attempt to repair non-monotonic timestamps

    Strategy: If timestamps are out of order, interpolate missing/wrong timestamps
    based on surrounding messages.

    Args:
        conversation: Conversation with potentially broken timestamps

    Returns:
        (repaired_conversation, warnings)
    """
    repaired = []
    warnings = []
    last_timestamp = None

    for i, msg in enumerate(conversation):
        msg_copy = msg.copy()
        timestamp = msg.get("timestamp")

        if timestamp is None:
            # Interpolate from neighbors
            if last_timestamp is not None:
                # Use last timestamp + small increment
                new_timestamp = last_timestamp + 0.1
                msg_copy["timestamp"] = new_timestamp
                warnings.append(f"Message {i}: missing timestamp, interpolated to {new_timestamp}")
            else:
                # First message - use 0.0
                msg_copy["timestamp"] = 0.0
                warnings.append(f"Message {i}: missing timestamp, set to 0.0")

            timestamp = msg_copy["timestamp"]

        # Check for non-monotonic
        if last_timestamp is not None and timestamp <= last_timestamp:
            # Repair by using last + increment
            new_timestamp = last_timestamp + 0.1
            msg_copy["timestamp"] = new_timestamp
            warnings.append(
                f"Message {i}: non-monotonic timestamp {timestamp}, "
                f"repaired to {new_timestamp}"
            )
            timestamp = new_timestamp

        repaired.append(msg_copy)
        last_timestamp = timestamp

    return repaired, warnings
