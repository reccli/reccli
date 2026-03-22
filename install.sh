#!/bin/bash
# RecCli local development install helper

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "Installing RecCli local dependencies..."
echo

cd "$SCRIPT_DIR"

echo "1. Installing Python dependencies"
python3 -m pip install -r requirements.txt

echo
echo "2. Installing and building TypeScript UI"
cd "$SCRIPT_DIR/packages/reccli-core/ui"
npm install
npm run build

echo
echo "RecCli local install complete."
echo
echo "Run commands from the repo root like:"
echo "  PYTHONPATH=packages python3 -m reccli.runtime.cli --help"
echo "  PYTHONPATH=packages python3 -m reccli.runtime.cli chat"
