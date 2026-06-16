"""Tests for the poll loop's resilience and event construction (spec §6, §13).

The watcher's headline guarantee is "never crash the loop": every cmux failure
mode must degrade to a skipped tick, not an exception. These tests exercise
cmux.run / get_focused error paths, poll_once's skip behavior, and build_event
field mapping with subprocess and the socket check monkeypatched.
"""

import subprocess
from dataclasses import dataclass, field

import pytest

from aw_watcher_cmux import cmux
from aw_watcher_cmux.main import build_event, poll_once
from aw_watcher_cmux.normalize import DEFAULT_AGENT_PATTERNS, Normalizer


@dataclass
class FakeConfig:
    cmux_bin: str = "cmux"
    socket_path: str | None = "/tmp/cmux.sock"
    agent_patterns: list = field(default_factory=lambda: list(DEFAULT_AGENT_PATTERNS))
    generic_terminal_label: str = "terminal"
    keep_command_name: bool = False


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["cmux"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


# --- cmux.run error mapping ------------------------------------------------

def test_run_raises_cmuxerror_on_timeout(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="cmux", timeout=1.0)
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(cmux.CmuxError):
        cmux.run("cmux", "list-workspaces")


def test_run_raises_cmuxerror_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(returncode=2, stderr="nope"))
    with pytest.raises(cmux.CmuxError):
        cmux.run("cmux", "bogus")


def test_run_raises_cmuxerror_on_missing_binary(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("no cmux")
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(cmux.CmuxError):
        cmux.run("cmux", "list-workspaces")


# --- get_focused ------------------------------------------------------------

def test_get_focused_happy_path(monkeypatch):
    outputs = {
        ("list-workspaces",): "  workspace:2  B\n* workspace:1  Certain QMS  [selected]\n",
        ("list-pane-surfaces", "--workspace", "workspace:1"): "* surface:32  ✳ refine reports  [selected]\n",
    }
    monkeypatch.setattr(cmux, "run", lambda _bin, *args, **k: outputs[args])
    f = cmux.get_focused("cmux")
    assert f.workspace_ref == "workspace:1"
    assert f.workspace_name == "Certain QMS"
    assert f.surface_ref == "surface:32"
    assert f.surface_title == "✳ refine reports"


def test_get_focused_no_selected_workspace_raises(monkeypatch):
    monkeypatch.setattr(cmux, "run", lambda _bin, *a, **k: "  workspace:1  A\n")
    with pytest.raises(cmux.CmuxError):
        cmux.get_focused("cmux")


def test_get_focused_no_selected_surface_raises(monkeypatch):
    def fake(_bin, *args, **k):
        if args[0] == "list-workspaces":
            return "* workspace:1  A  [selected]\n"
        return "  surface:1  shell\n"  # nothing selected
    monkeypatch.setattr(cmux, "run", fake)
    with pytest.raises(cmux.CmuxError):
        cmux.get_focused("cmux")


# --- poll_once skip behavior ------------------------------------------------

def test_poll_once_skips_when_socket_missing(monkeypatch):
    monkeypatch.setattr(cmux, "socket_available", lambda _p: False)
    assert poll_once(FakeConfig(), Normalizer()) is None


def test_poll_once_skips_on_cmuxerror(monkeypatch):
    monkeypatch.setattr(cmux, "socket_available", lambda _p: True)
    def boom(_bin):
        raise cmux.CmuxError("transient")
    monkeypatch.setattr(cmux, "get_focused", boom)
    assert poll_once(FakeConfig(), Normalizer()) is None


def test_poll_once_returns_event_on_success(monkeypatch):
    monkeypatch.setattr(cmux, "socket_available", lambda _p: True)
    monkeypatch.setattr(cmux, "get_focused", lambda _bin: cmux.Focused(
        "workspace:1", "Certain QMS", "surface:32", "✳ refine reports"))
    ev = poll_once(FakeConfig(), Normalizer())
    assert ev is not None
    assert ev.data["app"] == "Certain QMS"
    assert ev.data["is_agent"] is True


# --- build_event ------------------------------------------------------------

def test_build_event_maps_all_fields_and_normalizes():
    focused = cmux.Focused("workspace:1", "Certain QMS", "surface:54", "git log --oneline")
    ev = build_event(focused, Normalizer())
    assert ev.data == {
        "app": "Certain QMS",
        "title": "terminal",  # plain shell collapsed
        "is_agent": False,
        "workspace_ref": "workspace:1",
        "surface_ref": "surface:54",
    }
    assert ev.timestamp.tzinfo is not None  # tz-aware (aw requires it)
