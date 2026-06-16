"""Fixture-based tests for the pure AX extraction logic (spec §4, §7).

These run offline against serialized AX trees — no live cmux or pyobjc needed.
The fixtures are the contract: a cmux UI change that breaks extraction fails
here.
"""
import json
import os
from pathlib import Path

import pytest

from aw_watcher_cmux import ax
from aw_watcher_cmux.ax import Focused, extract_focused

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def test_extracts_focused_workspace_and_main_content_surface():
    f = extract_focused(load("ax_tree_focused.json"))
    assert f == Focused(
        workspace_name="Personal",
        workspace_index=9,
        surface_title="⠐ Specify aw-watcher-cmux for ActivityWatch",
    )


def test_sidebar_selection_is_excluded():
    # The only selected tab is a sidebar decoy under a "workspace N of 9" row.
    f = extract_focused(load("ax_tree_no_selection.json"))
    assert f is None


def test_returns_none_when_workspace_missing():
    assert extract_focused({"workspace": None, "window": {"role": "AXWindow", "children": []}}) is None


def test_returns_none_when_window_missing():
    assert extract_focused({"workspace": "Personal", "window": None}) is None


def test_workspace_index_none_when_row_absent():
    # A valid main-content selection with no matching sidebar row → index None
    # (a valid non-error result, not None for the whole Focused).
    snap = {
        "workspace": "Work",
        "window": {
            "desc": None, "selected": None, "children": [
                {"desc": "⠐ some task", "selected": True, "children": []},
            ],
        },
    }
    assert extract_focused(snap) == Focused(
        workspace_name="Work", workspace_index=None, surface_title="⠐ some task")


@pytest.mark.skipif(
    os.environ.get("AX_LIVE") != "1",
    reason="live AX smoke test; set AX_LIVE=1 and run inside cmux on macOS",
)
def test_live_get_focused_smoke():
    assert ax.is_trusted(), "grant Accessibility permission to run this"
    assert ax.cmux_pid() is not None, "cmux must be running"
    f = ax.get_focused()
    assert f is not None and f.workspace_name and f.surface_title
