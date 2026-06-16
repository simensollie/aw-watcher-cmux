"""Run the cmux CLI over its Unix socket and parse the focused context.

cmux exposes no event/subscribe API (socket protocol v2), so we poll two text
commands and pick the focused line. The globally focused tab is the [selected]
surface of the [selected] workspace (spec §3). --json is ignored by cmux, so we
parse the plain-text line format:

    [* ]<ref>  <name>  [selected]?

The focused line is the one ending in the [selected] marker (fallback: a line
starting with "*"). We strip only the trailing marker, anchored at line end, so
a title that literally contains "[selected]" is preserved (spec §13).
"""

from __future__ import annotations

import logging
import os
import stat
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_SOCKET = "/tmp/cmux.sock"
CALL_TIMEOUT = 1.0  # seconds; a hung cmux call must not stall the poll loop (§13)

# Trailing focus marker, anchored at line end.
_SELECTED_SUFFIX = "[selected]"


@dataclass(frozen=True)
class Focused:
    """The focused workspace and surface at one poll tick."""

    workspace_ref: str
    workspace_name: str
    surface_ref: str
    surface_title: str


class CmuxError(Exception):
    """A cmux call failed or returned nothing usable; the tick is skipped."""


def socket_path(override: str | None = None) -> str:
    """Resolve the socket path: explicit override, env, then default."""
    return override or os.environ.get("CMUX_SOCKET_PATH") or DEFAULT_SOCKET


def socket_available(path: str) -> bool:
    """True if the cmux socket exists (cmux is running)."""
    try:
        return stat.S_ISSOCK(os.stat(path).st_mode)
    except OSError:
        return False


def run(cmux_bin: str, *args: str, timeout: float = CALL_TIMEOUT) -> str:
    """Run `cmux <args>` and return stdout. Raises CmuxError on any failure."""
    try:
        proc = subprocess.run(
            [cmux_bin, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise CmuxError(f"cmux {' '.join(args)} timed out") from exc
    except OSError as exc:
        raise CmuxError(f"cmux binary not runnable: {exc}") from exc
    if proc.returncode != 0:
        raise CmuxError(
            f"cmux {' '.join(args)} exited {proc.returncode}: {proc.stderr.strip()}"
        )
    return proc.stdout


def focused_line(output: str) -> str | None:
    """Return the focused line: the one with the trailing [selected] marker,
    falling back to the first line starting with '*'."""
    fallback: str | None = None
    for raw in output.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.endswith(_SELECTED_SUFFIX):
            return line
        if fallback is None and line.lstrip().startswith("*"):
            fallback = line
    return fallback


def parse_line(line: str) -> tuple[str, str]:
    """Parse a workspace/surface line into (ref, name/title).

    Format: [* ]<ref>  <name>  [selected]?
    Strips a leading '*' focus glyph and a single trailing [selected] marker.
    """
    line = line.strip()
    if line.startswith("*"):
        line = line[1:].strip()
    if line.endswith(_SELECTED_SUFFIX):
        line = line[: -len(_SELECTED_SUFFIX)].rstrip()
    parts = line.split(None, 1)
    if not parts:
        raise CmuxError(f"unparseable line: {line!r}")
    ref = parts[0]
    name = parts[1].strip() if len(parts) > 1 else ""
    return ref, name


def get_focused(cmux_bin: str, socket: str | None = None) -> Focused:
    """Two CLI calls → the globally focused workspace/surface (spec §6).

    `socket`, if given, is passed as `cmux --socket <path>` so the oracle can
    target a non-default socket (otherwise cmux uses $CMUX_SOCKET_PATH/default).
    """
    sock_args = ("--socket", socket) if socket else ()
    ws_out = run(cmux_bin, *sock_args, "list-workspaces")
    ws_line = focused_line(ws_out)
    if ws_line is None:
        raise CmuxError("no selected workspace")
    ws_ref, ws_name = parse_line(ws_line)

    surf_out = run(cmux_bin, *sock_args, "list-pane-surfaces", "--workspace", ws_ref)
    surf_line = focused_line(surf_out)
    if surf_line is None:
        raise CmuxError("no selected surface")
    surf_ref, surf_title = parse_line(surf_line)

    return Focused(
        workspace_ref=ws_ref,
        workspace_name=ws_name,
        surface_ref=surf_ref,
        surface_title=surf_title,
    )
