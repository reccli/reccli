#!/usr/bin/env python3
"""
RecCli v2 GUI - Floating button launcher
Starts the floating button interface for easy .devsession recording
"""

import sys
from pathlib import Path

# Add reccli package to path
sys.path.insert(0, str(Path(__file__).parent))

from reccli.recorder import DevsessionGUI, HAS_TKINTER


def main():
    """Launch floating button GUI"""
    if not HAS_TKINTER:
        print("❌ Error: Tkinter not available")
        print("Install tkinter to use GUI mode:")
        print("  macOS: brew install python-tk")
        print("  Ubuntu: sudo apt install python3-tk")
        return 1

    # Check for terminal ID argument (for launching multiple instances)
    terminal_id = None
    if len(sys.argv) > 1:
        try:
            terminal_id = int(sys.argv[1])
        except ValueError:
            print(f"Invalid terminal ID: {sys.argv[1]}")
            return 1

    # Launch GUI
    try:
        gui = DevsessionGUI(terminal_id=terminal_id)
        gui.run()
        return 0
    except KeyboardInterrupt:
        print("\nExiting...")
        return 0
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
