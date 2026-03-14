"""
Summary Verification - Validate references and prevent hallucinations
Ensures all message references actually exist and are properly ordered
"""

from typing import Dict, List, Optional, Tuple, Any


class SummaryVerifier:
    """Verify summary item references against actual conversation and spans."""

    def __init__(self, conversation: List[Dict], spans: Optional[List[Dict]] = None):
        """
        Initialize verifier with conversation

        Args:
            conversation: List of message dicts with role, content, timestamp
        """
        self.conversation = conversation

        # Build message ID lookup
        # Message IDs are 1-based (msg_001, msg_002, ...)
        # Array indices are 0-based (0, 1, 2, ...)
        self.message_lookup = {}
        for idx, msg in enumerate(conversation):
            msg_id = f"msg_{idx+1:03d}"  # 1-based, zero-padded (msg_001 for first message)
            self.message_lookup[msg_id] = {
                "msg_num": idx + 1,  # 1-based message number
                "index": idx,        # 0-based array index
                "message": msg
            }
        self.spans = spans or []
        self.span_lookup = {
            span["id"]: span for span in self.spans
            if isinstance(span, dict) and span.get("id")
        }

    def verify_message_exists(self, msg_id: str) -> bool:
        """Check if message ID exists"""
        return msg_id in self.message_lookup

    def verify_message_range(self, message_range: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Verify message range is valid

        Range semantics: [start_index, end_index) - inclusive-exclusive, 0-based
        Example: msg_042 to msg_050 → start_index=41, end_index=50

        Args:
            message_range: Dict with start, end, start_index, end_index

        Returns:
            (is_valid, error_message)
        """
        # Check required fields
        required = ["start", "end", "start_index", "end_index"]
        for field in required:
            if field not in message_range:
                return False, f"Missing field: {field}"

        start_id = message_range["start"]
        end_id = message_range["end"]
        start_idx = message_range["start_index"]
        end_idx = message_range["end_index"]

        # Verify IDs exist
        if not self.verify_message_exists(start_id):
            return False, f"Start message not found: {start_id}"
        if not self.verify_message_exists(end_id):
            return False, f"End message not found: {end_id}"

        # Extract message numbers from IDs (1-based)
        start_num = self.message_lookup[start_id]["msg_num"]  # e.g., 42
        end_num = self.message_lookup[end_id]["msg_num"]      # e.g., 50

        # Verify indices match IDs using new semantics
        # msg_042 (42nd message) → start_index = 41 (0-based, inclusive)
        # msg_050 (50th message) → end_index = 50 (0-based, exclusive)
        expected_start_idx = start_num - 1
        expected_end_idx = end_num

        if start_idx != expected_start_idx:
            return False, f"Start index mismatch: {start_id} (message {start_num}) should have start_index={expected_start_idx} (0-based inclusive), got {start_idx}"

        if end_idx != expected_end_idx:
            return False, f"End index mismatch: {end_id} (message {end_num}) should have end_index={expected_end_idx} (0-based exclusive), got {end_idx}"

        # Verify non-negative range length
        if end_idx < start_idx:
            return False, f"Invalid range: end_index {end_idx} < start_index {start_idx} (negative length)"

        # Verify indices in bounds
        if start_idx < 0:
            return False, f"start_index {start_idx} < 0 (out of bounds)"
        if start_idx >= len(self.conversation):
            return False, f"start_index {start_idx} >= conversation length {len(self.conversation)}"
        if end_idx > len(self.conversation):
            return False, f"end_index {end_idx} > conversation length {len(self.conversation)}"
        if end_idx <= 0:
            return False, f"end_index {end_idx} <= 0 (invalid)"

        # Verify retrieval correctness
        # conversation[start_idx:end_idx] should include messages from start_id to end_id
        messages = self.conversation[start_idx:end_idx]
        if len(messages) == 0:
            return False, f"Range [{start_idx}, {end_idx}) is empty"

        # First message should be start_id
        first_msg_id = f"msg_{start_idx + 1:03d}"
        if first_msg_id != start_id:
            return False, f"First message in range is {first_msg_id}, expected {start_id}"

        # Last message should be end_id (remember end_idx is exclusive)
        last_msg_id = f"msg_{end_idx:03d}"  # end_idx is exclusive, so last is at end_idx - 1, which is message number end_idx
        if last_msg_id != end_id:
            return False, f"Last message in range is {last_msg_id}, expected {end_id}"

        return True, None

    def verify_references(self, references: List[str]) -> Tuple[bool, List[str]]:
        """
        Verify all references exist and are ordered

        Args:
            references: List of message IDs

        Returns:
            (all_valid, missing_ids)
        """
        missing = []
        for ref in references:
            if not self.verify_message_exists(ref):
                missing.append(ref)

        # Check if references are in chronological order
        if len(references) > 1 and not missing:
            indices = [self.message_lookup[ref]["index"] for ref in references]
            if indices != sorted(indices):
                # Not an error, just a warning (references can jump around)
                pass

        return len(missing) == 0, missing

    def verify_span(self, span: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Verify a span points to a valid conversation region."""
        required = ["id", "kind", "start_message_id", "start_index"]
        for field in required:
            if field not in span:
                return False, f"Missing field: {field}"

        status = span.get("status", "closed")
        start_id = span["start_message_id"]
        start_idx = span["start_index"]

        if not self.verify_message_exists(start_id):
            return False, f"Span start message not found: {start_id}"

        if status == "open":
            latest_message_id = span.get("latest_message_id")
            latest_index = span.get("latest_index")
            if latest_message_id is not None and not self.verify_message_exists(latest_message_id):
                return False, f"Open span latest message not found: {latest_message_id}"
            if latest_index is not None and (not isinstance(latest_index, int) or latest_index < start_idx):
                return False, f"Invalid open span latest_index: {latest_index}"

            end_id = span.get("end_message_id")
            end_idx = span.get("end_index")
            if end_id is None and end_idx is None:
                return True, None
        else:
            end_id = span.get("end_message_id")
            end_idx = span.get("end_index")

        if end_id is None or end_idx is None:
            return False, "Closed span missing end_message_id or end_index"
        if not self.verify_message_exists(end_id):
            return False, f"Span end message not found: {end_id}"

        expected_range = {
            "start": start_id,
            "end": end_id,
            "start_index": start_idx,
            "end_index": end_idx,
        }
        return self.verify_message_range(expected_range)

    def _resolved_spans(self, span_ids: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
        spans = []
        missing = []
        for span_id in span_ids:
            span = self.span_lookup.get(span_id)
            if span is None:
                missing.append(span_id)
            else:
                spans.append(span)
        return spans, missing

    def _span_contains_index(self, span: Dict[str, Any], index: int) -> bool:
        start_index = span.get("start_index", 0)
        end_index = span.get("end_index")
        if not isinstance(end_index, int):
            latest_index = span.get("latest_index")
            if isinstance(latest_index, int):
                end_index = latest_index + 1
        if not isinstance(end_index, int):
            return index >= start_index
        return start_index <= index < end_index

    def verify_item_links(self, item: Dict[str, Any]) -> List[str]:
        """Verify span IDs, ranges, and references agree with each other."""
        errors = []
        span_ids = item.get("span_ids")

        if span_ids is None:
            errors.append("Missing span_ids")
            return errors
        if not isinstance(span_ids, list):
            errors.append("Invalid span_ids: expected list")
            return errors

        spans, missing = self._resolved_spans(span_ids)
        if missing:
            errors.append(f"Missing span_ids: {missing}")

        if spans and "message_range" in item:
            start_idx = item["message_range"].get("start_index")
            end_idx = item["message_range"].get("end_index")
            if isinstance(start_idx, int) and isinstance(end_idx, int):
                union_start = min(span.get("start_index", start_idx) for span in spans)
                union_end = max(
                    (
                        span.get("end_index")
                        if isinstance(span.get("end_index"), int)
                        else (span.get("latest_index") + 1 if isinstance(span.get("latest_index"), int) else end_idx)
                    )
                    for span in spans
                )
                if start_idx < union_start or end_idx > union_end:
                    errors.append(
                        f"message_range [{start_idx}, {end_idx}) not contained within linked spans "
                        f"[{union_start}, {union_end})"
                    )

        if spans and "references" in item:
            for ref in item["references"]:
                if ref in self.message_lookup:
                    ref_idx = self.message_lookup[ref]["index"]
                    if not any(self._span_contains_index(span, ref_idx) for span in spans):
                        errors.append(f"Reference {ref} (index {ref_idx}) outside linked spans")

        return errors

    def verify_decision(self, decision: Dict) -> Tuple[bool, List[str]]:
        """
        Verify a decision item

        Returns:
            (is_valid, errors)
        """
        errors = []

        # Verify references
        if "references" in decision:
            valid, missing = self.verify_references(decision["references"])
            if not valid:
                errors.append(f"Missing references: {missing}")

        # Verify message range
        if "message_range" in decision:
            valid, error = self.verify_message_range(decision["message_range"])
            if not valid:
                errors.append(f"Invalid message_range: {error}")

        errors.extend(self.verify_item_links(decision))

        # Verify references fall within message range
        # Range semantics: [start_index, end_index) - inclusive-exclusive
        if "references" in decision and "message_range" in decision:
            msg_range = decision["message_range"]
            start_idx = msg_range.get("start_index", 0)
            end_idx = msg_range.get("end_index", len(self.conversation))

            for ref in decision["references"]:
                if ref in self.message_lookup:
                    ref_idx = self.message_lookup[ref]["index"]
                    # Range is [start_idx, end_idx) so ref must be >= start_idx and < end_idx
                    if ref_idx < start_idx or ref_idx >= end_idx:
                        errors.append(f"Reference {ref} (index {ref_idx}) outside message_range [{start_idx}, {end_idx})")

        return len(errors) == 0, errors

    def verify_code_change(self, change: Dict) -> Tuple[bool, List[str]]:
        """Verify a code change item"""
        errors = []

        # Same verification as decision
        if "references" in change:
            valid, missing = self.verify_references(change["references"])
            if not valid:
                errors.append(f"Missing references: {missing}")

        if "message_range" in change:
            valid, error = self.verify_message_range(change["message_range"])
            if not valid:
                errors.append(f"Invalid message_range: {error}")

        errors.extend(self.verify_item_links(change))

        return len(errors) == 0, errors

    def verify_summary(self, summary: Dict) -> Tuple[bool, Dict[str, List[str]]]:
        """
        Verify entire summary

        Args:
            summary: Summary dict to verify

        Returns:
            (is_valid, errors_by_category)
        """
        all_errors = {
            "spans": [],
            "decisions": [],
            "code_changes": [],
            "problems_solved": [],
            "open_issues": [],
            "next_steps": []
        }

        for span in self.spans:
            valid, error = self.verify_span(span)
            if not valid:
                all_errors["spans"].append(f"Span {span.get('id', 'no-id')}: {error}")

        # Verify decisions
        for i, decision in enumerate(summary.get("decisions", [])):
            valid, errors = self.verify_decision(decision)
            if not valid:
                all_errors["decisions"].append(f"Decision {i} ({decision.get('id', 'no-id')}): {', '.join(errors)}")

        # Verify code changes
        for i, change in enumerate(summary.get("code_changes", [])):
            valid, errors = self.verify_code_change(change)
            if not valid:
                all_errors["code_changes"].append(f"Code change {i} ({change.get('id', 'no-id')}): {', '.join(errors)}")

        # Verify problems solved
        for i, problem in enumerate(summary.get("problems_solved", [])):
            valid, errors = self.verify_decision(problem)  # Same structure
            if not valid:
                all_errors["problems_solved"].append(f"Problem {i} ({problem.get('id', 'no-id')}): {', '.join(errors)}")

        # Verify open issues
        for i, issue in enumerate(summary.get("open_issues", [])):
            valid, errors = self.verify_decision(issue)
            if not valid:
                all_errors["open_issues"].append(f"Issue {i} ({issue.get('id', 'no-id')}): {', '.join(errors)}")

        # Verify next steps
        for i, step in enumerate(summary.get("next_steps", [])):
            valid, errors = self.verify_decision(step)
            if not valid:
                all_errors["next_steps"].append(f"Next step {i} ({step.get('id', 'no-id')}): {', '.join(errors)}")

        # Check if any errors
        has_errors = any(len(errs) > 0 for errs in all_errors.values())

        return not has_errors, all_errors

    def extract_quote(self, msg_id: str, max_words: int = 20) -> Optional[str]:
        """
        Extract a short quote from a message for verification

        Args:
            msg_id: Message ID
            max_words: Maximum words in quote

        Returns:
            Quote string or None
        """
        if not self.verify_message_exists(msg_id):
            return None

        msg = self.message_lookup[msg_id]["message"]
        content = msg.get("content", "")

        # Get first N words
        words = content.split()[:max_words]
        quote = " ".join(words)

        if len(content.split()) > max_words:
            quote += "..."

        return quote

    def auto_fix_summary(self, summary: Dict) -> Tuple[Dict, List[str]]:
        """
        Attempt to auto-fix common summary errors

        Args:
            summary: Summary to fix

        Returns:
            (fixed_summary, warnings)
        """
        warnings = []
        fixed = summary.copy()
        from .summary_schema import ensure_summary_span_links

        self.spans = ensure_summary_span_links(fixed, self.spans)
        self.span_lookup = {
            span["id"]: span for span in self.spans
            if isinstance(span, dict) and span.get("id")
        }

        # Remove items with invalid references
        for category in ["decisions", "code_changes", "problems_solved", "open_issues", "next_steps"]:
            if category not in fixed:
                continue

            valid_items = []
            for item in fixed[category]:
                # Check if references are valid
                if "references" in item:
                    valid, missing = self.verify_references(item["references"])
                    if not valid:
                        warnings.append(f"Removed {category} item {item.get('id', 'no-id')} due to missing references: {missing}")
                        continue

                # Check if message range is valid
                if "message_range" in item:
                    valid, error = self.verify_message_range(item["message_range"])
                    if not valid:
                        warnings.append(f"Removed {category} item {item.get('id', 'no-id')} due to invalid range: {error}")
                        continue

                valid_items.append(item)

            fixed[category] = valid_items

        return fixed, warnings
