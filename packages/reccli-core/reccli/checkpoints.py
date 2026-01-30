"""
Checkpoint Management for RecCli Phase 7

Manual checkpoints for tracking project milestones.
Enables "what changed since CP_X?" queries.
"""

from typing import List, Dict, Optional
from datetime import datetime
from pathlib import Path
import json


class CheckpointManager:
    """
    Manage manual checkpoints in .devsession files

    Checkpoints are milestones marked by the user:
    - "CP_12 - pre-release"
    - "CP_14 - after refactor"
    - etc.

    Enables queries like: "What changed since CP_12?"
    """

    def __init__(self, session):
        """
        Initialize checkpoint manager

        Args:
            session: DevSession object
        """
        self.session = session

        # Initialize checkpoints list if not exists
        if not hasattr(session, 'checkpoints'):
            session.checkpoints = []

    def add_checkpoint(self, label: str, criteria: Optional[str] = None) -> Dict:
        """
        Add a manual checkpoint

        Args:
            label: Checkpoint label (e.g., "pre-release", "after refactor")
            criteria: Optional criteria description (e.g., "all tests passing")

        Returns:
            Checkpoint dict
        """
        # Generate checkpoint ID
        checkpoint_id = self._generate_checkpoint_id()

        # Create checkpoint
        checkpoint = {
            'id': checkpoint_id,
            't': datetime.now().isoformat(),
            'label': label,
            'criteria': criteria,
            'message_index': len(self.session.conversation) - 1,
            'token_count': self._get_current_token_count(),
            'summary_snapshot': self._snapshot_summary()
        }

        # Add to session
        self.session.checkpoints.append(checkpoint)

        # Save session
        self.session.save()

        return checkpoint

    def get_checkpoint(self, checkpoint_id: str) -> Optional[Dict]:
        """
        Get a checkpoint by ID

        Args:
            checkpoint_id: Checkpoint ID (e.g., "CP_12")

        Returns:
            Checkpoint dict or None if not found
        """
        for cp in self.session.checkpoints:
            if cp['id'] == checkpoint_id:
                return cp
        return None

    def list_checkpoints(self) -> List[Dict]:
        """
        List all checkpoints

        Returns:
            List of checkpoint dicts
        """
        return sorted(self.session.checkpoints, key=lambda x: x['t'])

    def diff_since_checkpoint(self, checkpoint_id: str) -> Dict:
        """
        Get changes since a checkpoint

        Args:
            checkpoint_id: Checkpoint ID

        Returns:
            Diff dict with spans and code changes since checkpoint
        """
        checkpoint = self.get_checkpoint(checkpoint_id)
        if not checkpoint:
            raise ValueError(f"Checkpoint not found: {checkpoint_id}")

        cp_time = datetime.fromisoformat(checkpoint['t'])
        cp_message_index = checkpoint['message_index']

        # Get all spans after checkpoint
        spans_since = []
        if self.session.summary:
            # Decisions
            for decision in self.session.summary.get('decisions', []):
                if self._is_after_checkpoint(decision, cp_time, cp_message_index):
                    spans_since.append({
                        'type': 'decision',
                        'id': decision.get('id'),
                        'content': decision.get('decision'),
                        'timestamp': self._get_span_timestamp(decision)
                    })

            # Code changes
            for code in self.session.summary.get('code_changes', []):
                if self._is_after_checkpoint(code, cp_time, cp_message_index):
                    spans_since.append({
                        'type': 'code_change',
                        'id': code.get('id'),
                        'content': f"{code.get('description')} ({', '.join(code.get('files', []))})",
                        'timestamp': self._get_span_timestamp(code)
                    })

            # Problems solved
            for problem in self.session.summary.get('problems_solved', []):
                if self._is_after_checkpoint(problem, cp_time, cp_message_index):
                    spans_since.append({
                        'type': 'problem_solved',
                        'id': problem.get('id'),
                        'content': problem.get('problem'),
                        'timestamp': self._get_span_timestamp(problem)
                    })

            # Open issues
            for issue in self.session.summary.get('open_issues', []):
                if self._is_after_checkpoint(issue, cp_time, cp_message_index):
                    spans_since.append({
                        'type': 'open_issue',
                        'id': issue.get('id'),
                        'content': issue.get('issue'),
                        'timestamp': self._get_span_timestamp(issue)
                    })

        # Sort by timestamp
        spans_since.sort(key=lambda x: x.get('timestamp', ''))

        return {
            'checkpoint': checkpoint,
            'spans_since': spans_since,
            'span_count': len(spans_since),
            'time_elapsed': self._format_time_elapsed(cp_time)
        }

    def _generate_checkpoint_id(self) -> str:
        """Generate next checkpoint ID (CP_01, CP_02, etc.)"""
        if not self.session.checkpoints:
            return "CP_01"

        # Find highest number
        max_num = 0
        for cp in self.session.checkpoints:
            cp_id = cp['id']
            if cp_id.startswith('CP_'):
                try:
                    num = int(cp_id.split('_')[1])
                    max_num = max(max_num, num)
                except (IndexError, ValueError):
                    pass

        return f"CP_{max_num + 1:02d}"

    def _get_current_token_count(self) -> int:
        """Get current token count from session"""
        if hasattr(self.session, 'token_counts') and self.session.token_counts:
            return self.session.token_counts.get('total', 0)
        return 0

    def _snapshot_summary(self) -> Dict:
        """Create snapshot of current summary state"""
        if not self.session.summary:
            return {}

        return {
            'decision_count': len(self.session.summary.get('decisions', [])),
            'code_change_count': len(self.session.summary.get('code_changes', [])),
            'problem_count': len(self.session.summary.get('problems_solved', [])),
            'open_issue_count': len(self.session.summary.get('open_issues', []))
        }

    def _is_after_checkpoint(
        self,
        span: Dict,
        checkpoint_time: datetime,
        checkpoint_message_index: int
    ) -> bool:
        """Check if span is after checkpoint"""
        # Check by message index if available
        message_range = span.get('message_range', {})
        if message_range:
            start_index = message_range.get('start_index', 0)
            return start_index > checkpoint_message_index

        # Fallback to timestamp
        span_time = self._get_span_timestamp(span)
        if span_time:
            try:
                span_dt = datetime.fromisoformat(span_time)
                return span_dt > checkpoint_time
            except (ValueError, TypeError):
                pass

        # Default to including if uncertain
        return True

    def _get_span_timestamp(self, span: Dict) -> Optional[str]:
        """Extract timestamp from span"""
        # Try temporal metadata
        temporal = span.get('temporal', {})
        if temporal:
            return temporal.get('t_first') or temporal.get('t_last')

        # Try message_range
        message_range = span.get('message_range', {})
        if message_range:
            start_index = message_range.get('start_index')
            if start_index is not None and start_index < len(self.session.conversation):
                msg = self.session.conversation[start_index]
                return msg.get('timestamp')

        return None

    def _format_time_elapsed(self, checkpoint_time: datetime) -> str:
        """Format time elapsed since checkpoint"""
        elapsed = datetime.now() - checkpoint_time

        days = elapsed.days
        hours, remainder = divmod(elapsed.seconds, 3600)
        minutes, _ = divmod(remainder, 60)

        if days > 0:
            return f"{days}d {hours}h"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"


