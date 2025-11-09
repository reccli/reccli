"""
Export formats for RecCli
Exports .devsession recordings to various formats
"""

import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional


class SessionExporter:
    """Export recorded sessions to various formats"""

    def __init__(self, session_file: Path, metadata: Optional[Dict] = None):
        """
        Initialize exporter

        Args:
            session_file: Path to .devsession file
            metadata: Optional metadata (session_id, duration, etc.)
        """
        self.session_file = Path(session_file)
        self.metadata = metadata or {}
        self.conversation = None  # Parsed conversation (if available)

        # Try to load .devsession file for structured conversation
        self._load_devsession()

        # Fallback: extract terminal output from raw events
        self.terminal_output = self._extract_terminal_output()

    def _load_devsession(self):
        """Load .devsession file if available and extract conversation"""
        # Check if it's a .devsession file (or no extension but valid JSON)
        if self.session_file.suffix == '.devsession' or self.session_file.suffix == '':
            try:
                with open(self.session_file, 'r', encoding='utf-8', errors='ignore') as f:
                    data = json.load(f)

                # Check if it's a valid .devsession format
                if data.get('format') == 'devsession' and 'conversation' in data:
                    self.conversation = data.get('conversation', [])
                    # Also update metadata if available
                    if 'session_id' in data:
                        self.metadata.setdefault('session_id', data['session_id'])
                    if 'created' in data:
                        self.metadata.setdefault('timestamp', data['created'])
                    # Calculate duration from terminal recording
                    if 'terminal_recording' in data and 'events' in data['terminal_recording']:
                        events = data['terminal_recording']['events']
                        if events:
                            duration = events[-1][0]  # last timestamp
                            self.metadata.setdefault('duration', f"{duration:.1f}s")
            except Exception as e:
                # Not a .devsession file or corrupt - fallback to terminal output
                pass

    def _clean_incremental_typing(self, content: str) -> str:
        """
        Remove incremental typing artifacts from terminal output.
        Keeps only final versions of lines (after Enter was pressed).
        """
        import re

        lines = content.split('\n')

        # First pass: identify all prompt lines and group them
        prompt_positions = []
        for i, line in enumerate(lines):
            if line.strip().startswith('>'):
                prompt_positions.append(i)

        if not prompt_positions:
            return content

        # Group prompts that are part of incremental typing
        # Look for prompts that are close together (incremental typing vs separate commands)
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
            # For each group, find the prompt with actual content (not just whitespace)
            non_empty_prompts = [pos for pos in group if lines[pos].strip() not in ['>', '> ']]

            if non_empty_prompts:
                # Keep the last non-empty prompt
                lines_to_keep.add(non_empty_prompts[-1])
            elif len(group) == 1:
                # Single empty prompt might be intentional
                lines_to_keep.add(group[0])

        # Build cleaned output - remove duplicates and unnecessary lines
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

            # Skip ALL loading animations (don't keep any)
            # Check for thinking indicators (✶, ·, ✻) or specific animation words
            if (stripped.startswith('✶') or stripped.startswith('·') or stripped.startswith('✻') or
                any(x in stripped for x in ['Galloping', 'Warping', 'Deliberating', 'Combobulating',
                                           'Musing', 'Prestidigitating', 'Finagling', 'Whatchamacalliting',
                                           'Sautéing', 'Unfurling', 'Pondering', 'Cogitating', 'Ruminating',
                                           'Juliening', 'Boondoggling', 'Thundering', 'Beaming', 'Perusing',
                                           '(esc to interrupt)', 'esc to interrupt'])):
                continue

            # Skip "Press Ctrl-D" and exit messages first (before checking UI elements)
            if any(x in stripped for x in ['Press Ctrl-D', 'again to exit']):
                continue

            # Skip debug output
            if '[DEBUG]' in stripped:
                continue

            # Skip duplicate UI elements - only keep first occurrence
            if any(x in stripped for x in ['? for shortcuts', 'Thinking off', 'tab to toggle']):
                if stripped not in seen_lines:
                    seen_lines.add(stripped)
                    cleaned_lines.append(line)
                continue

            # Skip duplicate status messages
            if 'Claude Opus limit reached' in stripped:
                # Only keep one
                if stripped not in seen_lines:
                    seen_lines.add(stripped)
                    cleaned_lines.append(line)
                continue

            # Keep everything else
            cleaned_lines.append(line)

        return '\n'.join(cleaned_lines)

    def _extract_terminal_output(self) -> str:
        """Extract plain text output from session file (.devsession or .txt)"""
        if not self.session_file.exists():
            return ""

        # Check if it's a .devsession file (v2 format)
        if self.session_file.suffix == '.devsession':
            try:
                with open(self.session_file, 'r', encoding='utf-8', errors='ignore') as f:
                    data = json.load(f)

                # Extract terminal events
                if 'terminal_recording' in data and 'events' in data['terminal_recording']:
                    output_lines = []
                    for event in data['terminal_recording']['events']:
                        # event format: [timestamp, event_type, data]
                        if len(event) >= 3 and event[1] == 'o':  # 'o' = output event
                            output_lines.append(event[2])

                    content = ''.join(output_lines)

                    # Remove ANSI escape sequences
                    import re
                    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
                    cleaned = ansi_escape.sub('', content)

                    # Remove incremental typing artifacts
                    cleaned = self._clean_incremental_typing(cleaned)
                    return cleaned

            except Exception as e:
                print(f"Error reading .devsession file: {e}")
                return ""

        # Check if it's a plain text file from script command
        if self.session_file.suffix == '.txt':
            try:
                with open(self.session_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    # Strip terminal control codes for cleaner output
                    import re
                    # Remove ANSI escape sequences
                    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
                    cleaned = ansi_escape.sub('', content)
                    # Remove incremental typing artifacts
                    cleaned = self._clean_incremental_typing(cleaned)
                    return cleaned
            except Exception as e:
                print(f"Error reading txt file: {e}")
                return ""

        # No other formats supported
        return ""

    def export_txt(self, output_file: Path) -> bool:
        """
        Export as plain text

        Args:
            output_file: Path to save .txt file

        Returns:
            True if successful
        """
        try:
            session_id = self.metadata.get('session_id', self.session_file.stem)
            duration = self.metadata.get('duration', 'Unknown')
            timestamp = self.metadata.get('timestamp', datetime.now().isoformat())

            content = f"""Session: {session_id}
Duration: {duration}
Date: {timestamp}

{'=' * 60}
Terminal Output
{'=' * 60}

{self.terminal_output}
"""

            with open(output_file, 'w') as f:
                f.write(content)

            return True
        except Exception as e:
            print(f"Error exporting to txt: {e}")
            return False

    def export_md(self, output_file: Path) -> bool:
        """
        Export as Markdown

        Args:
            output_file: Path to save .md file

        Returns:
            True if successful
        """
        try:
            session_id = self.metadata.get('session_id', self.session_file.stem)
            duration_raw = self.metadata.get('duration', 0)
            # Format duration properly
            if isinstance(duration_raw, (int, float)):
                duration = format_duration(duration_raw)
            else:
                duration = str(duration_raw)
            timestamp = self.metadata.get('timestamp', datetime.now().isoformat())

            # Use conversation if available, otherwise fall back to terminal output
            if self.conversation:
                # Format as conversation
                conversation_md = []
                for msg in self.conversation:
                    role = msg.get('role', 'unknown')
                    content = msg.get('content', '')

                    if role == 'user':
                        conversation_md.append(f"**User:**\n\n{content}\n")
                    elif role == 'assistant':
                        conversation_md.append(f"**Assistant:**\n\n{content}\n")

                output_section = "## Conversation\n\n" + "\n---\n\n".join(conversation_md)
            else:
                # Fallback to terminal output
                output_section = f"""## Terminal Output

```
{self.terminal_output}
```"""

            content = f"""# Session: {session_id}

**Duration:** {duration}
**Date:** {timestamp}

{output_section}

---

*Recorded with [RecCli](https://github.com/willluecke/RecCli)*
"""

            with open(output_file, 'w') as f:
                f.write(content)

            return True
        except Exception as e:
            print(f"Error exporting to md: {e}")
            return False

    def export_json(self, output_file: Path) -> bool:
        """
        Export as JSON

        Args:
            output_file: Path to save .json file

        Returns:
            True if successful
        """
        try:
            session_id = self.metadata.get('session_id', self.session_file.stem)
            duration = self.metadata.get('duration', 'Unknown')
            timestamp = self.metadata.get('timestamp', datetime.now().isoformat())

            data = {
                'format': 'reccli-session',
                'version': '1.0.0',
                'session_id': session_id,
                'duration': duration,
                'timestamp': timestamp,
                'terminal_output': self.terminal_output,
                'metadata': self.metadata,
                'source_file': str(self.session_file)
            }

            with open(output_file, 'w') as f:
                json.dump(data, f, indent=2)

            return True
        except Exception as e:
            print(f"Error exporting to json: {e}")
            return False

    def export_html(self, output_file: Path) -> bool:
        """
        Export as HTML with styled terminal output

        Args:
            output_file: Path to save .html file

        Returns:
            True if successful
        """
        try:
            session_id = self.metadata.get('session_id', self.session_file.stem)
            duration_raw = self.metadata.get('duration', 0)
            # Format duration properly
            if isinstance(duration_raw, (int, float)):
                duration = format_duration(duration_raw)
            else:
                duration = str(duration_raw)
            timestamp = self.metadata.get('timestamp', datetime.now().isoformat())

            # Escape HTML
            output_escaped = (
                self.terminal_output
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
            )

            html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Session: {session_id}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        .header {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .header h1 {{
            margin: 0 0 10px 0;
            color: #333;
        }}
        .metadata {{
            color: #666;
            font-size: 14px;
        }}
        .terminal {{
            background: #1e1e1e;
            color: #d4d4d4;
            padding: 20px;
            border-radius: 8px;
            overflow-x: auto;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
            font-family: 'Monaco', 'Menlo', 'Consolas', monospace;
            font-size: 13px;
            line-height: 1.5;
            white-space: pre-wrap;
            word-break: break-all;
        }}
        .footer {{
            text-align: center;
            margin-top: 20px;
            color: #999;
            font-size: 12px;
        }}
        .footer a {{
            color: #27ae60;
            text-decoration: none;
        }}
        .footer a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Session: {session_id}</h1>
        <div class="metadata">
            <strong>Duration:</strong> {duration} |
            <strong>Date:</strong> {timestamp}
        </div>
    </div>

    <div class="terminal">{output_escaped}</div>

    <div class="footer">
        Recorded with <a href="https://github.com/willluecke/RecCli" target="_blank">RecCli</a>
    </div>
</body>
</html>
"""

            with open(output_file, 'w') as f:
                f.write(html)

            return True
        except Exception as e:
            print(f"Error exporting to html: {e}")
            return False

    def export(self, output_file: Path, format: str) -> bool:
        """
        Export to specified format

        Args:
            output_file: Path to save file
            format: Format type ('txt', 'md', 'json', 'html')

        Returns:
            True if successful
        """
        format = format.lower().lstrip('.')

        exporters = {
            'txt': self.export_txt,
            'md': self.export_md,
            'json': self.export_json,
            'html': self.export_html
        }

        if format not in exporters:
            print(f"Unknown format: {format}")
            return False

        return exporters[format](output_file)


def format_duration(seconds: float) -> str:
    """
    Format duration as human-readable string

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string like "1h 23m" or "45s"
    """
    if seconds < 60:
        return f"{int(seconds)}s"

    minutes = int(seconds // 60)
    secs = int(seconds % 60)

    if minutes < 60:
        return f"{minutes}m {secs}s"

    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"
