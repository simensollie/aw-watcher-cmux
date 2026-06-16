#!/usr/bin/env bash
#
# One-command install for aw-watcher-cmux as a macOS launchd LaunchAgent.
#
# What it does:
#   1. Creates a self-contained venv at ~/.local/share/aw-watcher-cmux/venv
#      and installs aw-watcher-cmux into it (no pipx / global state needed).
#   2. Builds a tiny aw-watcher-cmux.app whose executable IS the Python
#      interpreter, so the macOS Accessibility entry shows as "aw-watcher-cmux"
#      (the bundle name) instead of "python3.14". The package is loaded from the
#      venv via PYTHONPATH — no exec(), which would lose the bundle identity.
#   3. Installs + loads a LaunchAgent (auto-starts at login, survives
#      ActivityWatch updates, writes nothing into the AW app).
#   4. The watcher pops the native Accessibility prompt from the bundle's own
#      launchd context on first run, registering the correct identity.
#
# Re-running is safe (idempotent): it rebuilds and reloads.
set -euo pipefail

if [ "$(uname)" != "Darwin" ]; then
  echo "aw-watcher-cmux is macOS only (cmux is macOS only)." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$HOME/.local/share/aw-watcher-cmux"
VENV="$APP_DIR/venv"
APP="$APP_DIR/aw-watcher-cmux.app"
BUNDLE_ID="io.sollie.aw-watcher-cmux"
LABEL="com.activitywatch.aw-watcher-cmux"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/activitywatch"
EXEC="$APP/Contents/MacOS/aw-watcher-cmux"

echo "==> Creating venv at $VENV"
mkdir -p "$APP_DIR" "$LOG_DIR" "$HOME/Library/LaunchAgents"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
echo "==> Installing aw-watcher-cmux from $REPO_ROOT"
"$VENV/bin/pip" install --quiet "$REPO_ROOT"

PYVER="$("$VENV/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
SITE="$VENV/lib/python$PYVER/site-packages"
BASEPY="$("$VENV/bin/python" -c 'import sys; print(sys._base_executable)')"

echo "==> Building $APP (so Accessibility shows 'aw-watcher-cmux', not 'python$PYVER')"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
# The bundle executable is a copy of the base interpreter. Because it lives at
# <app>/Contents/MacOS/<CFBundleExecutable>, macOS treats the process as this
# bundle and labels TCC entries with CFBundleName.
cp "$BASEPY" "$EXEC"
chmod +x "$EXEC"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>aw-watcher-cmux</string>
  <key>CFBundleDisplayName</key><string>aw-watcher-cmux</string>
  <key>CFBundleExecutable</key><string>aw-watcher-cmux</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>LSUIElement</key><true/>
  <key>LSBackgroundOnly</key><true/>
</dict></plist>
PLIST
# Ad-hoc sign so TCC has a stable identity across reinstalls.
codesign --force --sign - --identifier "$BUNDLE_ID" "$EXEC" 2>/dev/null \
  || echo "   (codesign unavailable; bundle still works, identity just isn't pinned)"

echo "==> Verifying the bundled interpreter can load the package"
if ! PYTHONPATH="$SITE" "$EXEC" -c "import aw_watcher_cmux, ApplicationServices" 2>/dev/null; then
  echo "ERROR: the bundled interpreter could not import aw_watcher_cmux/pyobjc." >&2
  echo "       Please open an issue with your Python/install details." >&2
  exit 1
fi

echo "==> Writing LaunchAgent $PLIST"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$EXEC</string><string>-m</string><string>aw_watcher_cmux</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>PYTHONPATH</key><string>$SITE</string>
  </dict>
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
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" 2>/dev/null || true

cat <<EOF

Installed and started. One manual step remains:

  A macOS dialog asking to control your computer via Accessibility should have
  appeared. In System Settings > Privacy & Security > Accessibility, enable the
  toggle for "aw-watcher-cmux", then restart it:

    launchctl kickstart -k gui/$(id -u)/$LABEL

  (If you previously granted a "python$PYVER" entry for this, you can remove it.)

The watcher re-checks permission every poll, so once enabled it starts recording.
Verify from inside a cmux tab:

    $VENV/bin/aw-watcher-cmux --selfcheck      # should print MATCH

Logs: $LOG_DIR/aw-watcher-cmux.log
Uninstall: scripts/uninstall.sh
EOF
