"""Tests for the AX-driven poll loop: status reporting, event mapping, and the
warn-once diagnostics (spec §4, §6)."""
from dataclasses import dataclass, field

from aw_watcher_cmux import ax
from aw_watcher_cmux import main as loop
from aw_watcher_cmux.main import (
    OK, NO_SOURCE, NOT_TRUSTED, AX_ERROR, build_event, poll_once,
)
from aw_watcher_cmux.normalize import DEFAULT_AGENT_PATTERNS, Normalizer


@dataclass
class FakeConfig:
    agent_patterns: list = field(default_factory=lambda: list(DEFAULT_AGENT_PATTERNS))
    generic_terminal_label: str = "terminal"
    keep_command_name: bool = False
    poll_interval: float = 0.0
    pulsetime: float = 5.0


def _focused(title="✳ refine reports"):
    return ax.Focused(workspace_name="Acme Web", workspace_index=1, surface_title=title)


# --- poll_once status matrix -----------------------------------------------

def test_poll_once_not_trusted(monkeypatch):
    monkeypatch.setattr(ax, "is_trusted", lambda: False)
    result, status = poll_once(FakeConfig(), Normalizer())
    assert status == NOT_TRUSTED and result is None


def test_poll_once_no_source_when_cmux_absent(monkeypatch):
    monkeypatch.setattr(ax, "is_trusted", lambda: True)
    monkeypatch.setattr(ax, "cmux_pid", lambda: None)
    result, status = poll_once(FakeConfig(), Normalizer())
    assert status == NO_SOURCE and result is None


def test_poll_once_ax_error_when_extraction_fails(monkeypatch):
    monkeypatch.setattr(ax, "is_trusted", lambda: True)
    monkeypatch.setattr(ax, "cmux_pid", lambda: 123)
    monkeypatch.setattr(ax, "get_focused", lambda: None)
    result, status = poll_once(FakeConfig(), Normalizer())
    assert status == AX_ERROR


def test_poll_once_ok(monkeypatch):
    monkeypatch.setattr(ax, "is_trusted", lambda: True)
    monkeypatch.setattr(ax, "cmux_pid", lambda: 123)
    monkeypatch.setattr(ax, "get_focused", lambda: _focused())
    ev, status = poll_once(FakeConfig(), Normalizer())
    assert status == OK
    assert ev.data == {"app": "Acme Web", "title": "refine reports",
                       "is_agent": True, "workspace_index": 1}


# --- build_event ------------------------------------------------------------

def test_build_event_drops_index_when_none():
    ev = build_event(ax.Focused("Personal", None, "git status"), Normalizer())
    assert ev.data == {"app": "Personal", "title": "terminal", "is_agent": False}
    assert ev.timestamp.tzinfo is not None


# --- run() warn-once --------------------------------------------------------

class _FakeClient:
    def __init__(self):
        self.heartbeats = 0

    def heartbeat(self, *a, **k):
        self.heartbeats += 1


def _drive(monkeypatch, statuses):
    seq = iter(statuses)
    warnings = []

    def fake_poll(_cfg, _norm):
        try:
            return next(seq)
        except StopIteration:
            raise KeyboardInterrupt

    monkeypatch.setattr(loop, "poll_once", fake_poll)
    monkeypatch.setattr(loop.time, "sleep", lambda _s: None)
    monkeypatch.setattr(loop.logger, "warning", lambda msg, *a: warnings.append(msg % a if a else msg))
    client = _FakeClient()
    try:
        loop.run(client, "bucket", FakeConfig())
    except KeyboardInterrupt:
        pass
    return client, warnings


def test_run_warns_once_on_persistent_not_trusted(monkeypatch):
    client, warnings = _drive(monkeypatch, [(None, NOT_TRUSTED)] * (loop.WARN_AFTER_CONSECUTIVE_ERRORS + 5))
    assert client.heartbeats == 0
    assert len(warnings) == 1
    assert "Accessibility" in warnings[0]


def test_run_warns_once_on_persistent_ax_error(monkeypatch):
    client, warnings = _drive(monkeypatch, [(None, AX_ERROR)] * (loop.WARN_AFTER_CONSECUTIVE_ERRORS + 5))
    assert len(warnings) == 1
    assert "structure" in warnings[0].lower()


def test_run_no_source_does_not_warn(monkeypatch):
    client, warnings = _drive(monkeypatch, [(None, NO_SOURCE)] * (loop.WARN_AFTER_CONSECUTIVE_ERRORS + 5))
    assert warnings == [] and client.heartbeats == 0


def test_run_heartbeats_on_ok(monkeypatch):
    ev = build_event(_focused(), Normalizer())
    client, warnings = _drive(monkeypatch, [(ev, OK)] * 3)
    assert client.heartbeats == 3 and warnings == []


def test_run_does_not_warn_below_threshold(monkeypatch):
    # One short of the threshold, then a recovery → no warning ever fires.
    seq = [(None, AX_ERROR)] * (loop.WARN_AFTER_CONSECUTIVE_ERRORS - 1)
    seq += [(build_event(_focused(), Normalizer()), OK)]
    client, warnings = _drive(monkeypatch, seq)
    assert warnings == [] and client.heartbeats == 1


def test_run_rewarns_after_recovery(monkeypatch):
    # A full error burst warns once; after a recovery a second burst warns again.
    burst = [(None, AX_ERROR)] * loop.WARN_AFTER_CONSECUTIVE_ERRORS
    recovery = [(None, NO_SOURCE)]
    client, warnings = _drive(monkeypatch, burst + recovery + burst)
    assert len(warnings) == 2
