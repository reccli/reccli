"""
Session compaction module
Reduces .devsession file size by removing redundant data
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional


class SessionCompactor:
    """
    Compacts .devsession files using different policies

    Modes:
    - none: Keep everything (default, for debugging)
    - conversation: Keep only conversation + metadata (smallest, ~2-5KB)
    - audit: Keep conversation + audit frames, drop keystrokes (~5-15KB)
    - lossless: Move events to external .events.zst file (keeps replay ability)
    """

    MODES = ['none', 'conversation', 'audit', 'lossless']

    def __init__(self, mode: str = 'conversation'):
        if mode not in self.MODES:
            raise ValueError(f"Invalid mode: {mode}. Must be one of {self.MODES}")
        self.mode = mode

    def compact(self, session_path: Path, output_path: Optional[Path] = None) -> Dict[str, Any]:
        """
        Compact a .devsession file

        Args:
            session_path: Path to .devsession file
            output_path: Optional output path (defaults to in-place)

        Returns:
            Stats dict with before/after sizes
        """
        if not output_path:
            output_path = session_path

        # Load session
        with open(session_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        original_size = session_path.stat().st_size
        events_count = len(data.get('terminal_recording', {}).get('events', []))

        # Apply compaction based on mode
        if self.mode == 'none':
            compacted_data = data

        elif self.mode == 'conversation':
            compacted_data = self._compact_conversation_only(data)

        elif self.mode == 'audit':
            compacted_data = self._compact_audit(data)

        elif self.mode == 'lossless':
            compacted_data = self._compact_lossless(data, session_path)

        # Write compacted file atomically
        tmp_path = output_path.with_suffix('.devsession.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(compacted_data, f, indent=2, ensure_ascii=False)

        tmp_path.rename(output_path)

        compacted_size = output_path.stat().st_size
        saved_bytes = original_size - compacted_size

        return {
            'mode': self.mode,
            'original_size': original_size,
            'compacted_size': compacted_size,
            'saved_bytes': saved_bytes,
            'compression_ratio': original_size / compacted_size if compacted_size > 0 else 0,
            'events_removed': events_count
        }

    def _compact_conversation_only(self, data: Dict) -> Dict:
        """Keep only conversation + minimal metadata (smallest)"""
        return {
            'format': 'devsession',
            'version': data.get('version', '2.0'),
            'session_id': data.get('session_id'),
            'created': data.get('created'),
            'updated': datetime.now().isoformat(),

            # Essential data only
            'conversation': data.get('conversation', []),

            # Minimal metadata
            'meta': {
                'duration': self._calculate_duration(data),
                'message_count': len(data.get('conversation', [])),
                'compaction': {
                    'mode': 'conversation',
                    'compacted_at': datetime.now().isoformat(),
                    'original_events': len(data.get('terminal_recording', {}).get('events', []))
                }
            },

            # Drop everything else
            'terminal_recording': None,
            'summary': None,
            'vector_index': None,
            'token_counts': {},
            'checksums': {},
            'compaction_history': data.get('compaction_history', []) + [{
                'mode': 'conversation',
                'timestamp': datetime.now().isoformat()
            }]
        }

    def _compact_audit(self, data: Dict) -> Dict:
        """Keep conversation + audit frames, drop individual keystrokes"""
        # Extract audit frames (key moments in the recording)
        audit_frames = self._extract_audit_frames(data)

        return {
            'format': 'devsession',
            'version': data.get('version', '2.0'),
            'session_id': data.get('session_id'),
            'created': data.get('created'),
            'updated': datetime.now().isoformat(),

            'conversation': data.get('conversation', []),
            'audit_frames': audit_frames,

            'meta': {
                'duration': self._calculate_duration(data),
                'message_count': len(data.get('conversation', [])),
                'audit_frames_count': len(audit_frames),
                'compaction': {
                    'mode': 'audit',
                    'compacted_at': datetime.now().isoformat(),
                    'original_events': len(data.get('terminal_recording', {}).get('events', []))
                }
            },

            'terminal_recording': None,
            'summary': data.get('summary'),
            'token_counts': data.get('token_counts', {}),
            'compaction_history': data.get('compaction_history', []) + [{
                'mode': 'audit',
                'timestamp': datetime.now().isoformat()
            }]
        }

    def _compact_lossless(self, data: Dict, session_path: Path) -> Dict:
        """Move events to external .events.json file (keeps replay ability)"""
        # TODO: Implement external events file with optional compression
        # For now, just do conversation-only
        return self._compact_conversation_only(data)

    def _calculate_duration(self, data: Dict) -> float:
        """Calculate session duration from events"""
        events = data.get('terminal_recording', {}).get('events', [])
        if not events:
            return 0.0
        return events[-1][0]  # Last event timestamp

    def _extract_audit_frames(self, data: Dict) -> list:
        """
        Extract key audit frames from events

        Audit frames capture important moments:
        - User input lines (not individual keystrokes)
        - Assistant response starts
        - Errors or warnings
        - Command executions
        """
        frames = []
        conversation = data.get('conversation', [])

        # Convert conversation messages into audit frames
        for msg in conversation:
            frames.append({
                'timestamp': msg.get('timestamp', 0),
                'role': msg.get('role'),
                'content_preview': msg.get('content', '')[:200],  # First 200 chars
                'content_length': len(msg.get('content', ''))
            })

        return frames


def auto_compact(session_path: Path, mode: str = 'conversation') -> Dict[str, Any]:
    """
    Auto-compact a session file using specified mode

    Args:
        session_path: Path to .devsession file
        mode: Compaction mode (conversation/audit/lossless/none)

    Returns:
        Stats dict
    """
    compactor = SessionCompactor(mode)
    return compactor.compact(session_path)


def should_compact(session_path: Path, ratio_threshold: int = 50) -> bool:
    """
    Determine if a session should be compacted based on size ratio

    Args:
        session_path: Path to .devsession file
        ratio_threshold: Compact if events_bytes/conversation_bytes > this

    Returns:
        True if compaction recommended
    """
    try:
        with open(session_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        events = data.get('terminal_recording', {}).get('events', [])
        conversation = data.get('conversation', [])

        if not conversation:
            # No conversation parsed, don't compact
            return False

        events_bytes = len(json.dumps(events))
        convo_bytes = len(json.dumps(conversation))

        if convo_bytes == 0:
            return False

        ratio = events_bytes / convo_bytes
        return ratio > ratio_threshold

    except Exception:
        return False
