"""
Summary Verification - Validate references and prevent hallucinations
Ensures all message references actually exist and are properly ordered
"""

from typing import Dict, List, Optional, Tuple, Any


class SummaryVerifier:
    """Verify summary item references against actual conversation"""

    def __init__(self, conversation: List[Dict]):
        """
        Initialize verifier with conversation

        Args:
            conversation: List of message dicts with role, content, timestamp
        """
        self.conversation = conversation

        # Build message ID lookup
        self.message_lookup = {}
        for idx, msg in enumerate(conversation):
            msg_id = f"msg_{idx+1:03d}"  # 1-based, zero-padded
            self.message_lookup[msg_id] = {
                "index": idx + 1,  # 1-based
                "message": msg
            }

    def verify_message_exists(self, msg_id: str) -> bool:
        """Check if message ID exists"""
        return msg_id in self.message_lookup

    def verify_message_range(self, message_range: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Verify message range is valid

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

        # Verify indices match IDs
        if self.message_lookup[start_id]["index"] != start_idx:
            return False, f"Start index mismatch: {start_id} should be index {self.message_lookup[start_id]['index']}, got {start_idx}"
        if self.message_lookup[end_id]["index"] != end_idx:
            return False, f"End index mismatch: {end_id} should be index {self.message_lookup[end_id]['index']}, got {end_idx}"

        # Verify range is ordered (start <= end)
        if start_idx > end_idx:
            return False, f"Invalid range: start {start_idx} > end {end_idx}"

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

        # Verify references fall within message range
        if "references" in decision and "message_range" in decision:
            msg_range = decision["message_range"]
            start_idx = msg_range.get("start_index", 0)
            end_idx = msg_range.get("end_index", len(self.conversation))

            for ref in decision["references"]:
                if ref in self.message_lookup:
                    ref_idx = self.message_lookup[ref]["index"]
                    if ref_idx < start_idx or ref_idx > end_idx:
                        errors.append(f"Reference {ref} (index {ref_idx}) outside message_range [{start_idx}, {end_idx}]")

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
            "decisions": [],
            "code_changes": [],
            "problems_solved": [],
            "open_issues": [],
            "next_steps": []
        }

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
