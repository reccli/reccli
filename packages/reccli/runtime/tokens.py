"""
Token Counting - Track context size and prevent hitting limits
Supports Claude and GPT models using tiktoken
"""

import json
from typing import List, Dict, Optional, Tuple
from pathlib import Path


class TokenCounter:
    """Count tokens for conversations and terminal output"""

    # Token limits for common models
    MODEL_LIMITS = {
        # Claude models
        "claude-3-5-sonnet-20241022": 200_000,
        "claude-3-5-haiku-20241022": 200_000,
        "claude-3-opus-20240229": 200_000,
        "claude-sonnet-4": 200_000,
        "claude-opus-4": 200_000,

        # GPT models
        "gpt-5": 128_000,
        "gpt-4-turbo": 128_000,
        "gpt-4": 8_192,
        "gpt-3.5-turbo": 16_385,
    }

    # Warning thresholds
    WARNING_THRESHOLD = 0.90  # 90%
    CRITICAL_THRESHOLD = 0.95  # 95%

    def __init__(self, model: str = "claude-3-5-sonnet-20241022"):
        """
        Initialize token counter

        Args:
            model: Model name for token encoding
        """
        self.model = model
        self.encoder = None
        self._init_encoder()

    def _init_encoder(self):
        """Initialize tiktoken encoder"""
        try:
            import tiktoken

            # Map model names to tiktoken encodings
            # Claude uses the same tokenizer as GPT-4
            if "claude" in self.model.lower():
                self.encoder = tiktoken.encoding_for_model("gpt-4")
            elif "gpt-5" in self.model.lower():
                # GPT-5 uses same encoding as GPT-4 (as of Jan 2025)
                self.encoder = tiktoken.encoding_for_model("gpt-4")
            else:
                # Try to get encoding directly
                self.encoder = tiktoken.encoding_for_model(self.model)

        except ImportError:
            print("⚠️  tiktoken not installed - token counting disabled")
            print("   Install with: pip install tiktoken")
            self.encoder = None
        except Exception as e:
            print(f"⚠️  Could not initialize token encoder: {e}")
            self.encoder = None

    def count_text(self, text: str) -> int:
        """
        Count tokens in a text string

        Args:
            text: Text to count

        Returns:
            Number of tokens (0 if encoder not available)
        """
        if not self.encoder:
            # Fallback: rough estimate (1 token ≈ 4 characters)
            return len(text) // 4

        try:
            tokens = self.encoder.encode(text)
            return len(tokens)
        except Exception as e:
            print(f"⚠️  Error counting tokens: {e}")
            return len(text) // 4

    def count_message(self, message: Dict) -> int:
        """
        Count tokens in a single message

        Args:
            message: Message dict with 'role' and 'content'

        Returns:
            Number of tokens
        """
        if message.get("deleted"):
            return 0

        # Format: {"role": "user", "content": "text"}
        # Count both role and content
        role = message.get("role", "")
        content = message.get("content", "")

        # Add overhead for message formatting (typically 3-4 tokens)
        overhead = 4

        return self.count_text(role) + self.count_text(content) + overhead

    def count_conversation(self, conversation: List[Dict]) -> int:
        """
        Count tokens in entire conversation

        Args:
            conversation: List of message dicts

        Returns:
            Total number of tokens
        """
        total = 0
        for message in conversation:
            total += self.count_message(message)

        # Add overhead for conversation structure (typically 3 tokens)
        return total + 3

    def count_terminal_output(self, events: List[List]) -> int:
        """
        Count tokens in terminal recording events

        Args:
            events: Terminal events array [[timestamp, type, data], ...]

        Returns:
            Estimated number of tokens
        """
        # Combine all output events
        text = ""
        for event in events:
            if len(event) >= 3 and event[1] == "o":  # output event
                text += event[2]

        return self.count_text(text)

    def get_limit(self, model: Optional[str] = None) -> int:
        """
        Get token limit for a model

        Args:
            model: Model name (uses self.model if None)

        Returns:
            Token limit for the model
        """
        model = model or self.model

        # Try exact match
        if model in self.MODEL_LIMITS:
            return self.MODEL_LIMITS[model]

        # Try partial match (e.g., "claude-3" matches "claude-3-opus-20240229")
        for key in self.MODEL_LIMITS:
            if model.lower() in key.lower() or key.lower() in model.lower():
                return self.MODEL_LIMITS[key]

        # Default to Claude Sonnet limit
        return 200_000

    def check_limit(self, token_count: int, model: Optional[str] = None) -> Tuple[str, float]:
        """
        Check if token count is approaching limit

        Args:
            token_count: Current token count
            model: Model name (uses self.model if None)

        Returns:
            Tuple of (status, percentage) where status is:
            - "ok" - Below warning threshold
            - "warning" - Above 90% (WARNING_THRESHOLD)
            - "critical" - Above 95% (CRITICAL_THRESHOLD)
        """
        limit = self.get_limit(model)
        percentage = token_count / limit

        if percentage >= self.CRITICAL_THRESHOLD:
            return ("critical", percentage)
        elif percentage >= self.WARNING_THRESHOLD:
            return ("warning", percentage)
        else:
            return ("ok", percentage)

    def format_warning(self, token_count: int, model: Optional[str] = None) -> Optional[str]:
        """
        Format a warning message if needed

        Args:
            token_count: Current token count
            model: Model name (uses self.model if None)

        Returns:
            Warning message string, or None if no warning needed
        """
        status, percentage = self.check_limit(token_count, model)
        limit = self.get_limit(model)

        if status == "critical":
            return (
                f"🚨 CRITICAL: {token_count:,} / {limit:,} tokens ({percentage:.1%})\n"
                f"   Context is nearly full! Consider compacting or starting new session."
            )
        elif status == "warning":
            return (
                f"⚠️  WARNING: {token_count:,} / {limit:,} tokens ({percentage:.1%})\n"
                f"   Approaching context limit. Consider compacting soon."
            )

        return None


def count_devsession_tokens(devsession_path: Path, model: str = "claude-3-5-sonnet-20241022") -> Dict[str, int]:
    """
    Count tokens in a .devsession file

    Args:
        devsession_path: Path to .devsession file
        model: Model name for token encoding

    Returns:
        Dict with token counts:
        {
            "conversation": int,
            "terminal_output": int,
            "summary": int,
            "total": int
        }
    """
    from ..devsession import DevSession

    counter = TokenCounter(model)
    session = DevSession.load(devsession_path)

    counts = {
        "conversation": 0,
        "terminal_output": 0,
        "summary": 0,
        "total": 0
    }

    # Count conversation tokens
    if session.conversation:
        counts["conversation"] = counter.count_conversation(session.conversation)

    # Count terminal output tokens
    if session.terminal_recording.get("events"):
        counts["terminal_output"] = counter.count_terminal_output(
            session.terminal_recording["events"]
        )

    # Count summary tokens
    if session.summary:
        counts["summary"] = counter.count_text(json.dumps(session.summary))

    # Total is the max (we only send one layer at a time)
    counts["total"] = max(
        counts["conversation"],
        counts["terminal_output"],
        counts["summary"]
    )

    return counts
