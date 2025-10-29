"""
Export formats for RecCli Phase 1 MVP
Converts asciinema recordings to various formats
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
            session_file: Path to asciinema .cast file
            metadata: Optional metadata (session_id, duration, etc.)
        """
        self.session_file = Path(session_file)
        self.metadata = metadata or {}

        # Try to extract terminal output from .cast file
        self.terminal_output = self._extract_terminal_output()

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
            if any(x in stripped for x in ['Galloping', 'Warping', 'Deliberating', 'Combobulating',
                                           'Musing', 'Prestidigitating', 'Finagling', 'Whatchamacalliting',
                                           '(esc to interrupt)']):
                continue

            # Skip "Press Ctrl-D" and exit messages first (before checking UI elements)
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
                # Only keep one
                if stripped not in seen_lines:
                    seen_lines.add(stripped)
                    cleaned_lines.append(line)
                continue

            # Keep everything else
            cleaned_lines.append(line)

        return '\n'.join(cleaned_lines)

    def _extract_terminal_output(self) -> str:
        """Extract plain text output from session file (.cast or .txt)"""
        if not self.session_file.exists():
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

        # Handle .cast files
        try:
            # Use asciinema convert to get plain text output (asciinema 3.x)
            result = subprocess.run(
                ['asciinema', 'convert', '-f', 'raw', str(self.session_file), '-'],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                # Apply the same cleaning as for .txt files
                import re
                content = result.stdout
                # Remove ANSI escape sequences
                ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
                cleaned = ansi_escape.sub('', content)
                # Remove incremental typing artifacts
                cleaned = self._clean_incremental_typing(cleaned)
                return cleaned
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        # Fallback: parse .cast file manually
        try:
            with open(self.session_file, 'r') as f:
                lines = f.readlines()

            output = []
            for line in lines[1:]:  # Skip header
                try:
                    event = json.loads(line)
                    if len(event) >= 3 and event[1] == 'o':  # Output event
                        output.append(event[2])
                except json.JSONDecodeError:
                    continue

            content = ''.join(output)

            # Apply the same cleaning as for other methods
            import re
            # Remove ANSI escape sequences
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            cleaned = ansi_escape.sub('', content)
            # Remove incremental typing artifacts
            cleaned = self._clean_incremental_typing(cleaned)
            return cleaned
        except Exception:
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
            duration = self.metadata.get('duration', 'Unknown')
            timestamp = self.metadata.get('timestamp', datetime.now().isoformat())

            content = f"""# Session: {session_id}

**Duration:** {duration}
**Date:** {timestamp}

## Terminal Output

```
{self.terminal_output}
```

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
            duration = self.metadata.get('duration', 'Unknown')
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

    def export_cast(self, output_file: Path) -> bool:
        """
        Export as asciinema .cast (just copy the file)

        Args:
            output_file: Path to save .cast file

        Returns:
            True if successful
        """
        try:
            import shutil
            shutil.copy2(self.session_file, output_file)
            return True
        except Exception as e:
            print(f"Error exporting to cast: {e}")
            return False

    def export(self, output_file: Path, format: str) -> bool:
        """
        Export to specified format

        Args:
            output_file: Path to save file
            format: Format type ('txt', 'md', 'json', 'html', 'cast')

        Returns:
            True if successful
        """
        format = format.lower().lstrip('.')

        exporters = {
            'txt': self.export_txt,
            'md': self.export_md,
            'json': self.export_json,
            'html': self.export_html,
            'cast': self.export_cast
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
