"""
DevSession - .devsession file format manager
Handles reading, writing, and managing .devsession files
"""

import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any


class DevSession:
    """Manages .devsession file format"""

    FORMAT_VERSION = "1.0"

    def __init__(self, session_id: Optional[str] = None):
        """
        Initialize a new .devsession

        Args:
            session_id: Optional session identifier (auto-generated if None)
        """
        self.session_id = session_id or f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.created = datetime.now().isoformat()
        self.updated = self.created

        # Terminal recording layer
        self.terminal_recording = {
            "version": 2,
            "width": 80,
            "height": 24,
            "shell": None,
            "events": []
        }

        # Conversation layer (parsed from terminal events)
        self.conversation = []

        # Summary layer (AI-generated)
        self.summary = None

        # Vector index (for semantic search)
        self.vector_index = None

        # Token counts (for context monitoring)
        self.token_counts = {
            "conversation": 0,
            "terminal_output": 0,
            "summary": 0,
            "total": 0,
            "last_updated": None
        }

        # Checksums for event integrity
        self.checksums = {}

        # Compaction history
        self.compaction_history = []

    def append_event(self, timestamp: float, event_type: str, data: str) -> str:
        """
        Append a terminal event

        Args:
            timestamp: Time in seconds since recording started
            event_type: Event type ("o" = output, "i" = input, "r" = resize)
            data: Event data (text or resize dimensions)

        Returns:
            Event hash for reference
        """
        event = [timestamp, event_type, data]
        self.terminal_recording["events"].append(event)

        # Generate event hash for checksums
        event_hash = self._hash_event(event)
        self.checksums[event_hash] = True

        return event_hash

    def set_terminal_info(self, width: int, height: int, shell: str):
        """Set terminal metadata"""
        self.terminal_recording["width"] = width
        self.terminal_recording["height"] = height
        self.terminal_recording["shell"] = shell

    def _hash_event(self, event: List) -> str:
        """Generate blake2b hash for a single event"""
        event_str = json.dumps(event, sort_keys=True)
        return hashlib.blake2b(event_str.encode(), digest_size=16).hexdigest()

    def _hash_data(self, data: Any) -> str:
        """Generate blake2b hash for any data structure"""
        data_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.blake2b(data_str.encode('utf-8'), digest_size=32).hexdigest()

    def _calculate_checksums(self) -> Dict[str, str]:
        """Calculate checksums for all major data structures"""
        checksums = {}

        # Hash of all terminal events
        if self.terminal_recording["events"]:
            checksums["events"] = self._hash_data(self.terminal_recording["events"])

        # Hash of conversation (if exists)
        if self.conversation:
            checksums["conversation"] = self._hash_data(self.conversation)

        # Hash of summary (if exists)
        if self.summary:
            checksums["summary"] = self._hash_data(self.summary)

        # Hash of vector index (if exists)
        if self.vector_index:
            checksums["vector_index"] = self._hash_data(self.vector_index)

        return checksums

    def verify_checksums(self) -> bool:
        """
        Verify data integrity using stored checksums

        Returns:
            True if all checksums match, False if corrupted
        """
        if not self.checksums:
            # No checksums stored (old file format or not saved yet)
            return True

        # Calculate current checksums
        current = self._calculate_checksums()

        # Compare each checksum
        for key in current:
            if key in self.checksums:
                if current[key] != self.checksums[key]:
                    print(f"⚠️  Checksum mismatch for {key}: data may be corrupted")
                    return False

        return True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        # Calculate fresh checksums before saving
        self.checksums = self._calculate_checksums()

        return {
            "format": "devsession",
            "version": self.FORMAT_VERSION,
            "session_id": self.session_id,
            "created": self.created,
            "updated": datetime.now().isoformat(),

            "terminal_recording": self.terminal_recording,
            "conversation": self.conversation,
            "summary": self.summary,
            "vector_index": self.vector_index,
            "token_counts": self.token_counts,
            "checksums": self.checksums,
            "compaction_history": self.compaction_history
        }

    def save(self, path: Path) -> None:
        """
        Save .devsession file

        Args:
            path: Output file path (.devsession extension)
        """
        path = Path(path)

        # Ensure .devsession extension
        if path.suffix != '.devsession':
            path = path.with_suffix('.devsession')

        # Create parent directory if needed
        path.parent.mkdir(parents=True, exist_ok=True)

        # Write JSON
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def _validate_schema(cls, data: Dict[str, Any]) -> None:
        """
        Validate .devsession file schema

        Args:
            data: Parsed JSON data

        Raises:
            ValueError: If schema validation fails
        """
        # Required top-level fields
        required_fields = ["format", "version", "terminal_recording"]
        for field in required_fields:
            if field not in data:
                raise ValueError(f"Missing required field: {field}")

        # Validate format
        if data["format"] != "devsession":
            raise ValueError(f"Invalid format: {data['format']} (expected 'devsession')")

        # Validate version format (e.g., "1.0")
        version = data["version"]
        if not isinstance(version, str):
            raise ValueError(f"Invalid version type: {type(version)} (expected string)")

        # Validate terminal_recording structure
        terminal = data["terminal_recording"]
        if not isinstance(terminal, dict):
            raise ValueError(f"terminal_recording must be object, got {type(terminal)}")

        if "events" not in terminal:
            raise ValueError("terminal_recording missing 'events' field")

        events = terminal["events"]
        if not isinstance(events, list):
            raise ValueError(f"terminal_recording.events must be array, got {type(events)}")

        # Validate event structure (sample first few events)
        for i, event in enumerate(events[:10]):  # Check first 10 events
            if not isinstance(event, list):
                raise ValueError(f"Event {i} must be array, got {type(event)}")
            if len(event) != 3:
                raise ValueError(f"Event {i} must have 3 elements, got {len(event)}")

            timestamp, event_type, data = event

            if not isinstance(timestamp, (int, float)):
                raise ValueError(f"Event {i} timestamp must be number, got {type(timestamp)}")

            if event_type not in ["o", "i", "r"]:
                raise ValueError(f"Event {i} type must be 'o', 'i', or 'r', got '{event_type}'")

            if not isinstance(data, str):
                raise ValueError(f"Event {i} data must be string, got {type(data)}")

        # Validate conversation (if present)
        if "conversation" in data and data["conversation"]:
            conversation = data["conversation"]
            if not isinstance(conversation, list):
                raise ValueError(f"conversation must be array, got {type(conversation)}")

            # Check first message structure
            if conversation and isinstance(conversation[0], dict):
                msg = conversation[0]
                if "role" not in msg or "content" not in msg:
                    raise ValueError("Conversation messages must have 'role' and 'content'")

    @classmethod
    def load(cls, path: Path, verify_checksums: bool = True) -> 'DevSession':
        """
        Load .devsession file

        Args:
            path: Path to .devsession file
            verify_checksums: Whether to verify data integrity (default: True)

        Returns:
            DevSession instance

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file format is invalid
        """
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f".devsession file not found: {path}")

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Validate schema
        cls._validate_schema(data)

        # Create instance
        session = cls(session_id=data.get("session_id"))
        session.created = data.get("created", session.created)
        session.updated = data.get("updated", session.updated)
        session.terminal_recording = data.get("terminal_recording", session.terminal_recording)
        session.conversation = data.get("conversation", [])
        session.summary = data.get("summary")
        session.vector_index = data.get("vector_index")
        session.token_counts = data.get("token_counts", session.token_counts)
        session.checksums = data.get("checksums", {})
        session.compaction_history = data.get("compaction_history", [])

        # Verify checksums
        if verify_checksums and not session.verify_checksums():
            raise ValueError(f"Checksum verification failed: {path} may be corrupted")

        return session

    def get_duration(self) -> float:
        """Get total recording duration in seconds"""
        if not self.terminal_recording["events"]:
            return 0.0

        last_event = self.terminal_recording["events"][-1]
        return last_event[0]  # timestamp is first element

    def get_event_count(self) -> int:
        """Get total number of events"""
        return len(self.terminal_recording["events"])

    def parse_conversation(self) -> List[Dict]:
        """
        Parse terminal events into conversation messages

        Returns:
            Conversation array with user/assistant messages
        """
        from .parser import parse_conversation
        return parse_conversation(self.terminal_recording["events"])

    def auto_parse_conversation(self) -> bool:
        """
        Automatically parse conversation if terminal events exist and conversation is empty

        Returns:
            True if conversation was parsed, False if skipped
        """
        # Only parse if we have events and no conversation yet
        if self.terminal_recording["events"] and not self.conversation:
            self.conversation = self.parse_conversation()
            return True
        return False

    def calculate_tokens(self, model: str = "claude-3-5-sonnet-20241022") -> Dict[str, int]:
        """
        Calculate token counts for all layers

        Args:
            model: Model name for token encoding

        Returns:
            Dict with token counts for each layer
        """
        from .tokens import TokenCounter

        counter = TokenCounter(model)

        counts = {
            "conversation": 0,
            "terminal_output": 0,
            "summary": 0,
            "total": 0,
            "last_updated": datetime.now().isoformat()
        }

        # Count conversation tokens
        if self.conversation:
            counts["conversation"] = counter.count_conversation(self.conversation)

        # Count terminal output tokens
        if self.terminal_recording.get("events"):
            counts["terminal_output"] = counter.count_terminal_output(
                self.terminal_recording["events"]
            )

        # Count summary tokens
        if self.summary:
            import json
            counts["summary"] = counter.count_text(json.dumps(self.summary))

        # Total is the max (we only send one layer at a time)
        counts["total"] = max(
            counts["conversation"],
            counts["terminal_output"],
            counts["summary"]
        )

        # Update stored counts
        self.token_counts = counts

        return counts

    def check_tokens(self, model: str = "claude-3-5-sonnet-20241022") -> Optional[str]:
        """
        Check token count and return warning if needed

        Args:
            model: Model name for token limit checking

        Returns:
            Warning message if approaching limit, None otherwise
        """
        from .tokens import TokenCounter

        # Calculate current tokens
        counts = self.calculate_tokens(model)
        counter = TokenCounter(model)

        # Check limit and format warning
        return counter.format_warning(counts["total"], model)

    def generate_summary(
        self,
        llm_client=None,
        redact_secrets: bool = True
    ) -> bool:
        """
        Generate AI summary from conversation

        Args:
            llm_client: Optional LLM client for summarization
            redact_secrets: Whether to redact secrets before summarization

        Returns:
            True if summary was generated successfully
        """
        from .summarizer import SessionSummarizer
        import hashlib

        # Check if we have a conversation to summarize
        if not self.conversation:
            # Try to parse from terminal events first
            if not self.auto_parse_conversation():
                print("⚠️  No conversation to summarize")
                return False

        # Calculate session hash for provenance
        session_str = json.dumps(self.conversation, sort_keys=True)
        session_hash = hashlib.blake2b(session_str.encode(), digest_size=16).hexdigest()

        # Create summarizer
        summarizer = SessionSummarizer(llm_client=llm_client)

        # Generate summary
        print(f"📝 Generating summary for {len(self.conversation)} messages...")
        self.summary = summarizer.summarize_session(
            self.conversation,
            session_hash=session_hash,
            redact_secrets=redact_secrets
        )

        print("✅ Summary generated successfully")
        return True

    def pin_summary_item(self, item_id: str) -> bool:
        """
        Pin a summary item (prevents auto-deletion during compaction)

        Args:
            item_id: ID of item to pin (e.g., "dec_7a1e...")

        Returns:
            True if item was found and pinned
        """
        from .summary_schema import add_audit_entry

        if not self.summary:
            return False

        # Search for item in all categories
        categories = ["decisions", "code_changes", "problems_solved", "open_issues", "next_steps"]
        for category in categories:
            for item in self.summary.get(category, []):
                if item.get("id") == item_id:
                    item["pinned"] = True
                    add_audit_entry(self.summary, "pin", item_id)
                    return True

        return False

    def unpin_summary_item(self, item_id: str) -> bool:
        """Unpin a summary item"""
        from .summary_schema import add_audit_entry

        if not self.summary:
            return False

        categories = ["decisions", "code_changes", "problems_solved", "open_issues", "next_steps"]
        for category in categories:
            for item in self.summary.get(category, []):
                if item.get("id") == item_id:
                    item["pinned"] = False
                    add_audit_entry(self.summary, "unpin", item_id)
                    return True

        return False

    def lock_summary_item(self, item_id: str) -> bool:
        """
        Lock a summary item (prevents auto-edits during re-summarization)

        Args:
            item_id: ID of item to lock

        Returns:
            True if item was found and locked
        """
        from .summary_schema import add_audit_entry

        if not self.summary:
            return False

        categories = ["decisions", "code_changes", "problems_solved", "open_issues", "next_steps"]
        for category in categories:
            for item in self.summary.get(category, []):
                if item.get("id") == item_id:
                    item["locked"] = True
                    add_audit_entry(self.summary, "lock", item_id)
                    return True

        return False

    def unlock_summary_item(self, item_id: str) -> bool:
        """Unlock a summary item"""
        from .summary_schema import add_audit_entry

        if not self.summary:
            return False

        categories = ["decisions", "code_changes", "problems_solved", "open_issues", "next_steps"]
        for category in categories:
            for item in self.summary.get(category, []):
                if item.get("id") == item_id:
                    item["locked"] = False
                    add_audit_entry(self.summary, "unlock", item_id)
                    return True

        return False

    def __repr__(self) -> str:
        duration = self.get_duration()
        event_count = self.get_event_count()
        return f"<DevSession {self.session_id} | {duration:.1f}s | {event_count} events>"