def format_checkpoint_diff(diff: Dict) -> str:
    """
    Format checkpoint diff for display

    Args:
        diff: Diff dict from diff_since_checkpoint()

    Returns:
        Formatted string
    """
    cp = diff['checkpoint']
    spans = diff['spans_since']

    output = []
    output.append(f"\n📍 Changes since {cp['id']}: {cp['label']}")
    output.append(f"⏱️  Created: {cp['t']}")
    output.append(f"⌛ Time elapsed: {diff['time_elapsed']}")
    output.append(f"📊 {diff['span_count']} changes")
    output.append("")

    if not spans:
        output.append("   No changes recorded since checkpoint")
        return "\n".join(output)

    # Group by type
    by_type = {}
    for span in spans:
        span_type = span['type']
        if span_type not in by_type:
            by_type[span_type] = []
        by_type[span_type].append(span)

    # Display by type
    type_labels = {
        'decision': '🎯 Decisions',
        'code_change': '💻 Code Changes',
        'problem_solved': '✅ Problems Solved',
        'open_issue': '⚠️  Open Issues'
    }

    for span_type, label in type_labels.items():
        if span_type in by_type:
            output.append(f"{label} ({len(by_type[span_type])})")
            for span in by_type[span_type]:
                output.append(f"   • {span['content']}")
            output.append("")

    return "\n".join(output)
