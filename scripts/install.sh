#!/usr/bin/env bash
#
# One-command install for aw-watcher-cmux as a macOS launchd LaunchAgent.
#
# What it does:
#   1. Creates a self-contained venv at ~/.local/share/aw-watcher-cmux/venv
#      and installs aw-watcher-cmux into it (no pipx / global state needed).
#   2. Installs + loads a LaunchAgent so the watcher auto-starts at login and
#      survives ActivityWatch updates (nothing is written into the AW app).
#   3. Triggers the macOS Accessibility permission prompt for the venv's Python
#      and opens the right Settings pane — the one manual step.
#
# Re-running is safe (idempotent): it reinstalls and reloads.
set -euo pipefail

if [ "$(uname)" != "Darwin" ]; then
  echo "aw-watcher-cmux is macOS only (cmux is macOS only)." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$HOME/.local/share/aw-watcher-cmux"
VENV="$APP_DIR/venv"
LABEL="com.activitywatch.aw-watcher-cmux"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/activitywatch"
EXEC="$VENV/bin/aw-watcher-cmux"

echo "==> Creating venv at $VENV"
mkdir -p "$APP_DIR" "$LOG_DIR" "$HOME/Library/LaunchAgents"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
echo "==> Installing aw-watcher-cmux from $REPO_ROOT"
"$VENV/bin/pip" install --quiet "$REPO_ROOT"

echo "==> Writing LaunchAgent $PLIST"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$EXEC</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LOG_DIR/aw-watcher-cmux.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/aw-watcher-cmux.log</string>
</dict></plist>
PLIST

echo "==> Loading the LaunchAgent"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "==> Opening the Accessibility settings pane"
# The LaunchAgent we just started (RunAtLoad) triggers the native Accessibility
# prompt from its own launchd context on first run, registering the correct
# binary. We just open the pane so the user can flip the toggle.
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" 2>/dev/null || true

cat <<EOF

Installed and started. One manual step remains:

  A macOS dialog asking to control your computer via Accessibility should have
  appeared (or will shortly). In System Settings > Privacy & Security >
  Accessibility, enable the toggle for aw-watcher-cmux, then restart it:

    launchctl kickstart -k gui/$(id -u)/$LABEL

The watcher re-checks permission every poll, so once enabled it starts recording
(no reinstall needed). Verify from inside a cmux tab:

    $EXEC --selfcheck      # should print MATCH

Logs: $LOG_DIR/aw-watcher-cmux.log
Uninstall: scripts/uninstall.sh
EOF
