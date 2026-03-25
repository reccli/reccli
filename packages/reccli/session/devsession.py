"""
DevSession - .devsession file format manager
Handles reading, writing, and managing .devsession files
"""

import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from ..project.devproject import default_devsession_path


class DevSession:
    """Manages .devsession file format"""

    FORMAT_VERSION = "1.1.0"

    def __init__(self, session_id: Optional[str] = None):
        """
        Initialize a new .devsession

        Args:
            session_id: Optional session identifier (auto-generated if None)
        """
        self.session_id = session_id or f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.created = datetime.now().isoformat()
        self.updated = self.created
        self.metadata = {
            "session_id": self.session_id,
            "created_at": self.created,
            "updated_at": self.updated,
        }

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

        # Semantic spans linking summary items back to conversation ranges
        self.spans = []

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

        # Summary synchronization frontier for rolling compaction/update flows
        self.summary_sync = {
            "status": "not_started",
            "last_synced_msg_id": None,
            "last_synced_msg_index": None,
            "updated_at": None,
            "pending_messages": 0,
            "pending_tokens": 0,
        }

        # Embeddings can remain inline for prototyping or be externalized to a sidecar
        self.embedding_storage = {
            "mode": "inline",
            "messages_file": None,
            "format": None,
            "external_message_count": 0,
            "loaded": True,
        }

        # Artifacts (optional files/resources referenced in the session)
        self.artifacts = {}

        # Checksums for event integrity
        self.checksums = {}

        # Compaction history
        self.compaction_history = []

        self.episodes = []
        self.current_episode_id = None
        self.checkpoints = []
        self.path: Optional[Path] = None

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

        # Hash of spans (if exists)
        if self.spans:
            checksums["spans"] = self._hash_data(self.spans)

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
        now = datetime.now().isoformat()
        self.updated = now
        self.metadata["session_id"] = self.session_id
        self.metadata["created_at"] = self.created
        self.metadata["updated_at"] = now
        self.refresh_summary_sync()
        self.checksums = self._calculate_checksums()

        result = {
            # Spec-required root fields
            "format": "devsession",
            "version": self.FORMAT_VERSION,
            "metadata": self.metadata,
            "conversation": self.conversation,

            # Spec-optional layers
            "summary": self.summary,
            "spans": self.spans,
            "vector_index": self.vector_index,
            "summary_sync": self.summary_sync,
            "embedding_storage": self.embedding_storage,
            "artifacts": self.artifacts,

            # Implementation extensions (not yet in spec)
            "terminal_recording": self.terminal_recording,
            "token_counts": self.token_counts,
            "checksums": self.checksums,
            "compaction_history": self.compaction_history,
            "checkpoints": self.checkpoints,
            "episodes": self.episodes,
            "current_episode_id": self.current_episode_id,
        }
        return result

    def save(self, path: Optional[Path] = None, skip_validation: bool = False) -> None:
        """
        Save .devsession file with validation

        Args:
            path: Output file path (.devsession extension). If omitted, uses the
                path the session was loaded from or last saved to.
            skip_validation: Skip validation (use with caution)

        Raises:
            ValueError: If validation fails and auto-fix cannot repair
        """
        if path is None:
            if self.path is None:
                base_dir = None
                if self.metadata.get("project_root"):
                    base_dir = Path(self.metadata["project_root"])
                elif self.metadata.get("working_directory"):
                    base_dir = Path(self.metadata["working_directory"])
                path = default_devsession_path(base_dir)
            else:
                path = self.path

        path = Path(path)

        # Ensure .devsession extension (but don't add if already present or if it's a .tmp file)
        path_str = str(path)
        if not (path_str.endswith('.devsession') or path_str.endswith('.devsession.tmp')):
            path = path.with_suffix('.devsession')

        self.path = path

        # Validate summary before writing (Safeguard #7)
        if not skip_validation and self.summary and self.conversation:
            from ..summarization.summary_verification import SummaryVerifier
            from ..summarization.summary_schema import ensure_summary_span_links
            from .reindexing import tag_messages_with_ids

            # Ensure messages have IDs (needed for validation)
            tag_messages_with_ids(self.conversation)
            self.spans = ensure_summary_span_links(self.summary, self.spans)

            verifier = SummaryVerifier(self.conversation, self.spans)
            is_valid, errors = verifier.verify_summary(self.summary)

            if not is_valid:
                # Try auto-fix
                print("⚠️  Summary validation failed before save:")
                for category, category_errors in errors.items():
                    if category_errors:
                        print(f"  {category}:")
                        for err in category_errors:
                            print(f"    - {err}")

                fixed_summary, warnings = verifier.auto_fix_summary(self.summary)

                if warnings:
                    print("🔧 Auto-fixing summary:")
                    for warning in warnings:
                        print(f"  - {warning}")

                # Validate fixed summary
                is_valid_after_fix, errors_after_fix = verifier.verify_summary(fixed_summary)

                if is_valid_after_fix:
                    print("✅ Summary fixed successfully")
                    self.summary = fixed_summary
                else:
                    raise ValueError(
                        f"Cannot save: Summary validation failed even after auto-fix. "
                        f"Errors: {errors_after_fix}. "
                        f"Use skip_validation=True to force save (not recommended)."
                    )

        # Create parent directory if needed
        path.parent.mkdir(parents=True, exist_ok=True)

        # Write JSON
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def _validate_schema(cls, data: Dict[str, Any]) -> None:
        """
        Validate .devsession file schema per DEVSESSION_FORMAT.md spec v1.1.0.

        Args:
            data: Parsed JSON data

        Raises:
            ValueError: If schema validation fails
        """
        # Required root fields
        for field in ["format", "version", "terminal_recording"]:
            if field not in data:
                raise ValueError(f"Missing required field: {field}")

        if data["format"] != "devsession":
            raise ValueError(f"Invalid format: {data['format']} (expected 'devsession')")

        version = data["version"]
        if not isinstance(version, str):
            raise ValueError(f"Invalid version type: {type(version)} (expected string)")

        # Validate metadata (spec-required, but lenient for backwards compat)
        if "metadata" in data and data["metadata"] is not None:
            if not isinstance(data["metadata"], dict):
                raise ValueError(f"metadata must be object, got {type(data['metadata'])}")

        # Validate conversation (spec-required array)
        if "conversation" in data and data["conversation"]:
            conversation = data["conversation"]
            if not isinstance(conversation, list):
                raise ValueError(f"conversation must be array, got {type(conversation)}")
            # Check first message structure: spec requires role + content
            if conversation and isinstance(conversation[0], dict):
                msg = conversation[0]
                if "role" not in msg or "content" not in msg:
                    raise ValueError("Conversation messages must have 'role' and 'content'")

        # Validate terminal_recording structure (required)
        terminal = data["terminal_recording"]
        if not isinstance(terminal, dict):
            raise ValueError(f"terminal_recording must be object, got {type(terminal)}")
        if "events" not in terminal:
            raise ValueError("terminal_recording missing 'events' field")
        events = terminal["events"]
        if not isinstance(events, list):
            raise ValueError(f"terminal_recording.events must be array, got {type(events)}")
        for i, event in enumerate(events[:10]):
            if not isinstance(event, list):
                raise ValueError(f"Event {i} must be array, got {type(event)}")
            if len(event) != 3:
                raise ValueError(f"Event {i} must have 3 elements, got {len(event)}")
            timestamp, event_type, evt_data = event
            if not isinstance(timestamp, (int, float)):
                raise ValueError(f"Event {i} timestamp must be number, got {type(timestamp)}")
            if event_type not in ["o", "i", "r"]:
                raise ValueError(f"Event {i} type must be 'o', 'i', or 'r', got '{event_type}'")
            if not isinstance(evt_data, str):
                raise ValueError(f"Event {i} data must be string, got {type(evt_data)}")

        # Validate other optional structures
        if "spans" in data and data["spans"] is not None and not isinstance(data["spans"], list):
            raise ValueError(f"spans must be array, got {type(data['spans'])}")
        if "summary_sync" in data and data["summary_sync"] is not None and not isinstance(data["summary_sync"], dict):
            raise ValueError(f"summary_sync must be object, got {type(data['summary_sync'])}")
        if "embedding_storage" in data and data["embedding_storage"] is not None and not isinstance(data["embedding_storage"], dict):
            raise ValueError(f"embedding_storage must be object, got {type(data['embedding_storage'])}")

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

        # Create instance — read identity from metadata first, root-level as fallback
        # for backwards compatibility with pre-1.1.0 files
        metadata = data.get("metadata") or {}
        session_id = metadata.get("session_id") or data.get("session_id")
        session = cls(session_id=session_id)
        session.created = metadata.get("created_at") or data.get("created", session.created)
        session.updated = metadata.get("updated_at") or data.get("updated", session.updated)
        session.metadata = metadata

        # Spec layers
        session.conversation = data.get("conversation", [])
        session.summary = data.get("summary")
        session.spans = data.get("spans", [])
        session.vector_index = data.get("vector_index")
        session.summary_sync = data.get("summary_sync", session.summary_sync)
        session.embedding_storage = data.get("embedding_storage", session.embedding_storage)
        session.artifacts = data.get("artifacts", {})

        # Implementation extensions
        session.terminal_recording = data.get("terminal_recording", session.terminal_recording)
        session.token_counts = data.get("token_counts", session.token_counts)
        session.checksums = data.get("checksums", {})
        session.compaction_history = data.get("compaction_history", [])
        session.checkpoints = data.get("checkpoints", [])
        session.episodes = data.get("episodes", [])
        session.current_episode_id = data.get("current_episode_id")
        session.path = path

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

    def _resolve_message_index(self, message_id: str) -> Optional[int]:
        for idx, msg in enumerate(self.conversation):
            if msg.get("_message_id") == message_id or msg.get("id") == message_id or msg.get("_id") == message_id:
                return idx
        return None

    def refresh_summary_sync(self) -> Dict[str, Any]:
        """
        Update the summary frontier metadata.

        The frontier marks the last message safely represented in summary/span form.
        It enables rolling summary updates without re-summarizing the entire session.
        """
        frontier_end = None

        if self.spans:
            frontier_end = max(
                (
                    span.get("end_index")
                    for span in self.spans
                    if span.get("status", "closed") != "open" and isinstance(span.get("end_index"), int)
                ),
                default=None,
            )

        if frontier_end is None and self.summary:
            categories = ["decisions", "code_changes", "problems_solved", "open_issues", "next_steps"]
            frontier_end = max(
                (
                    item.get("message_range", {}).get("end_index")
                    for category in categories
                    for item in self.summary.get(category, [])
                    if isinstance(item.get("message_range", {}).get("end_index"), int)
                ),
                default=None,
            )

        if frontier_end is None:
            self.summary_sync = {
                "status": "not_started" if not self.summary else "pending",
                "last_synced_msg_id": None,
                "last_synced_msg_index": None,
                "updated_at": datetime.now().isoformat(),
                "pending_messages": sum(1 for msg in self.conversation if not msg.get("deleted")),
                "pending_tokens": self.get_pending_token_count(0),
            }
            return self.summary_sync

        frontier_end = max(0, min(frontier_end, len(self.conversation)))
        last_synced_idx = frontier_end - 1 if frontier_end > 0 else None
        last_synced_id = None
        if last_synced_idx is not None and last_synced_idx < len(self.conversation):
            msg = self.conversation[last_synced_idx]
            last_synced_id = msg.get("_message_id") or msg.get("id") or msg.get("_id") or f"msg_{last_synced_idx + 1:03d}"

        pending_messages = sum(
            1
            for idx, msg in enumerate(self.conversation)
            if idx >= frontier_end and not msg.get("deleted")
        )
        status = "synced" if pending_messages == 0 else "pending"
        self.summary_sync = {
            "status": status,
            "last_synced_msg_id": last_synced_id,
            "last_synced_msg_index": last_synced_idx,
            "updated_at": datetime.now().isoformat(),
            "pending_messages": pending_messages,
            "pending_tokens": self.get_pending_token_count(frontier_end),
        }
        return self.summary_sync

    def get_summary_frontier_index(self) -> int:
        """Return the exclusive frontier index already represented in closed summary/span form."""
        self.refresh_summary_sync()
        last_synced = self.summary_sync.get("last_synced_msg_index")
        if isinstance(last_synced, int):
            return last_synced + 1
        return 0

    def get_pending_conversation(self, start_index: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return non-deleted messages beyond the current summary frontier."""
        frontier = self.get_summary_frontier_index() if start_index is None else start_index
        return [
            msg for idx, msg in enumerate(self.conversation)
            if idx >= frontier and not msg.get("deleted")
        ]

    def get_pending_token_count(self, start_index: Optional[int] = None, model: str = "claude-3-5-sonnet-20241022") -> int:
        """Estimate token count for uncompacted messages beyond the frontier."""
        from ..runtime.tokens import TokenCounter

        frontier = self.get_summary_frontier_index() if start_index is None else start_index
        pending = [
            msg for idx, msg in enumerate(self.conversation)
            if idx >= frontier and not msg.get("deleted")
        ]
        if not pending:
            return 0
        return TokenCounter(model).count_conversation(pending)

    def replace_open_tail_span(self, start_index: int, topic: str = "active conversation tail") -> Optional[Dict[str, Any]]:
        """
        Replace any existing open tail span with a new one starting at `start_index`.
        Returns the new open span or None if no tail exists.
        """
        from ..summarization.summary_schema import create_span, sort_spans

        self.spans = [span for span in self.spans if span.get("status") != "open"]

        if start_index >= len(self.conversation):
            self.spans = sort_spans(self.spans)
            self.refresh_summary_sync()
            return None

        start_msg = self.conversation[start_index]
        latest_index = len(self.conversation) - 1
        latest_msg = self.conversation[latest_index]
        open_span = create_span(
            kind="active_context",
            start_message_id=start_msg.get("_message_id") or start_msg.get("id") or start_msg.get("_id") or f"msg_{start_index + 1:03d}",
            start_index=start_index,
            topic=topic,
            status="open",
            latest_message_id=latest_msg.get("_message_id") or latest_msg.get("id") or latest_msg.get("_id") or f"msg_{latest_index + 1:03d}",
            latest_index=latest_index,
            t_first=start_msg.get("timestamp"),
            t_last=latest_msg.get("timestamp"),
        )
        self.spans.append(open_span)
        self.spans = sort_spans(self.spans)
        self.refresh_summary_sync()
        return open_span

    def tombstone_message(self, message_id: str, reason: str = "redacted") -> bool:
        """
        Tombstone a message instead of deleting it.

        This preserves array indices and message IDs so span and summary references
        remain stable even when content must be removed from active use.
        """
        idx = self._resolve_message_index(message_id)
        if idx is None:
            return False

        message = self.conversation[idx]
        if message.get("deleted"):
            return True

        content = message.get("content", "")
        if content:
            message["content_hash_before_delete"] = hashlib.blake2b(
                content.encode("utf-8"), digest_size=16
            ).hexdigest()
        message["content"] = "[TOMBSTONED]"
        message["deleted"] = True
        message["deleted_at"] = datetime.now().isoformat()
        message["delete_reason"] = reason
        message.pop("embedding", None)
        message.pop("embedding_ref", None)
        self.refresh_summary_sync()
        return True

    def redact_message(self, message_id: str, redacted_content: str, reason: str = "manual_redaction") -> bool:
        """
        Redact a message in place without deleting its identity.
        """
        idx = self._resolve_message_index(message_id)
        if idx is None:
            return False

        message = self.conversation[idx]
        original = message.get("content", "")
        message["content"] = redacted_content
        message["redacted"] = True
        message["redacted_at"] = datetime.now().isoformat()
        message["redaction_reason"] = reason
        if original:
            message["content_hash_before_redaction"] = hashlib.blake2b(
                original.encode("utf-8"), digest_size=16
            ).hexdigest()
        message.pop("embedding", None)
        message.pop("embedding_ref", None)
        self.refresh_summary_sync()
        return True

    def parse_conversation(self) -> List[Dict]:
        """
        Parse terminal events into conversation messages

        Returns:
            Conversation array with user/assistant messages
        """
        from ..recording.parser import parse_conversation
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
        from ..runtime.tokens import TokenCounter

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
        from ..runtime.tokens import TokenCounter

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
        from ..summarization.summarizer import SessionSummarizer
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

        # Auto-create LLM client: try Anthropic first, fall back to OpenAI
        model = None
        if llm_client is None:
            from ..runtime.config import Config
            config = Config()

            # Try Anthropic
            try:
                import anthropic
                api_key = config.get_api_key("anthropic")
                if api_key:
                    llm_client = anthropic.Anthropic(api_key=api_key)
                    model = "claude-sonnet-4-6"
            except Exception:
                pass

            # Fall back to OpenAI
            if llm_client is None:
                try:
                    from openai import OpenAI
                    api_key = config.get_api_key("openai")
                    if api_key:
                        llm_client = OpenAI(api_key=api_key)
                        model = "gpt-5.4"
                except Exception:
                    pass

        # Create summarizer
        kwargs = {"llm_client": llm_client}
        if model:
            kwargs["model"] = model
        summarizer = SessionSummarizer(**kwargs)

        # Generate summary
        print(f"📝 Generating summary for {len(self.conversation)} messages...")
        self.summary = summarizer.summarize_session(
            self.conversation,
            session_hash=session_hash,
            redact_secrets=redact_secrets
        )
        from ..summarization.summary_schema import ensure_summary_span_links

        self.spans = ensure_summary_span_links(self.summary, self.spans)
        self.refresh_summary_sync()

        print("✅ Summary generated successfully")
        return True

    def generate_embeddings(self, provider=None, force: bool = False, storage_mode: Optional[str] = None) -> int:
        """
        Generate embeddings for all messages in conversation

        Args:
            provider: EmbeddingProvider instance (or None to use default)
            force: Force re-embedding even if embeddings exist

        Returns:
            Number of messages embedded

        Example:
            session.generate_embeddings()  # Use default OpenAI
            session.generate_embeddings(LocalEmbeddings())  # Use local model
        """
        from ..retrieval.embeddings import get_embedding_provider
        from datetime import datetime

        if not self.conversation:
            print("⚠️  No conversation to embed")
            return 0

        # Get provider
        if provider is None:
            provider = get_embedding_provider()

        # Check if already embedded with same model
        if not force:
            existing_model = None
            if self.conversation and len(self.conversation) > 0:
                first_msg = self.conversation[0]
                if 'embed_model' in first_msg:
                    existing_model = first_msg['embed_model']

            if existing_model == provider.model_name:
                print(f"⚠️  Embeddings already exist for model {existing_model}")
                print(f"   Use force=True to re-embed")
                return 0

        # Filter messages to embed (skip if already embedded and not forcing)
        messages_to_embed = []
        indices_to_embed = []

        for i, msg in enumerate(self.conversation):
            if msg.get("deleted"):
                continue
            if force or 'embedding' not in msg:
                messages_to_embed.append(msg)
                indices_to_embed.append(i)

        if not messages_to_embed:
            print("✓ All messages already embedded")
            return 0

        # Batch embed all messages
        texts = [msg['content'] for msg in messages_to_embed]
        print(f"🔮 Generating embeddings for {len(texts)} messages using {provider.model_name}...")

        try:
            embeddings = provider.embed_batch(texts)
        except Exception as e:
            print(f"❌ Embedding failed: {e}")
            return 0

        # Attach embeddings to messages
        embed_ts = datetime.now().isoformat()
        for msg, embedding in zip(messages_to_embed, embeddings):
            msg['embedding'] = embedding
            msg['embed_model'] = provider.model_name
            msg['embed_provider'] = provider.provider_name
            msg['embed_dim'] = provider.dimensions
            msg['embed_ts'] = embed_ts
            msg['text_hash'] = provider.compute_text_hash(msg['content'])

        # Embed spans
        span_count = self._embed_spans(provider, embed_ts, force)

        # Embed summary items
        summary_count = self._embed_summary_items(provider, embed_ts, force)

        requested_mode = storage_mode or self.embedding_storage.get("mode", "inline")
        if requested_mode == "external" and self.path is not None:
            self.externalize_message_embeddings()

        total = len(embeddings) + span_count + summary_count
        print(f"✓ Generated {total} embeddings ({provider.dimensions}D): {len(embeddings)} messages, {span_count} spans, {summary_count} summary items")
        return total

    def _embed_spans(self, provider, embed_ts: str, force: bool) -> int:
        """Embed semantic spans. Text = topic + kind + message range context."""
        if not self.spans:
            return 0

        to_embed = []
        for span in self.spans:
            if not force and 'embedding' in span:
                continue
            topic = span.get("topic", "")
            kind = span.get("kind", "")
            if not topic:
                continue
            # Compose text: topic + kind + a slice of the conversation it covers
            text = f"[{kind}] {topic}"
            start = span.get("start_index", 0)
            end = span.get("end_index", start + 1)
            msgs = self.conversation[start:min(end, start + 5)]
            for msg in msgs:
                role = msg.get("role", "")
                content = (msg.get("content") or "")[:150]
                text += f"\n{role}: {content}"
            to_embed.append((span, text))

        if not to_embed:
            return 0

        texts = [t for _, t in to_embed]
        try:
            embeddings = provider.embed_batch(texts)
        except Exception:
            return 0

        for (span, text), embedding in zip(to_embed, embeddings):
            span['embedding'] = embedding
            span['embed_model'] = provider.model_name
            span['embed_provider'] = provider.provider_name
            span['embed_dim'] = provider.dimensions
            span['embed_ts'] = embed_ts
            span['text_hash'] = provider.compute_text_hash(text)

        return len(embeddings)

    def _embed_summary_items(self, provider, embed_ts: str, force: bool) -> int:
        """Embed summary items (decisions, code changes, problems, issues, next steps)."""
        if not self.summary:
            return 0

        TEXT_COMPOSERS = {
            "decisions": lambda item: f"Decision: {item.get('decision', '')}. Reasoning: {item.get('reasoning', '')}",
            "code_changes": lambda item: f"Code change: {item.get('description', '')}. Files: {', '.join(item.get('files') or [])}",
            "problems_solved": lambda item: f"Problem: {item.get('problem', '')}. Solution: {item.get('solution', '')}",
            "open_issues": lambda item: f"Issue ({item.get('severity', 'medium')}): {item.get('issue', '')}",
            "next_steps": lambda item: f"Next step (priority {item.get('priority', '?')}): {item.get('action', '')}",
        }

        to_embed = []
        for category, composer in TEXT_COMPOSERS.items():
            for item in self.summary.get(category, []):
                if not force and 'embedding' in item:
                    continue
                text = composer(item)
                if len(text.strip()) < 10:
                    continue
                to_embed.append((item, text))

        if not to_embed:
            return 0

        texts = [t for _, t in to_embed]
        try:
            embeddings = provider.embed_batch(texts)
        except Exception:
            return 0

        for (item, text), embedding in zip(to_embed, embeddings):
            item['embedding'] = embedding
            item['embed_model'] = provider.model_name
            item['embed_provider'] = provider.provider_name
            item['embed_dim'] = provider.dimensions
            item['embed_ts'] = embed_ts
            item['text_hash'] = provider.compute_text_hash(text)

        return len(embeddings)

    def externalize_message_embeddings(self, sidecar_path: Optional[Path] = None) -> Optional[Path]:
        """
        Move inline message embeddings to a sidecar `.npy` file to keep `.devsession`
        JSON lightweight. Embeddings can be rehydrated on demand later.
        """
        if sidecar_path is None:
            if self.path is None:
                return None
            sidecar_path = self.path.with_suffix(".embeddings.npy")

        rows = []
        refs = []
        for idx, msg in enumerate(self.conversation):
            if msg.get("deleted"):
                msg.pop("embedding", None)
                msg.pop("embedding_ref", None)
                continue
            embedding = msg.get("embedding")
            if embedding:
                rows.append(embedding)
                refs.append(idx)

        if not rows:
            return None

        import numpy as np

        matrix = np.array(rows, dtype=np.float32)
        np.save(sidecar_path, matrix)

        for ref_index, msg_index in enumerate(refs):
            msg = self.conversation[msg_index]
            msg["embedding_ref"] = ref_index
            msg.pop("embedding", None)

        self.embedding_storage = {
            "mode": "external",
            "messages_file": str(sidecar_path.name if self.path else sidecar_path),
            "format": "npy",
            "external_message_count": len(refs),
            "loaded": False,
        }
        return sidecar_path

    def load_external_message_embeddings(self) -> int:
        """Hydrate message embeddings from sidecar storage on demand."""
        if self.embedding_storage.get("mode") != "external":
            return 0

        messages_file = self.embedding_storage.get("messages_file")
        if not messages_file:
            return 0

        path = Path(messages_file)
        if not path.is_absolute() and self.path is not None:
            path = self.path.parent / path
        if not path.exists():
            return 0

        import numpy as np

        matrix = np.load(path, mmap_mode="r")
        hydrated = 0
        for msg in self.conversation:
            ref = msg.get("embedding_ref")
            if msg.get("deleted"):
                continue
            if isinstance(ref, int) and 0 <= ref < len(matrix) and "embedding" not in msg:
                msg["embedding"] = matrix[ref].tolist()
                hydrated += 1

        self.embedding_storage["loaded"] = True
        return hydrated

    def pin_summary_item(self, item_id: str) -> bool:
        """
        Pin a summary item (prevents auto-deletion during compaction)

        Args:
            item_id: ID of item to pin (e.g., "dec_7a1e...")

        Returns:
            True if item was found and pinned
        """
        from ..summarization.summary_schema import add_audit_entry

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
        from ..summarization.summary_schema import add_audit_entry

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
        from ..summarization.summary_schema import add_audit_entry

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
        from ..summarization.summary_schema import add_audit_entry

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

    def start_episode(self, goal: str) -> str:
        # Close previous episode if open
        if self.current_episode_id and self.episodes:
            for ep in reversed(self.episodes):
                if ep.get('id') == self.current_episode_id and ep.get('end_index') is None:
                    ep['end_index'] = max(0, len(self.conversation) - 1) if self.conversation else -1
                    ep['ended_at'] = datetime.now().isoformat()
                    break

        # Compute next episode number
        next_num = 1
        for ep in self.episodes:
            eid = ep.get('id', '')
            if isinstance(eid, str) and eid.startswith('ep_'):
                try:
                    n = int(eid.split('_')[-1])
                    next_num = max(next_num, n + 1)
                except Exception:
                    pass

        eid = f"ep_{next_num:03d}"
        new_ep = {
            'id': eid,
            'goal': goal,
            'start_index': len(self.conversation) if self.conversation else 0,
            'end_index': None,
            'started_at': datetime.now().isoformat(),
        }
        self.episodes.append(new_ep)
        self.current_episode_id = eid
        return eid

    def get_episode_id_for_message_index(self, index: int) -> Optional[str]:
        if not isinstance(index, int) or index < 0:
            return None

        # Prefer explicit episodes with ranges
        for ep in self.episodes:
            start = ep.get('start_index', 0)
            end = ep.get('end_index')
            if end is None:
                if index >= start:
                    return ep.get('id')
            else:
                if start <= index <= end:
                    return ep.get('id')

        return None

    def __repr__(self) -> str:
        duration = self.get_duration()
        event_count = self.get_event_count()
        return f"<DevSession {self.session_id} | {duration:.1f}s | {event_count} events>"
