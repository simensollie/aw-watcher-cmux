"""Read cmux's focused workspace + selected surface via the macOS Accessibility
API. Split into a pure extractor (tested offline against serialized AX trees)
and a thin live layer (pyobjc) that produces those trees. See spec §4.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Sidebar workspace rows describe themselves as "<name>, workspace N of M".
# We use this both to read the focused workspace's index and to exclude the
# sidebar's per-workspace selected tab from the main-content selection.
# Assumes main-content tab titles never end in this exact "…, workspace N of M"
# form (cmux tab titles don't); such a title would be misread as a sidebar row.
_SIDEBAR_RE = re.compile(r",\s*workspace\s+(\d+)\s+of\s+(\d+)\s*$")


@dataclass(frozen=True)
class Focused:
    """The focused workspace and its selected surface, read from AX."""
    workspace_name: str
    workspace_index: int | None
    surface_title: str


def extract_focused(snapshot: dict) -> Focused | None:
    """Pure: turn a serialized AX tree into a Focused, or None if the expected
    structure isn't present (caller treats None as AX_ERROR — never guesses)."""
    workspace = snapshot.get("workspace")
    window = snapshot.get("window")
    if not workspace or not window:
        return None

    state = {"index": None, "title": None}

    def walk(node: dict, under_sidebar_ws: bool) -> None:
        desc = node.get("desc")
        m = _SIDEBAR_RE.search(desc) if desc else None
        now_sidebar = under_sidebar_ws
        if m:
            now_sidebar = True
            if desc[: m.start()] == workspace and state["index"] is None:
                state["index"] = int(m.group(1))
        # A genuine selection: selected, has a title, in main content (not the
        # sidebar workspace list, and not itself a workspace row). `not now_sidebar`
        # already excludes a matched row (matching sets now_sidebar=True); the
        # explicit `m is None` documents "not the workspace row itself".
        if (
            node.get("selected") is True
            and desc
            and not now_sidebar
            and m is None
            and state["title"] is None
        ):
            state["title"] = desc
        for child in node.get("children", []) or []:
            walk(child, now_sidebar)

    walk(window, False)
    if state["title"] is None:
        return None
    return Focused(workspace_name=workspace, workspace_index=state["index"],
                   surface_title=state["title"])


# --- live layer (pyobjc) ----------------------------------------------------
# Imported lazily so the pure extractor (and its tests) work without pyobjc.

CMUX_BUNDLE_ID = "com.cmuxterm.app"

_MAX_DEPTH = 12


def is_trusted() -> bool:
    """True if this process may use the Accessibility API."""
    from ApplicationServices import AXIsProcessTrusted
    return bool(AXIsProcessTrusted())


def cmux_pid() -> int | None:
    """PID of the running cmux app, or None if cmux isn't running."""
    from AppKit import NSWorkspace
    try:
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            if app.bundleIdentifier() == CMUX_BUNDLE_ID:
                return int(app.processIdentifier())
    except Exception:  # noqa: BLE001 - never let AppKit errors crash the loop
        return None
    return None


def _copy(el, attr):
    """Read one AX attribute. `None` is the pyobjc placeholder for the output
    parameter; the call returns (error, value). Any bridge error → None so a
    revoked permission or stale element degrades to a skipped tick, not a crash.
    """
    from ApplicationServices import AXUIElementCopyAttributeValue
    try:
        err, val = AXUIElementCopyAttributeValue(el, attr, None)
    except Exception:  # noqa: BLE001
        return None
    return val if err == 0 else None


def serialize_node(el, depth: int = 0) -> dict:
    """Recursively serialize an AXUIElement into the plain-dict shape that
    extract_focused() consumes. role/title/value aren't read by extraction but
    are kept so `--snapshot` produces a complete, human-readable tree for
    capturing fixtures and diagnosing UI changes."""
    sel = _copy(el, "AXSelected")
    node = {
        "role": _copy(el, "AXRole"),
        "title": _copy(el, "AXTitle"),
        "value": _copy(el, "AXValue"),
        "desc": _copy(el, "AXDescription"),
        "selected": bool(sel) if sel is not None else None,
        "children": [],
    }
    if depth < _MAX_DEPTH:
        for child in (_copy(el, "AXChildren") or []):
            node["children"].append(serialize_node(child, depth + 1))
    return node


def snapshot_app(pid: int) -> dict | None:
    """Serialize the focused window of the cmux app at `pid`."""
    from ApplicationServices import AXUIElementCreateApplication
    app = AXUIElementCreateApplication(pid)
    win = _copy(app, "AXFocusedWindow")
    if win is None:
        return None
    return {"workspace": _copy(win, "AXTitle"), "window": serialize_node(win)}


def get_focused() -> Focused | None:
    """Live focused workspace + selected surface, or None if cmux is running but
    the expected AX structure wasn't found. Returns None too if no focused
    window; callers distinguish 'cmux not running' via cmux_pid()."""
    pid = cmux_pid()
    if pid is None:
        return None
    try:
        snap = snapshot_app(pid)
    except Exception:  # noqa: BLE001 - AX bridge errors become a skipped tick
        return None
    if snap is None:
        return None
    return extract_focused(snap)
