"""Fixture-based tests for the pure AX extraction logic (spec §4, §7).

These run offline against serialized AX trees — no live cmux or pyobjc needed.
The fixtures are the contract: a cmux UI change that breaks extraction fails
here.
"""
import json
from pathlib import Path

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
