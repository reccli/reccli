#!/bin/bash
# RecCli Uninstallation Script

set -e

echo "🗑️  Uninstalling RecCli..."

LAUNCH_AGENT_DIR="$HOME/Library/LaunchAgents"
LAUNCH_AGENT_PLIST="com.reccli.watcher.plist"

# Stop and unload the LaunchAgent
if [ -f "$LAUNCH_AGENT_DIR/$LAUNCH_AGENT_PLIST" ]; then
    echo "🛑 Stopping watcher..."
    launchctl unload "$LAUNCH_AGENT_DIR/$LAUNCH_AGENT_PLIST" 2>/dev/null || true
    rm "$LAUNCH_AGENT_DIR/$LAUNCH_AGENT_PLIST"
    echo "✅ LaunchAgent removed"
fi

# Kill any running instances
echo "🔪 Killing running instances..."
pkill -f "reccli.py" || true

# Clean up temp files
echo "🧹 Cleaning up..."
rm -f /tmp/reccli_processes.json
rm -f /tmp/reccli_debug.log
rm -f /tmp/reccli_watcher.log
rm -f /tmp/reccli_watcher_error.log

echo ""
echo "✅ RecCli uninstalled successfully!"
echo ""
echo "Note: Your recordings in ~/.reccli/recordings were NOT deleted"
echo "To remove them: rm -rf ~/.reccli"
