#!/usr/bin/env python3
"""
Launch the TypeScript UI for RecCli chat.
"""

import os
import sys
import subprocess
from pathlib import Path


def ensure_ui_built():
    """Ensure the TypeScript UI is built."""
    ui_dir = Path(__file__).parent.parent / "ui"
    dist_dir = ui_dir / "dist"

    # Check if dist directory exists and has files
    if not dist_dir.exists() or not list(dist_dir.glob("*.js")):
        print("Building TypeScript UI...")
        result = subprocess.run(
            ["npm", "run", "build"],
            cwd=ui_dir,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"Failed to build UI: {result.stderr}")
            sys.exit(1)


def launch_typescript_ui(model: str = "claude", session_name: str = None):
    """Launch the TypeScript UI with the Python backend."""
    ui_dir = Path(__file__).parent.parent / "ui"

    # Ensure UI is built
    ensure_ui_built()

    # Set environment variables for the backend
    env = os.environ.copy()
    env["RECCLI_MODEL"] = model
    if session_name:
        env["RECCLI_SESSION_NAME"] = session_name

    # Launch the TypeScript UI
    try:
        subprocess.run(
            ["node", "dist/index.js"],
            cwd=ui_dir,
            env=env,
            check=True
        )
    except KeyboardInterrupt:
        print("\n\nChat session ended.")
    except subprocess.CalledProcessError as e:
        print(f"Error launching UI: {e}")
        sys.exit(1)


if __name__ == "__main__":
    launch_typescript_ui()