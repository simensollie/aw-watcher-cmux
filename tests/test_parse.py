"""Tests for cmux CLI output parsing (spec §3, §13)."""

from pathlib import Path

import pytest

from aw_watcher_cmux import cmux

FIXTURES = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_focused_line_picks_selected_workspace():
    line = cmux.focused_line(read("list-workspaces.txt"))
    assert line is not None
    ref, name = cmux.parse_line(line)
    assert ref == "workspace:1"
    assert name == "Certain QMS"


def test_focused_line_picks_selected_surface():
    line = cmux.focused_line(read("list-pane-surfaces.txt"))
    ref, title = cmux.parse_line(line)
    assert ref == "surface:54"
    assert title == "git log --oneline -20"


def test_parse_line_strips_leading_glyph_without_selected():
    ref, title = cmux.parse_line("  surface:32  ✳ refine reports")
    assert ref == "surface:32"
    assert title == "✳ refine reports"


def test_parse_line_preserves_bracketed_title():
    # Only the trailing marker is stripped; an in-title bracket survives (§13).
    ref, title = cmux.parse_line("* surface:9  git log [WIP commits]  [selected]")
    assert ref == "surface:9"
    assert title == "git log [WIP commits]"


def test_focused_line_fallback_to_star_when_no_marker():
    out = "  workspace:2  B\n* workspace:1  A\n"
    line = cmux.focused_line(out)
    ref, name = cmux.parse_line(line)
    assert ref == "workspace:1"
    assert name == "A"


def test_focused_line_none_when_nothing_selected():
    assert cmux.focused_line("  workspace:1  A\n  workspace:2  B\n") is None


def test_focused_line_ignores_blank_lines():
    assert cmux.focused_line("\n   \n") is None


def test_parse_line_empty_raises():
    with pytest.raises(cmux.CmuxError):
        cmux.parse_line("   ")
