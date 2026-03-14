#!/bin/bash
# RecCli local development cleanup helper

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "Cleaning local RecCli build artifacts..."

rm -rf "$SCRIPT_DIR/packages/reccli-core/ui/node_modules"
rm -rf "$SCRIPT_DIR/packages/reccli-core/ui/dist"

echo
echo "Removed UI build artifacts."
echo "User data under ~/reccli/ was not modified."
echo "To remove local RecCli data manually, run:"
echo "  rm -rf ~/reccli"
