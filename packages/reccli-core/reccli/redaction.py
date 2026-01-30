"""
Redaction - Remove secrets and PII before summarization
Prevents leaking sensitive data into AI summaries
"""

import re
import math
from typing import Dict, List, Tuple, Optional


class SecretRedactor:
    """Redact secrets from conversation before summarization"""

    # Regex patterns for common secrets
    PATTERNS = {
        # API Keys and tokens
        "api_key": [
            r'(?i)(api[_-]?key|apikey|key)["\s:=]+([a-zA-Z0-9_\-]{20,})',
            r'(?i)(bearer|token)["\s:=]+([a-zA-Z0-9_\-\.]{20,})',
            r'sk-[a-zA-Z0-9]{20,}',  # OpenAI style
            r'ghp_[a-zA-Z0-9]{36,}',  # GitHub Personal Access Token
            r'gho_[a-zA-Z0-9]{36,}',  # GitHub OAuth Token
        ],

        # AWS credentials
        "aws": [
            r'AKIA[0-9A-Z]{16}',  # AWS Access Key
            r'(?i)(aws_secret_access_key)["\s:=]+([a-zA-Z0-9/+=]{40})',
        ],

        # Private keys
        "private_key": [
            r'-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----',
        ],

        # Passwords
        "password": [
            r'(?i)(password|passwd|pwd)["\s:=]+([^\s"\']{8,})',
        ],

        # JWTs
        "jwt": [
            r'eyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*',
        ],

        # Database URLs with credentials
        "db_url": [
            r'(postgres|mysql|mongodb)://[^:]+:[^@]+@[^\s]+',
        ],

        # Email addresses (PII)
        "email": [
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
        ],

        # Phone numbers (PII)
        "phone": [
            r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',  # US format
            r'\+\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}',  # International
        ],

        # Credit card numbers (PII)
        "credit_card": [
            r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b',
        ],

        # Social Security Numbers (PII)
        "ssn": [
            r'\b\d{3}-\d{2}-\d{4}\b',
        ],

        # IP addresses (potentially sensitive)
        "ip_address": [
            r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b',
        ],
    }

    # Entropy threshold for detecting random strings (likely secrets)
    ENTROPY_THRESHOLD = 4.5  # bits per character

    def __init__(self, redact_emails: bool = True, redact_ips: bool = False):
        """
        Initialize redactor

        Args:
            redact_emails: Whether to redact email addresses
            redact_ips: Whether to redact IP addresses (often not needed)
        """
        self.redact_emails = redact_emails
        self.redact_ips = redact_ips
        self.redaction_map = {}  # Original -> Redacted mapping

    def calculate_entropy(self, s: str) -> float:
        """
        Calculate Shannon entropy of a string

        Args:
            s: String to analyze

        Returns:
            Entropy in bits per character
        """
        if not s:
            return 0.0

        # Count character frequencies
        freq = {}
        for char in s:
            freq[char] = freq.get(char, 0) + 1

        # Calculate entropy
        entropy = 0.0
        length = len(s)
        for count in freq.values():
            p = count / length
            entropy -= p * math.log2(p)

        return entropy

    def is_likely_secret(self, s: str) -> bool:
        """
        Detect if string is likely a secret based on entropy

        Args:
            s: String to analyze

        Returns:
            True if likely a secret
        """
        # Must be reasonably long
        if len(s) < 16:
            return False

        # Check entropy
        entropy = self.calculate_entropy(s)
        return entropy >= self.ENTROPY_THRESHOLD

    def redact_text(self, text: str, context: str = "") -> Tuple[str, List[str]]:
        """
        Redact secrets from text

        Args:
            text: Text to redact
            context: Context label (for logging)

        Returns:
            (redacted_text, redaction_types)
        """
        redacted = text
        redaction_types = []

        # Apply pattern-based redaction
        for secret_type, patterns in self.PATTERNS.items():
            # Skip optional redactions
            if secret_type == "email" and not self.redact_emails:
                continue
            if secret_type == "ip_address" and not self.redact_ips:
                continue

            for pattern in patterns:
                matches = list(re.finditer(pattern, redacted))
                for match in matches:
                    original = match.group(0)
                    placeholder = f"[REDACTED_{secret_type.upper()}]"

                    # Store in map for potential rehydration
                    self.redaction_map[placeholder] = original

                    redacted = redacted.replace(original, placeholder)
                    redaction_types.append(secret_type)

        # Entropy-based detection for unknown secret formats
        # Split on whitespace and check each token
        words = redacted.split()
        for word in words:
            # Skip if already redacted
            if word.startswith("[REDACTED_"):
                continue

            # Skip common words/paths
            if word.startswith("/") or word.startswith(".") or word.startswith("http"):
                continue

            # Check entropy
            if self.is_likely_secret(word):
                placeholder = "[REDACTED_HIGH_ENTROPY]"
                self.redaction_map[placeholder] = word
                redacted = redacted.replace(word, placeholder)
                redaction_types.append("high_entropy")

        return redacted, redaction_types

    def redact_message(self, message: Dict) -> Tuple[Dict, List[str]]:
        """
        Redact secrets from a conversation message

        Args:
            message: Message dict with role, content, timestamp

        Returns:
            (redacted_message, redaction_types)
        """
        redacted_msg = message.copy()
        all_redaction_types = []

        # Redact content
        if "content" in message:
            redacted_content, types = self.redact_text(
                message["content"],
                context=f"{message.get('role', 'unknown')}"
            )
            redacted_msg["content"] = redacted_content
            all_redaction_types.extend(types)

        return redacted_msg, all_redaction_types

    def redact_conversation(self, conversation: List[Dict]) -> Tuple[List[Dict], Dict[str, int]]:
        """
        Redact secrets from entire conversation

        Args:
            conversation: List of message dicts

        Returns:
            (redacted_conversation, redaction_stats)
        """
        redacted_conv = []
        stats = {}

        for message in conversation:
            redacted_msg, types = self.redact_message(message)
            redacted_conv.append(redacted_msg)

            # Count redaction types
            for t in types:
                stats[t] = stats.get(t, 0) + 1

        return redacted_conv, stats

    def get_redaction_map(self) -> Dict[str, str]:
        """
        Get mapping of placeholders to original values

        Returns:
            Dict of {placeholder: original_value}
        """
        return self.redaction_map.copy()

    def rehydrate_text(self, text: str) -> str:
        """
        Restore redacted values (use carefully!)

        Args:
            text: Text with redaction placeholders

        Returns:
            Text with original values restored
        """
        rehydrated = text
        for placeholder, original in self.redaction_map.items():
            rehydrated = rehydrated.replace(placeholder, original)
        return rehydrated


def redact_for_summarization(
    conversation: List[Dict],
    redact_emails: bool = True,
    redact_ips: bool = False
) -> Tuple[List[Dict], Dict[str, int]]:
    """
    Convenience function to redact conversation before summarization

    Args:
        conversation: List of message dicts
        redact_emails: Whether to redact email addresses
        redact_ips: Whether to redact IP addresses

    Returns:
        (redacted_conversation, redaction_stats)
    """
    redactor = SecretRedactor(redact_emails=redact_emails, redact_ips=redact_ips)
    return redactor.redact_conversation(conversation)
