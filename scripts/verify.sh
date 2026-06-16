#!/usr/bin/env bash
#
# End-to-end verification for aw-watcher-cmux against an ISOLATED test server
# (aw-server --testing on port 5666). Never touches your real ActivityWatch
# data on port 5600.
#
# IMPORTANT: cmux's control socket only authorizes callers running INSIDE a
# cmux surface (access_mode 'cmuxOnly'). Run this script from a cmux terminal
# tab. If you run it detached, cmux rejects every query (SIGPIPE) and this
# script will tell you so.
#
# Usage:  scripts/verify.sh            # auto-detects python / aw-server
#         PY=.venv/bin/python scripts/verify.sh
#         DURATION=10 scripts/verify.sh
set -euo pipefail

PORT=5666
HOST="$(hostname)"
BUCKET="aw-watcher-cmux_${HOST}-testing"
DURATION="${DURATION:-8}"
PY="${PY:-python3}"

# --- locate aw-server (bundled app, then PATH) ------------------------------
AWS="${AW_SERVER:-}"
if [ -z "$AWS" ]; then
  if [ -x "/Applications/ActivityWatch.app/Contents/MacOS/aw-server" ]; then
    AWS="/Applications/ActivityWatch.app/Contents/MacOS/aw-server"
  elif command -v aw-server >/dev/null 2>&1; then
    AWS="$(command -v aw-server)"
  else
    echo "ERROR: aw-server not found. Install ActivityWatch or set AW_SERVER=/path/to/aw-server" >&2
    exit 1
  fi
fi
echo "aw-server : $AWS"
echo "python    : $($PY --version 2>&1)"
echo "bucket    : $BUCKET"

STARTED_SERVER=0
WATCHER_PID=""
cleanup() {
  [ -n "$WATCHER_PID" ] && kill "$WATCHER_PID" 2>/dev/null || true
  if [ "$STARTED_SERVER" = "1" ]; then
    pkill -f "aw-server --testing" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# --- start an isolated test server if one isn't already up ------------------
if curl -s -m 1 "http://localhost:${PORT}/api/0/info" >/dev/null 2>&1; then
  echo "test server already running on :${PORT}"
else
  echo "starting aw-server --testing on :${PORT} ..."
  "$AWS" --testing >/tmp/aw-verify-server.log 2>&1 &
  STARTED_SERVER=1
  for _ in $(seq 1 20); do
    curl -s -m 1 "http://localhost:${PORT}/api/0/info" >/dev/null 2>&1 && break
    sleep 0.5
  done
  curl -s -m 1 "http://localhost:${PORT}/api/0/info" >/dev/null 2>&1 \
    || { echo "ERROR: test server did not come up (see /tmp/aw-verify-server.log)"; exit 1; }
fi

# --- run the watcher for DURATION seconds -----------------------------------
echo "running watcher for ${DURATION}s (poll=1s) ..."
"$PY" -m aw_watcher_cmux --testing --verbose --poll-interval 1 >/tmp/aw-verify-watcher.log 2>&1 &
WATCHER_PID=$!
sleep "$DURATION"
kill "$WATCHER_PID" 2>/dev/null || true
wait "$WATCHER_PID" 2>/dev/null || true   # reap quietly (no job-control message)
WATCHER_PID=""

# --- inspect results --------------------------------------------------------
if grep -q "exited -13\|cmuxOnly" /tmp/aw-verify-watcher.log; then
  echo
  echo "FAIL: cmux rejected every query (SIGPIPE / access denied)."
  echo "      You are almost certainly running this detached or outside a cmux"
  echo "      surface. Re-run from inside a cmux terminal tab."
  echo "      watcher log: /tmp/aw-verify-watcher.log"
  exit 2
fi

COUNT=$(curl -s "http://localhost:${PORT}/api/0/buckets/${BUCKET}/events?limit=100" \
        | "$PY" -c 'import sys,json; print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0)

echo
if [ "${COUNT:-0}" -gt 0 ]; then
  echo "PASS: ${COUNT} event(s) in ${BUCKET}. Sample:"
  curl -s "http://localhost:${PORT}/api/0/buckets/${BUCKET}/events?limit=3" \
    | "$PY" -m json.tool
else
  echo "FAIL: no events recorded. See /tmp/aw-verify-watcher.log"
  exit 2
fi
