"""Poll/heartbeat loop: emit the focused cmux workspace/tab, read via the macOS
Accessibility API (spec §3-§6). The watcher over-emits; "active cmux time" is
recovered query-side by intersecting with the window + AFK watchers (spec §8).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from aw_core.models import Event

from . import ax
from .normalize import Normalizer

logger = logging.getLogger(__name__)

# Poll outcomes.
OK = "ok"
NO_SOURCE = "no_source"        # cmux not running — a legitimate gap
NOT_TRUSTED = "not_trusted"    # missing Accessibility permission
AX_ERROR = "ax_error"          # cmux running but expected AX structure absent

WARN_AFTER_CONSECUTIVE_ERRORS = 10

_HINTS = {
    NOT_TRUSTED: (
        "aw-watcher-cmux lacks macOS Accessibility permission, so it cannot read "
        "cmux's focus. Grant it in System Settings > Privacy & Security > "
        "Accessibility (add the watcher, or aw-qt if it manages this watcher). "
        "See the README 'Install' section."
    ),
    AX_ERROR: (
        "cmux is running but its Accessibility structure was not as expected, so "
        "no focus could be read. cmux's UI may have changed in an update. Capture "
        "a fresh tree with `aw-watcher-cmux --snapshot` and file an issue."
    ),
}


def build_event(focused: ax.Focused, normalizer: Normalizer) -> Event:
    """Map a Focused to an aw currentwindow-style event (spec §5). Stores the
    normalized title so consecutive plain-shell ticks heartbeat-merge."""
    title, is_agent = normalizer.normalize(focused.surface_title)
    data = {"app": focused.workspace_name, "title": title, "is_agent": is_agent}
    if focused.workspace_index is not None:
        data["workspace_index"] = focused.workspace_index
    return Event(timestamp=datetime.now(timezone.utc), data=data)


def poll_once(config, normalizer: Normalizer) -> tuple[Event | None, str]:
    """One poll tick → (event_or_None, status). On OK the first element is the
    Event; otherwise it's None."""
    if not ax.is_trusted():
        return None, NOT_TRUSTED
    if ax.cmux_pid() is None:
        return None, NO_SOURCE
    focused = ax.get_focused()
    if focused is None:
        return None, AX_ERROR
    return build_event(focused, normalizer), OK


def run(client, bucket_id: str, config) -> None:
    """Run the poll loop until interrupted."""
    normalizer = Normalizer(
        agent_patterns=config.agent_patterns,
        generic_terminal_label=config.generic_terminal_label,
        keep_command_name=config.keep_command_name,
    )
    logger.info("aw-watcher-cmux started (poll=%ss, pulsetime=%ss)",
                config.poll_interval, config.pulsetime)
    consecutive = 0
    warned_status = None
    last_error_status = None
    while True:
        time.sleep(config.poll_interval)
        result, status = poll_once(config, normalizer)

        if status == OK:
            consecutive = 0
            warned_status = None
            last_error_status = None
            # queued=True buffers heartbeats so a transient aw-server outage
            # neither loses data nor crashes the loop (spec §13). This is the
            # one external call we deliberately don't guard — a real error here
            # should surface, not be swallowed.
            client.heartbeat(bucket_id, result, pulsetime=config.pulsetime, queued=True)
            logger.debug("heartbeat: app=%r title=%r is_agent=%s",
                         result.data["app"], result.data["title"], result.data["is_agent"])
        elif status == NO_SOURCE:
            consecutive = 0
            warned_status = None
            last_error_status = None
            logger.debug("cmux not running; skipping tick (gap)")
        else:  # NOT_TRUSTED or AX_ERROR
            # NOT_TRUSTED and AX_ERROR can't co-occur in one poll, but if the
            # failing condition changes, restart the counter for the new one.
            if status != last_error_status:
                consecutive = 0
                last_error_status = status
            consecutive += 1
            if consecutive >= WARN_AFTER_CONSECUTIVE_ERRORS and warned_status != status:
                logger.warning("%s (consecutive failures: %s)", _HINTS[status], consecutive)
                warned_status = status
