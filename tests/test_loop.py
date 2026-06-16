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
from aw_watcher_cmux import main as loop
from aw_watcher_cmux.main import CMUX_ERROR, NO_SOCKET, OK, build_event, poll_once
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
    result, status = poll_once(FakeConfig(), Normalizer())
    assert result is None
    assert status == NO_SOCKET


def test_poll_once_reports_cmux_error(monkeypatch):
    monkeypatch.setattr(cmux, "socket_available", lambda _p: True)
    def boom(_bin):
        raise cmux.CmuxError("cmux list-workspaces exited -13: ")
    monkeypatch.setattr(cmux, "get_focused", boom)
    result, status = poll_once(FakeConfig(), Normalizer())
    assert status == CMUX_ERROR
    assert "exited -13" in result  # message carried for the warning


def test_poll_once_returns_event_on_success(monkeypatch):
    monkeypatch.setattr(cmux, "socket_available", lambda _p: True)
    monkeypatch.setattr(cmux, "get_focused", lambda _bin: cmux.Focused(
        "workspace:1", "Certain QMS", "surface:32", "✳ refine reports"))
    ev, status = poll_once(FakeConfig(), Normalizer())
    assert status == OK
    assert ev.data["app"] == "Certain QMS"
    assert ev.data["is_agent"] is True


# --- run() warn-once diagnostic --------------------------------------------

class _FakeClient:
    def __init__(self):
        self.heartbeats = 0

    def heartbeat(self, *a, **k):
        self.heartbeats += 1


def _run_n_ticks(monkeypatch, statuses, runconfig):
    """Drive run() through a fixed sequence of poll_once outcomes, then stop.

    statuses: list of (result, status) tuples poll_once should yield in order.
    Returns (fake_client, list_of_warning_messages).
    """
    seq = iter(statuses)
    warnings = []

    def fake_poll(_cfg, _norm):
        try:
            return next(seq)
        except StopIteration:
            raise KeyboardInterrupt  # ends run() cleanly

    monkeypatch.setattr(loop, "poll_once", fake_poll)
    monkeypatch.setattr(loop.time, "sleep", lambda _s: None)  # no real waiting
    monkeypatch.setattr(loop.logger, "warning", lambda msg, *a: warnings.append(msg % a if a else msg))

    client = _FakeClient()
    try:
        loop.run(client, "bucket", runconfig)
    except KeyboardInterrupt:
        pass
    return client, warnings


@dataclass
class FakeRunConfig(FakeConfig):
    poll_interval: float = 0.0
    pulsetime: float = 5.0


def test_run_warns_once_after_persistent_cmux_errors(monkeypatch):
    errors = [("cmux list-workspaces exited -13: ", CMUX_ERROR)] * (
        loop.WARN_AFTER_CONSECUTIVE_ERRORS + 5)
    client, warnings = _run_n_ticks(monkeypatch, errors, FakeRunConfig())
    assert client.heartbeats == 0
    assert len(warnings) == 1  # warn-once, not every tick
    assert "cmuxOnly" in warnings[0]


def test_run_does_not_warn_on_transient_errors(monkeypatch):
    # A few errors, then success → below threshold, no warning, heartbeat sent.
    seq = [("e", CMUX_ERROR)] * 3 + [(_ok_event(), OK)]
    client, warnings = _run_n_ticks(monkeypatch, seq, FakeRunConfig())
    assert warnings == []
    assert client.heartbeats == 1


def test_run_no_socket_is_not_an_error(monkeypatch):
    seq = [(None, NO_SOCKET)] * (loop.WARN_AFTER_CONSECUTIVE_ERRORS + 5)
    client, warnings = _run_n_ticks(monkeypatch, seq, FakeRunConfig())
    assert warnings == []
    assert client.heartbeats == 0


def _ok_event():
    return build_event(
        cmux.Focused("workspace:1", "Certain QMS", "surface:1", "✳ work"), Normalizer())


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
