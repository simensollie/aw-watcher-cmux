#!/usr/bin/env bash
#
# Remove the aw-watcher-cmux LaunchAgent and venv installed by install.sh.
# Does not touch ActivityWatch or your recorded data. You may also want to
# remove aw-watcher-cmux's entry from System Settings > Privacy & Security >
# Accessibility manually.
set -euo pipefail

LABEL="com.activitywatch.aw-watcher-cmux"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
APP_DIR="$HOME/.local/share/aw-watcher-cmux"

echo "==> Stopping + unloading the LaunchAgent"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"

echo "==> Removing venv at $APP_DIR"
rm -rf "$APP_DIR"

echo "Done. (Accessibility list entry, if any, must be removed manually.)"
