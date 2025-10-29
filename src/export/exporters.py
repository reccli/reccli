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

    def _extract_terminal_output(self) -> str:
        """Extract plain text output from asciinema .cast file"""
        if not self.session_file.exists():
            return ""

        try:
            # Use asciinema cat to get plain text output
            result = subprocess.run(
                ['asciinema', 'cat', str(self.session_file)],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return result.stdout
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

            return ''.join(output)
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
