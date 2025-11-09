"""
Code Change Detector - Extract ground truth code changes from conversation
Detects file operations from tool messages and code blocks
"""

import re
from typing import Dict, List, Optional, Tuple
from pathlib import Path


class CodeChangeDetector:
    """Detect code changes from conversation events"""

    # Patterns for detecting file operations in messages
    FILE_OP_PATTERNS = {
        "created": [
            r'(?i)created?\s+(?:file|directory):\s*([^\s\n]+)',
            r'(?i)wrote\s+(?:file|to):\s*([^\s\n]+)',
            r'(?i)new\s+file:\s*([^\s\n]+)',
        ],
        "updated": [
            r'(?i)updated?\s+(?:file):\s*([^\s\n]+)',
            r'(?i)modified?\s+(?:file):\s*([^\s\n]+)',
            r'(?i)edited?\s+(?:file):\s*([^\s\n]+)',
        ],
        "deleted": [
            r'(?i)deleted?\s+(?:file):\s*([^\s\n]+)',
            r'(?i)removed?\s+(?:file):\s*([^\s\n]+)',
        ],
    }

    # Pattern for detecting code blocks with file paths
    CODE_BLOCK_PATTERN = re.compile(
        r'```(?:[\w]+)?\s*\n'  # Opening fence with optional language
        r'(.*?)'                # Content
        r'```',                 # Closing fence
        re.DOTALL
    )

    def __init__(self):
        """Initialize detector"""
        self.detected_changes = {}  # file_path -> change info

    def detect_file_operations(self, message: Dict) -> List[Dict]:
        """
        Detect file operations from a single message

        Args:
            message: Message dict with role, content, timestamp

        Returns:
            List of detected operations
        """
        content = message.get("content", "")
        operations = []

        # Check each pattern type
        for op_type, patterns in self.FILE_OP_PATTERNS.items():
            for pattern in patterns:
                matches = re.finditer(pattern, content)
                for match in matches:
                    file_path = match.group(1).strip()
                    operations.append({
                        "type": op_type,
                        "file": file_path,
                        "timestamp": message.get("timestamp", 0),
                        "message_role": message.get("role", "unknown")
                    })

        return operations

    def detect_code_blocks(self, message: Dict) -> List[Dict]:
        """
        Detect code blocks in message

        Args:
            message: Message dict

        Returns:
            List of code blocks with metadata
        """
        content = message.get("content", "")
        blocks = []

        matches = self.CODE_BLOCK_PATTERN.finditer(content)
        for match in matches:
            code_content = match.group(1)
            blocks.append({
                "content": code_content,
                "lines": len(code_content.split('\n')),
                "timestamp": message.get("timestamp", 0),
                "message_role": message.get("role", "unknown")
            })

        return blocks

    def estimate_lines_changed(self, code_block: str) -> Tuple[int, int]:
        """
        Estimate lines added/removed from a code block

        Args:
            code_block: Code content

        Returns:
            (lines_added, lines_removed)
        """
        lines = code_block.split('\n')

        # Look for diff-style markers
        added = sum(1 for line in lines if line.strip().startswith('+') and not line.strip().startswith('+++'))
        removed = sum(1 for line in lines if line.strip().startswith('-') and not line.strip().startswith('---'))

        # If no diff markers, assume all lines are additions
        if added == 0 and removed == 0:
            added = len([line for line in lines if line.strip()])

        return added, removed

    def analyze_conversation(self, conversation: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Analyze entire conversation for code changes

        Args:
            conversation: List of message dicts

        Returns:
            Dict with categorized changes
        """
        results = {
            "file_operations": [],
            "code_blocks": [],
            "files_changed": {},  # file_path -> operation info
        }

        for i, message in enumerate(conversation):
            msg_id = f"msg_{i+1:03d}"

            # Detect file operations
            ops = self.detect_file_operations(message)
            for op in ops:
                op["message_id"] = msg_id
                results["file_operations"].append(op)

                # Track file changes
                file_path = op["file"]
                if file_path not in results["files_changed"]:
                    results["files_changed"][file_path] = {
                        "operations": [],
                        "first_seen": msg_id,
                        "last_seen": msg_id,
                    }
                results["files_changed"][file_path]["operations"].append(op["type"])
                results["files_changed"][file_path]["last_seen"] = msg_id

            # Detect code blocks
            blocks = self.detect_code_blocks(message)
            for block in blocks:
                block["message_id"] = msg_id
                added, removed = self.estimate_lines_changed(block["content"])
                block["lines_added"] = added
                block["lines_removed"] = removed
                results["code_blocks"].append(block)

        return results

    def build_code_changes_from_ground_truth(
        self,
        conversation: List[Dict],
        group_by_topic: bool = True
    ) -> List[Dict]:
        """
        Build code change items using ground truth from conversation

        Args:
            conversation: List of message dicts
            group_by_topic: Whether to group related changes

        Returns:
            List of code change dicts ready for summary
        """
        analysis = self.analyze_conversation(conversation)
        changes = []

        # Group file operations by file
        for file_path, info in analysis["files_changed"].items():
            # Determine change type
            ops = info["operations"]
            if "created" in ops:
                change_type = "feature"  # New file likely a feature
            elif "deleted" in ops:
                change_type = "refactor"
            else:
                change_type = "feature"  # Default

            # Find related code blocks
            related_blocks = [
                block for block in analysis["code_blocks"]
                if file_path in block.get("content", "")
            ]

            # Sum lines changed
            total_added = sum(block["lines_added"] for block in related_blocks)
            total_removed = sum(block["lines_removed"] for block in related_blocks)

            # Build message range
            # Range semantics: [start_index, end_index) - inclusive-exclusive, 0-based
            # Message IDs are 1-based: msg_042 is 42nd message, stored at index 41
            first_msg = info["first_seen"]
            last_msg = info["last_seen"]
            first_msg_num = int(first_msg.split("_")[1])  # e.g., 42
            last_msg_num = int(last_msg.split("_")[1])    # e.g., 50

            # Convert to 0-based indices
            # msg_042 (42nd message) → index 41 (0-based, inclusive)
            # msg_050 (50th message) → index 49, so end_index = 50 (exclusive)
            start_index = first_msg_num - 1   # 42 → 41
            end_index = last_msg_num          # 50 (exclusive, so includes index 49 which is msg_050)

            changes.append({
                "files": [file_path],
                "description": f"{'Created' if 'created' in ops else 'Updated'} {Path(file_path).name}",
                "type": change_type,
                "lines_added": total_added if total_added > 0 else None,
                "lines_removed": total_removed if total_removed > 0 else None,
                "source_of_truth": "file_events",
                "references": [first_msg, last_msg] if first_msg != last_msg else [first_msg],
                "message_range": {
                    "start": first_msg,
                    "end": last_msg,
                    "start_index": start_index,
                    "end_index": end_index
                },
                "confidence": "high"
            })

        return changes

    def augment_llm_code_changes(
        self,
        llm_changes: List[Dict],
        ground_truth_analysis: Dict
    ) -> List[Dict]:
        """
        Augment LLM-inferred code changes with ground truth metrics

        Args:
            llm_changes: Code changes from LLM summarization
            ground_truth_analysis: Results from analyze_conversation()

        Returns:
            Augmented code changes with real metrics where available
        """
        augmented = []

        for change in llm_changes:
            aug_change = change.copy()

            # Try to find matching ground truth
            for file_path in change.get("files", []):
                if file_path in ground_truth_analysis["files_changed"]:
                    # Found ground truth - augment with real data
                    info = ground_truth_analysis["files_changed"][file_path]

                    # Find related code blocks
                    related_blocks = [
                        block for block in ground_truth_analysis["code_blocks"]
                        if file_path in block.get("content", "")
                    ]

                    # Sum real lines changed
                    if related_blocks:
                        total_added = sum(block["lines_added"] for block in related_blocks)
                        total_removed = sum(block["lines_removed"] for block in related_blocks)

                        aug_change["lines_added"] = total_added
                        aug_change["lines_removed"] = total_removed
                        aug_change["source_of_truth"] = "file_events"
                        aug_change["confidence"] = "high"

            augmented.append(aug_change)

        return augmented
