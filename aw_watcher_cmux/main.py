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


def poll_once(config, normalizer: Normalizer) -> Event | None:
    """One poll tick. Returns an Event, or None if the tick should be skipped."""
    path = cmux.socket_path(config.socket_path)
    if not cmux.socket_available(path):
        logger.debug("cmux socket %s missing; skipping tick (gap)", path)
        return None
    try:
        focused = cmux.get_focused(config.cmux_bin)
    except cmux.CmuxError as exc:
        logger.debug("skipping tick: %s", exc)
        return None
    return build_event(focused, normalizer)


def run(client, bucket_id: str, config) -> None:
    """Run the poll loop until interrupted. `client` is a connected ActivityWatchClient."""
    normalizer = Normalizer(
        agent_patterns=config.agent_patterns,
        generic_terminal_label=config.generic_terminal_label,
        keep_command_name=config.keep_command_name,
    )
    logger.info("aw-watcher-cmux started (poll=%ss, pulsetime=%ss)",
                config.poll_interval, config.pulsetime)
    while True:
        time.sleep(config.poll_interval)
        event = poll_once(config, normalizer)
        if event is None:
            continue
        # queued=True buffers heartbeats so a transient aw-server outage neither
        # loses data nor crashes the loop (spec §13).
        client.heartbeat(bucket_id, event, pulsetime=config.pulsetime, queued=True)
        logger.debug("heartbeat: app=%r title=%r is_agent=%s",
                     event.data["app"], event.data["title"], event.data["is_agent"])
