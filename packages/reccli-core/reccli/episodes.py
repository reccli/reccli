"""
Episode Detection for RecCli Phase 7

Heuristic detection of conversation episodes (work phases).
Episodes help organize context and improve retrieval.
"""

from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from collections import Counter
import re


class EpisodeDetector:
    """
    Detect episodes in conversation using heuristics

    Episodes are coherent work phases with distinct focus:
    - Burst of errors followed by fixes
    - New file set being worked on
    - Topic/vocabulary shift
    - Time gap (> 30 minutes)

    Example episodes:
    - Episode 1: "Debugging authentication bug" (msg 1-45)
    - Episode 2: "Building export dialog" (msg 46-120)
    - Episode 3: "Refactoring database layer" (msg 121-189)
    """

    def __init__(
        self,
        min_episode_length: int = 5,
        time_gap_threshold: int = 30,  # minutes
        file_set_threshold: float = 0.5,  # 50% different files
        vocab_threshold: float = 0.4  # 40% different vocab
    ):
        """
        Initialize episode detector

        Args:
            min_episode_length: Minimum messages per episode
            time_gap_threshold: Time gap to trigger new episode (minutes)
            file_set_threshold: File set difference to trigger new episode (0-1)
            vocab_threshold: Vocabulary difference to trigger new episode (0-1)
        """
        self.min_episode_length = min_episode_length
        self.time_gap_threshold = timedelta(minutes=time_gap_threshold)
        self.file_set_threshold = file_set_threshold
        self.vocab_threshold = vocab_threshold

    def detect_episodes(self, conversation: List[Dict]) -> List[Dict]:
        """
        Detect episodes in conversation

        Args:
            conversation: List of message dicts

        Returns:
            List of episode dicts with ranges and descriptions
        """
        if len(conversation) < self.min_episode_length:
            # Too short for episodes
            return [{
                'id': 'ep_001',
                'start_index': 0,
                'end_index': len(conversation) - 1,
                'message_count': len(conversation),
                'description': 'Single session',
                'trigger': 'none'
            }]

        episodes = []
        current_episode_start = 0
        episode_number = 1

        for i in range(1, len(conversation)):
            current_msg = conversation[i]
            prev_msg = conversation[i - 1]

            # Check for episode boundary triggers
            trigger = self._check_episode_boundary(
                prev_messages=conversation[current_episode_start:i],
                current_msg=current_msg,
                prev_msg=prev_msg
            )

            if trigger and (i - current_episode_start) >= self.min_episode_length:
                # Create episode for previous segment
                episode = self._create_episode(
                    episode_number=episode_number,
                    messages=conversation[current_episode_start:i],
                    start_index=current_episode_start,
                    end_index=i - 1,
                    trigger=trigger
                )
                episodes.append(episode)

                # Start new episode
                current_episode_start = i
                episode_number += 1

        # Create final episode
        if current_episode_start < len(conversation):
            episode = self._create_episode(
                episode_number=episode_number,
                messages=conversation[current_episode_start:],
                start_index=current_episode_start,
                end_index=len(conversation) - 1,
                trigger='end_of_session'
            )
            episodes.append(episode)

        return episodes

    def assign_episode_ids_to_summary(
        self,
        summary: Dict,
        episodes: List[Dict]
    ) -> Dict:
        """
        Assign episode IDs to summary items

        Args:
            summary: Summary dict
            episodes: List of episode dicts

        Returns:
            Updated summary with episode_id fields
        """
        # Assign episode_id to each summary item based on message_range
        for category in ['decisions', 'code_changes', 'problems_solved', 'open_issues', 'next_steps']:
            items = summary.get(category, [])
            for item in items:
                message_range = item.get('message_range', {})
                if message_range:
                    start_index = message_range.get('start_index', 0)
                    episode = self._find_episode_for_index(episodes, start_index)
                    if episode:
                        item['episode_id'] = episode['id']

        return summary

    def get_current_episode(
        self,
        conversation: List[Dict],
        episodes: List[Dict]
    ) -> Optional[Dict]:
        """
        Get the current (most recent) episode

        Args:
            conversation: Full conversation
            episodes: List of episodes

        Returns:
            Current episode dict or None
        """
        if not episodes:
            return None

        # Return last episode
        return episodes[-1]

    def _check_episode_boundary(
        self,
        prev_messages: List[Dict],
        current_msg: Dict,
        prev_msg: Dict
    ) -> Optional[str]:
        """
        Check if there's an episode boundary between prev_msg and current_msg

        Args:
            prev_messages: Messages in current episode so far
            current_msg: Current message
            prev_msg: Previous message

        Returns:
            Trigger reason if boundary detected, None otherwise
        """
        # Trigger 1: Time gap
        if self._has_time_gap(prev_msg, current_msg):
            return 'time_gap'

        # Trigger 2: Burst detection (many errors → fixes)
        if self._is_burst_boundary(prev_messages, current_msg):
            return 'error_burst_resolved'

        # Trigger 3: File set change
        if self._has_file_set_change(prev_messages, current_msg):
            return 'file_set_change'

        # Trigger 4: Vocabulary shift
        if self._has_vocabulary_shift(prev_messages, current_msg):
            return 'vocabulary_shift'

        return None

    def _has_time_gap(self, prev_msg: Dict, current_msg: Dict) -> bool:
        """Check if there's a significant time gap"""
        try:
            prev_time = self._parse_timestamp(prev_msg.get('timestamp'))
            curr_time = self._parse_timestamp(current_msg.get('timestamp'))

            if prev_time and curr_time:
                gap = curr_time - prev_time
                return gap > self.time_gap_threshold
        except (ValueError, TypeError):
            pass

        return False

    def _is_burst_boundary(
        self,
        prev_messages: List[Dict],
        current_msg: Dict
    ) -> bool:
        """
        Detect if we're at the end of an error burst

        Error burst: Multiple errors/failures followed by resolution
        """
        if len(prev_messages) < 5:
            return False

        # Count error indicators in recent messages
        recent = prev_messages[-10:]
        error_count = sum(1 for m in recent if self._contains_error_indicators(m))

        # High error rate (>50%) followed by non-error message
        if error_count >= 5 and not self._contains_error_indicators(current_msg):
            # Check if current message indicates resolution
            content = current_msg.get('content', '').lower()
            resolution_indicators = ['fixed', 'resolved', 'working', 'success', 'passed', 'complete']
            if any(ind in content for ind in resolution_indicators):
                return True

        return False

    def _has_file_set_change(
        self,
        prev_messages: List[Dict],
        current_msg: Dict
    ) -> bool:
        """Check if file set has significantly changed"""
        if len(prev_messages) < 10:
            return False

        # Extract files from previous episode
        prev_files = set()
        for msg in prev_messages[-20:]:
            prev_files.update(self._extract_files(msg.get('content', '')))

        # Extract files from current message
        current_files = self._extract_files(current_msg.get('content', ''))

        if not prev_files or not current_files:
            return False

        # Calculate overlap
        overlap = len(prev_files & current_files) / len(prev_files)
        return overlap < (1 - self.file_set_threshold)

    def _has_vocabulary_shift(
        self,
        prev_messages: List[Dict],
        current_msg: Dict
    ) -> bool:
        """Check if vocabulary has significantly shifted"""
        if len(prev_messages) < 15:
            return False

        # Get vocabulary from previous episode
        prev_vocab = self._extract_vocabulary(prev_messages[-30:])

        # Get vocabulary from current message neighborhood
        current_vocab = self._extract_vocabulary([current_msg])

        if not prev_vocab or not current_vocab:
            return False

        # Calculate overlap
        common = prev_vocab & current_vocab
        overlap = len(common) / len(prev_vocab) if prev_vocab else 0

        return overlap < (1 - self.vocab_threshold)

    def _create_episode(
        self,
        episode_number: int,
        messages: List[Dict],
        start_index: int,
        end_index: int,
        trigger: str
    ) -> Dict:
        """Create episode dict"""
        # Extract key characteristics
        files = set()
        topics = []
        has_errors = False

        for msg in messages:
            content = msg.get('content', '')
            files.update(self._extract_files(content))
            if self._contains_error_indicators(msg):
                has_errors = True

        # Generate description
        description = self._generate_episode_description(
            messages=messages,
            files=files,
            has_errors=has_errors
        )

        return {
            'id': f'ep_{episode_number:03d}',
            'start_index': start_index,
            'end_index': end_index,
            'message_count': len(messages),
            'description': description,
            'trigger': trigger,
            'files': list(files)[:10],  # Top 10 files
            'has_errors': has_errors,
            't_first': messages[0].get('timestamp') if messages else None,
            't_last': messages[-1].get('timestamp') if messages else None
        }

    def _generate_episode_description(
        self,
        messages: List[Dict],
        files: set,
        has_errors: bool
    ) -> str:
        """Generate human-readable episode description"""
        # Extract key verbs and nouns
        text = ' '.join([m.get('content', '')[:500] for m in messages[:10]])

        # Common action verbs in coding
        verbs = ['build', 'fix', 'debug', 'refactor', 'implement', 'add', 'update', 'create', 'test']
        found_verbs = [v for v in verbs if v in text.lower()]

        # File-based description
        if files:
            file_list = list(files)[:3]
            if len(file_list) == 1:
                base = f"Working on {file_list[0]}"
            else:
                base = f"Working on {', '.join(file_list[:2])}"
        else:
            base = "Development work"

        # Add action if found
        if found_verbs:
            base = f"{found_verbs[0].title()} - {base}"

        # Add error indicator
        if has_errors:
            base += " (debugging)"

        return base

    def _find_episode_for_index(
        self,
        episodes: List[Dict],
        message_index: int
    ) -> Optional[Dict]:
        """Find episode that contains a message index"""
        for episode in episodes:
            if episode['start_index'] <= message_index <= episode['end_index']:
                return episode
        return None

    def _parse_timestamp(self, timestamp) -> Optional[datetime]:
        """Parse timestamp to datetime"""
        if isinstance(timestamp, (int, float)):
            # Unix timestamp or seconds
            return datetime.fromtimestamp(timestamp)
        elif isinstance(timestamp, str):
            try:
                return datetime.fromisoformat(timestamp)
            except ValueError:
                pass
        return None

    def _contains_error_indicators(self, message: Dict) -> bool:
        """Check if message contains error indicators"""
        content = message.get('content', '').lower()
        indicators = [
            'error', 'exception', 'failed', 'failure', 'traceback',
            'undefined', 'null', 'cannot', 'unable', 'crash'
        ]
        return any(ind in content for ind in indicators)

    def _extract_files(self, text: str) -> set:
        """Extract file paths from text"""
        # Common file patterns
        patterns = [
            r'[\w/\-\.]+\.(py|js|ts|tsx|jsx|java|go|rs|cpp|c|h|css|html|md)',
            r'`[\w/\-\.]+\.(py|js|ts|tsx|jsx|java|go|rs|cpp|c|h|css|html|md)`'
        ]

        files = set()
        for pattern in patterns:
            matches = re.findall(pattern, text)
            files.update(matches)

        return files

    def _extract_vocabulary(self, messages: List[Dict]) -> set:
        """Extract technical vocabulary from messages"""
        text = ' '.join([m.get('content', '') for m in messages])

        # Extract technical words (camelCase, snake_case, capitalized)
        words = set()

        # CamelCase/PascalCase
        words.update(re.findall(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b', text))

        # snake_case
        words.update(re.findall(r'\b[a-z]+(?:_[a-z]+)+\b', text))

        # Technical capitalized words
        words.update(re.findall(r'\b[A-Z]{2,}\b', text))

        return words
