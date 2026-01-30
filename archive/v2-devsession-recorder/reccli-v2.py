#!/usr/bin/env python3
"""
RecCli v2.0 - Pure Python Terminal Recorder with .devsession format
Entry point script
"""

import sys
from pathlib import Path

# Add reccli package to path
sys.path.insert(0, str(Path(__file__).parent))

from reccli.cli import main

if __name__ == '__main__':
    sys.exit(main())
