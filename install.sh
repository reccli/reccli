#!/bin/bash
# RecCli Installation Script

set -e

echo "🚀 Installing RecCli..."

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LAUNCH_AGENT_DIR="$HOME/Library/LaunchAgents"
LAUNCH_AGENT_PLIST="com.reccli.watcher.plist"

# Create LaunchAgents directory if it doesn't exist
mkdir -p "$LAUNCH_AGENT_DIR"

# Copy the plist file and update the paths
echo "📝 Installing LaunchAgent..."
cat "$SCRIPT_DIR/$LAUNCH_AGENT_PLIST" | \
    sed "s|/Users/will/coding-projects/reccli/reccli.py|$SCRIPT_DIR/reccli.py|g" | \
    sed "s|/usr/local/bin/python3|$(which python3)|g" > "$LAUNCH_AGENT_DIR/$LAUNCH_AGENT_PLIST"

# Load the LaunchAgent
echo "🔄 Loading LaunchAgent..."
launchctl unload "$LAUNCH_AGENT_DIR/$LAUNCH_AGENT_PLIST" 2>/dev/null || true
launchctl load "$LAUNCH_AGENT_DIR/$LAUNCH_AGENT_PLIST"

echo ""
echo "✅ RecCli installed successfully!"
echo ""
echo "🎉 The watcher is now running in the background"
echo "   - Opens a popup automatically when you open a Terminal window"
echo "   - Will auto-start on system login"
echo ""
echo "Commands:"
echo "  python3 $SCRIPT_DIR/reccli.py watch     - Start watcher (already running)"
echo "  python3 $SCRIPT_DIR/reccli.py launch    - Launch for current terminals only"
echo "  python3 $SCRIPT_DIR/reccli.py killall   - Stop all reccli popups"
echo "  python3 $SCRIPT_DIR/reccli.py status    - View recording stats"
echo ""
echo "📂 Recordings saved to: ~/.reccli/recordings"
echo "📝 Logs: /tmp/reccli_watcher.log"
