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
        # sidebar workspace list, and not itself a workspace row).
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
