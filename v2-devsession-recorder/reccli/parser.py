"""
Conversation Parser - Extract structured conversations from terminal recordings
Parses terminal events into user/assistant message pairs

Based on proven logic from reccli-public/src/export/exporters.py
Handles incremental typing, ANSI codes, and terminal artifacts
"""

import re
from typing import List, Dict, Optional, Tuple


class ConversationParser:
    """Parse terminal events into structured conversation"""

    # LLM CLI patterns to detect
    LLM_PATTERNS = [
        r'Claude Code',
        r'ChatGPT',
        r'GPT-\d+',
        r'claude-\w+',
        r'gpt-\w+',
    ]

    # ANSI escape code pattern
    ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

    def __init__(self):
        """Initialize parser"""
        self.llm_detected = None
        self.in_llm_session = False

    def detect_llm(self, events: List[List]) -> Optional[str]:
        """
        Detect which LLM CLI is being used

        Args:
            events: Terminal events array

        Returns:
            LLM name if detected, None otherwise
        """
        # Check first 20 output events for LLM signatures
        output_text = ""
        for event in events[:50]:
            if event[1] == "o":  # output event
                output_text += event[2]

        # Check for LLM patterns
        for pattern in self.LLM_PATTERNS:
            if re.search(pattern, output_text, re.IGNORECASE):
                match = re.search(pattern, output_text, re.IGNORECASE)
                self.llm_detected = match.group(0)
                return self.llm_detected

        return None

    def is_user_input(self, event: List, next_event: Optional[List] = None) -> bool:
        """
        Determine if an event is user input

        Args:
            event: Current event [timestamp, type, data]
            next_event: Next event (optional, for echo detection)

        Returns:
            True if this is user input
        """
        timestamp, event_type, data = event

        # Input events are user input
        if event_type == "i":
            # Filter out shell echoes and prompts
            if data.strip() in ["", "$", ">", "#"]:
                return False
            # Filter out command that launched the LLM
            if data.strip().lower() in ["claude", "chatgpt", "gpt", "codex"]:
                return False
            return True

        return False

    def clean_text(self, text: str) -> str:
        """
        Clean terminal text (remove ANSI codes, normalize whitespace)
        Based on reccli-public proven logic

        Args:
            text: Raw terminal text

        Returns:
            Cleaned text
        """
        # Remove ANSI escape codes
        text = self.ANSI_ESCAPE.sub('', text)

        # Remove carriage returns
        text = text.replace('\r\n', '\n').replace('\r', '\n')

        # Strip trailing whitespace but preserve leading (for code blocks)
        lines = [line.rstrip() for line in text.split('\n')]
        text = '\n'.join(lines)

        return text.strip()

    def clean_incremental_typing(self, content: str) -> str:
        """
        Remove incremental typing artifacts from terminal output.
        Keeps only final versions of lines (after Enter was pressed).

        Based on proven logic from reccli-public/src/export/exporters.py

        Args:
            content: Raw terminal content

        Returns:
            Cleaned content with typing artifacts removed
        """
        lines = content.split('\n')

        # First pass: identify all prompt lines and group them
        prompt_positions = []
        for i, line in enumerate(lines):
            if line.strip().startswith('>'):
                prompt_positions.append(i)

        if not prompt_positions:
            return content

        # Group prompts that are part of incremental typing
        # A large gap (>10 lines) or a response from Claude indicates a new command
        prompt_groups = []
        current_group = [prompt_positions[0]]

        for i in range(1, len(prompt_positions)):
            prev_pos = prompt_positions[i-1]
            curr_pos = prompt_positions[i]

            # Check if there's a Claude response (⏺) between the two prompts
            has_response = False
            for j in range(prev_pos + 1, curr_pos):
                if '⏺' in lines[j]:
                    has_response = True
                    break

            # If there's a response or large gap, start new group
            if has_response or (curr_pos - prev_pos > 10):
                prompt_groups.append(current_group)
                current_group = [curr_pos]
            else:
                # Same group (incremental typing)
                current_group.append(curr_pos)

        # Don't forget the last group
        prompt_groups.append(current_group)

        # Determine which lines to keep
        lines_to_keep = set()
        for group in prompt_groups:
            # For each group, find the prompt with actual content
            non_empty_prompts = [pos for pos in group if lines[pos].strip() not in ['>', '> ']]

            if non_empty_prompts:
                # Keep the last non-empty prompt
                lines_to_keep.add(non_empty_prompts[-1])
            elif len(group) == 1:
                # Single empty prompt might be intentional
                lines_to_keep.add(group[0])

        # Build cleaned output
        cleaned_lines = []
        seen_lines = set()

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Skip separators
            if all(c in '─' for c in stripped) and stripped:
                continue

            # Keep selected prompt lines (but only if they have content)
            if i in lines_to_keep:
                if stripped not in ['>', '> ']:
                    cleaned_lines.append(line)
                continue

            # Skip other prompt lines
            if stripped.startswith('>'):
                continue

            # Skip empty lines
            if not stripped:
                continue

            # Skip loading animations
            if any(x in stripped for x in ['Galloping', 'Warping', 'Deliberating', 'Combobulating',
                                           'Musing', 'Prestidigitating', 'Finagling', 'Whatchamacalliting',
                                           '(esc to interrupt)']):
                continue

            # Skip exit messages
            if any(x in stripped for x in ['Press Ctrl-D', 'again to exit']):
                continue

            # Skip duplicate UI elements - only keep first occurrence
            if any(x in stripped for x in ['? for shortcuts', 'Thinking off', 'tab to toggle']):
                if stripped not in seen_lines:
                    seen_lines.add(stripped)
                    cleaned_lines.append(line)
                continue

            # Skip duplicate status messages
            if 'Claude Opus limit reached' in stripped:
                if stripped not in seen_lines:
                    seen_lines.add(stripped)
                    cleaned_lines.append(line)
                continue

            # Keep everything else
            cleaned_lines.append(line)

        return '\n'.join(cleaned_lines)

    def group_output_lines(self, events: List[List], start_idx: int, end_idx: int) -> str:
        """
        Group consecutive output events into a single message

        Args:
            events: All terminal events
            start_idx: Start index
            end_idx: End index (exclusive)

        Returns:
            Combined output text
        """
        output_lines = []
        for i in range(start_idx, end_idx):
            if events[i][1] == "o":  # output event
                output_lines.append(events[i][2])

        combined = ''.join(output_lines)
        return self.clean_text(combined)

    def parse(self, events: List[List]) -> List[Dict]:
        """
        Parse terminal events into conversation messages

        Args:
            events: Terminal events array [[timestamp, type, data], ...]

        Returns:
            Conversation array [{"role": "user", "content": "...", "timestamp": 0.0}, ...]
        """
        conversation = []

        # Detect LLM
        llm = self.detect_llm(events)
        if not llm:
            # No LLM detected - might be a regular shell session
            return conversation

        # Find start of LLM session (after LLM startup text)
        llm_start_idx = 0
        for i, event in enumerate(events):
            if event[1] == "o" and llm in event[2]:
                llm_start_idx = i + 1
                break

        # Parse messages
        i = llm_start_idx
        pending_output_start = None

        while i < len(events):
            event = events[i]
            timestamp, event_type, data = event

            # User input
            if self.is_user_input(event):
                # Save any pending assistant output
                if pending_output_start is not None:
                    assistant_text = self.group_output_lines(events, pending_output_start, i)
                    if assistant_text:
                        conversation.append({
                            "role": "assistant",
                            "content": assistant_text,
                            "timestamp": events[pending_output_start][0]
                        })
                    pending_output_start = None

                # Add user message
                user_text = self.clean_text(data)
                if user_text and user_text.lower() not in ["exit", "quit", "bye"]:
                    conversation.append({
                        "role": "user",
                        "content": user_text,
                        "timestamp": timestamp
                    })

            # Output (potential assistant response)
            elif event_type == "o":
                # Start collecting output if not already
                if pending_output_start is None:
                    # Skip shell prompts
                    if data.strip() not in ["$", ">", "#", ""]:
                        pending_output_start = i

            i += 1

        # Save any remaining assistant output
        if pending_output_start is not None:
            assistant_text = self.group_output_lines(events, pending_output_start, len(events))
            if assistant_text:
                conversation.append({
                    "role": "assistant",
                    "content": assistant_text,
                    "timestamp": events[pending_output_start][0]
                })

        return conversation


def parse_conversation(events: List[List]) -> List[Dict]:
    """
    Convenience function to parse terminal events into conversation

    Args:
        events: Terminal events array

    Returns:
        Conversation array
    """
    parser = ConversationParser()
    return parser.parse(events)
