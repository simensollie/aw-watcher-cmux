"""Poll/heartbeat loop: emit the currently focused cmux workspace/tab.

The watcher is intentionally dumb (spec §4): it always emits the focused
workspace/surface, even when cmux is not the frontmost macOS app. cmux exposes
no "am I frontmost" query, so rather than teach the watcher about OS focus we
over-emit and let an aw query intersect with the window + AFK watchers (§8).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from aw_core.models import Event

from . import cmux
from .normalize import Normalizer

logger = logging.getLogger(__name__)

# Poll outcomes. NO_SOCKET is a normal gap (cmux not running); CMUX_ERROR means
# the socket exists but the query failed — persistently, that is almost always
# the cmuxOnly access mode rejecting a detached caller (see warn-once below).
OK = "ok"
NO_SOCKET = "no_socket"
CMUX_ERROR = "cmux_error"

# After this many consecutive failed ticks *while the socket exists*, emit one
# loud warning. At a 2s poll that's ~20s of silent gap before we explain it.
WARN_AFTER_CONSECUTIVE_ERRORS = 10

_ACCESS_DENIED_HINT = (
    "cmux is running but every query is being rejected. cmux's control socket "
    "only authorizes callers running INSIDE a cmux surface (access_mode "
    "'cmuxOnly'); a detached process (launchd, aw-qt, nohup) has no caller "
    "surface and is refused. Run aw-watcher-cmux from inside a cmux tab, or "
    "enable external socket auth in cmux Settings. See the README "
    "'Installation' section. (last error: %s)"
)


def build_event(focused: cmux.Focused, normalizer: Normalizer) -> Event:
    """Map a focused context to an aw currentwindow-style event.

    Stores the *normalized* title (not the raw churny one) so consecutive
    plain-shell ticks heartbeat-merge into long terminal blocks (spec §5).
    """
    title, is_agent = normalizer.normalize(focused.surface_title)
    data = {
        "app": focused.workspace_name,
        "title": title,
        "is_agent": is_agent,
        "workspace_ref": focused.workspace_ref,
        "surface_ref": focused.surface_ref,
    }
    return Event(timestamp=datetime.now(timezone.utc), data=data)


def poll_once(config, normalizer: Normalizer) -> tuple[Event | None, str]:
    """One poll tick. Returns (event_or_None, status) where status is one of
    OK / NO_SOCKET / CMUX_ERROR. On CMUX_ERROR the event is the error message
    string so the loop can surface it."""
    path = cmux.socket_path(config.socket_path)
    if not cmux.socket_available(path):
        logger.debug("cmux socket %s missing; skipping tick (gap)", path)
        return None, NO_SOCKET
    try:
        focused = cmux.get_focused(config.cmux_bin)
    except cmux.CmuxError as exc:
        logger.debug("skipping tick: %s", exc)
        return str(exc), CMUX_ERROR
    return build_event(focused, normalizer), OK


def run(client, bucket_id: str, config) -> None:
    """Run the poll loop until interrupted. `client` is a connected ActivityWatchClient."""
    normalizer = Normalizer(
        agent_patterns=config.agent_patterns,
        generic_terminal_label=config.generic_terminal_label,
        keep_command_name=config.keep_command_name,
    )
    logger.info("aw-watcher-cmux started (poll=%ss, pulsetime=%ss)",
                config.poll_interval, config.pulsetime)
    consecutive_errors = 0
    warned = False
    while True:
        time.sleep(config.poll_interval)
        result, status = poll_once(config, normalizer)

        if status == OK:
            consecutive_errors = 0
            warned = False
            # queued=True buffers heartbeats so a transient aw-server outage
            # neither loses data nor crashes the loop (spec §13).
            client.heartbeat(bucket_id, result, pulsetime=config.pulsetime, queued=True)
            logger.debug("heartbeat: app=%r title=%r is_agent=%s",
                         result.data["app"], result.data["title"], result.data["is_agent"])
        elif status == NO_SOCKET:
            # cmux not running — a legitimate gap, not an error. Reset state so a
            # later access-denied burst still warns.
            consecutive_errors = 0
            warned = False
        else:  # CMUX_ERROR: socket present but the query failed
            consecutive_errors += 1
            if not warned and consecutive_errors >= WARN_AFTER_CONSECUTIVE_ERRORS:
                logger.warning(_ACCESS_DENIED_HINT, result)
                warned = True
