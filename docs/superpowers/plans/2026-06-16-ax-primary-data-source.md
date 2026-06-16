# AX-primary data source — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the cmux-socket data source with the macOS Accessibility (AX) API so the watcher reads focused workspace + selected surface from outside any cmux surface, enabling a normal detached launchd/aw-qt deployment.

**Architecture:** A pure extraction function consumes a serialized AX tree (plain dicts) and returns the focused workspace + selected surface title; a thin pyobjc layer produces that serialized tree live from the running cmux app. The poll loop calls the AX layer; the old cmux socket parser is kept only as a verification oracle (`--selfcheck`). UI-tree fragility is contained by fixture-based tests plus the live oracle.

**Tech Stack:** Python 3.10+, pyobjc (`pyobjc-framework-Cocoa`, `pyobjc-framework-ApplicationServices`), aw-client, pytest.

**Spec:** `docs/superpowers/specs/2026-06-16-ax-primary-data-source-design.md`

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `aw_watcher_cmux/ax.py` | NEW. `Focused` dataclass; pure `extract_focused(snapshot)`; live `is_trusted()`, `cmux_pid()`, `serialize_node()`, `snapshot_app()`, `get_focused()` | create |
| `aw_watcher_cmux/normalize.py` | title → `(label, is_agent)` | unchanged |
| `aw_watcher_cmux/main.py` | poll loop; `build_event` (new schema); `poll_once` (AX statuses); `run` (warn-once) | modify |
| `aw_watcher_cmux/cmux.py` | socket parser; now only the `--selfcheck` oracle | unchanged (kept) |
| `aw_watcher_cmux/__main__.py` | startup trust check; `--selfcheck`; `--snapshot` | modify |
| `tests/test_ax.py` | fixture-based extraction tests + skipped live smoke | create |
| `tests/fixtures/ax_tree_focused.json` | serialized AX tree (happy path) | create |
| `tests/fixtures/ax_tree_no_selection.json` | serialized AX tree, no main-content selection | create |
| `tests/test_loop.py` | rewritten for AX `poll_once`/`run` | modify |
| `pyproject.toml` | add pyobjc deps | modify |
| `packaging/com.activitywatch.aw-watcher-cmux.plist` | drop cmuxOnly warning | modify |
| `README.md` | Accessibility-permission install | modify |
| `scripts/verify.sh` | note it now works detached | modify |

---

## Task 1: Pure AX extraction core + fixtures

**Files:**
- Create: `aw_watcher_cmux/ax.py`
- Create: `tests/fixtures/ax_tree_focused.json`
- Create: `tests/fixtures/ax_tree_no_selection.json`
- Create: `tests/test_ax.py`

- [ ] **Step 1: Create the happy-path fixture**

Create `tests/fixtures/ax_tree_focused.json`. Each node is `{role,title,value,desc,selected,children}`. It models a focused window titled `Personal`, a sidebar (whose workspace rows have `desc` like `"<name>, workspace N of 9"`, with a *decoy* selected tab nested under the focused workspace row), and a main-content `AXSplitter` holding the genuinely selected surface.

```json
{
  "workspace": "Personal",
  "window": {
    "role": "AXWindow", "title": "Personal", "value": null, "desc": null, "selected": null,
    "children": [
      {
        "role": "AXStaticText", "title": null, "value": "Personal", "desc": null, "selected": null,
        "children": [
          {"role": "AXButton", "title": null, "value": null, "desc": "Certain QMS, workspace 1 of 9", "selected": null, "children": []},
          {"role": "AXButton", "title": null, "value": null, "desc": "Personal, workspace 9 of 9", "selected": null,
            "children": [
              {"role": "AXButton", "title": null, "value": "", "desc": "⠂ Fetch and ingest recent meetings from Plaud", "selected": true, "children": []}
            ]
          }
        ]
      },
      {
        "role": "AXSplitter", "title": null, "value": null, "desc": null, "selected": false,
        "children": [
          {"role": "AXButton", "title": null, "value": "", "desc": "⠐ Specify aw-watcher-cmux for ActivityWatch", "selected": true, "children": []},
          {"role": "AXButton", "title": null, "value": null, "desc": "terminal", "selected": null, "children": []}
        ]
      }
    ]
  }
}
```

- [ ] **Step 2: Create the no-selection fixture**

Create `tests/fixtures/ax_tree_no_selection.json` — same shape but the main-content tab is not selected (only the sidebar decoy is). Extraction must return `None` (→ `AX_ERROR`, never guess).

```json
{
  "workspace": "Personal",
  "window": {
    "role": "AXWindow", "title": "Personal", "value": null, "desc": null, "selected": null,
    "children": [
      {
        "role": "AXStaticText", "title": null, "value": "Personal", "desc": null, "selected": null,
        "children": [
          {"role": "AXButton", "title": null, "value": null, "desc": "Personal, workspace 9 of 9", "selected": null,
            "children": [
              {"role": "AXButton", "title": null, "value": "", "desc": "⠂ sidebar decoy", "selected": true, "children": []}
            ]
          }
        ]
      },
      {
        "role": "AXSplitter", "title": null, "value": null, "desc": null, "selected": false,
        "children": [
          {"role": "AXButton", "title": null, "value": "", "desc": "terminal", "selected": false, "children": []}
        ]
      }
    ]
  }
}
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_ax.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ax.py -v`
Expected: FAIL — `ImportError: cannot import name 'Focused' from 'aw_watcher_cmux.ax'` (module doesn't exist yet).

- [ ] **Step 5: Implement the pure extraction in `ax.py`**

Create `aw_watcher_cmux/ax.py` with the dataclass and pure logic (the live pyobjc layer is added in Task 2):

```python
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ax.py -v`
Expected: PASS (4 passed).

- [ ] **Step 7: Commit**

```bash
git add aw_watcher_cmux/ax.py tests/test_ax.py tests/fixtures/ax_tree_focused.json tests/fixtures/ax_tree_no_selection.json
git commit -m "feat(ax): pure AX-tree extraction of focused workspace + surface"
```

---

## Task 2: Live AX layer (pyobjc) + deps

**Files:**
- Modify: `aw_watcher_cmux/ax.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_ax.py` (add skipped live smoke test)

- [ ] **Step 1: Add pyobjc dependencies**

Edit `pyproject.toml`, replace the `dependencies` array:

```toml
dependencies = [
    # >=0.5 for client_hostname and queued heartbeat/create_bucket support.
    "aw-client>=0.5.13",
    # macOS Accessibility API access (read cmux's focused workspace/surface).
    "pyobjc-framework-Cocoa>=10",
    "pyobjc-framework-ApplicationServices>=10",
]
```

- [ ] **Step 2: Install the new deps**

Run: `.venv/bin/pip install -q -e ".[dev]"`
Expected: completes; `\.venv/bin/python -c "import ApplicationServices, AppKit; print('ok')"` prints `ok`.

- [ ] **Step 3: Append the live layer to `ax.py`**

Add to the end of `aw_watcher_cmux/ax.py`:

```python
# --- live layer (pyobjc) ----------------------------------------------------
# Imported lazily so the pure extractor (and its tests) work without pyobjc.

CMUX_BUNDLE_ID = "com.cmuxterm.app"

# AX attribute names we read per node.
_ATTRS = ("AXRole", "AXTitle", "AXValue", "AXDescription", "AXSelected", "AXChildren")
_MAX_DEPTH = 12


def is_trusted() -> bool:
    """True if this process may use the Accessibility API."""
    from ApplicationServices import AXIsProcessTrusted
    return bool(AXIsProcessTrusted())


def cmux_pid() -> int | None:
    """PID of the running cmux app, or None if cmux isn't running."""
    from AppKit import NSWorkspace
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.bundleIdentifier() == CMUX_BUNDLE_ID:
            return int(app.processIdentifier())
    return None


def _copy(el, attr):
    from ApplicationServices import AXUIElementCopyAttributeValue
    err, val = AXUIElementCopyAttributeValue(el, attr, None)
    return val if err == 0 else None


def serialize_node(el, depth: int = 0) -> dict:
    """Recursively serialize an AXUIElement into the plain-dict shape that
    extract_focused() consumes."""
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
    snap = snapshot_app(pid)
    if snap is None:
        return None
    return extract_focused(snap)
```

- [ ] **Step 4: Add a skipped-by-default live smoke test**

Append to `tests/test_ax.py`:

```python
import os
import pytest
from aw_watcher_cmux import ax


@pytest.mark.skipif(
    os.environ.get("AX_LIVE") != "1",
    reason="live AX smoke test; set AX_LIVE=1 and run inside cmux on macOS",
)
def test_live_get_focused_smoke():
    assert ax.is_trusted(), "grant Accessibility permission to run this"
    assert ax.cmux_pid() is not None, "cmux must be running"
    f = ax.get_focused()
    assert f is not None and f.workspace_name and f.surface_title
```

- [ ] **Step 5: Run the suite (live test skipped)**

Run: `.venv/bin/pytest tests/test_ax.py -v`
Expected: PASS for the 4 pure tests; `test_live_get_focused_smoke` SKIPPED.

- [ ] **Step 6: Manually confirm the live layer (inside a cmux surface)**

Run: `AX_LIVE=1 .venv/bin/pytest tests/test_ax.py::test_live_get_focused_smoke -v`
Expected: PASS (cmux running, permission granted). If it FAILS, capture a fresh fixture with `--snapshot` (Task 4) and adjust `extract_focused`; do not weaken the assertions.

- [ ] **Step 7: Commit**

```bash
git add aw_watcher_cmux/ax.py pyproject.toml tests/test_ax.py
git commit -m "feat(ax): live pyobjc layer reading cmux focus via Accessibility API"
```

---

## Task 3: Switch the poll loop to AX

**Files:**
- Modify: `aw_watcher_cmux/main.py`
- Modify: `tests/test_loop.py`

- [ ] **Step 1: Rewrite the failing loop tests for AX**

Replace the entire contents of `tests/test_loop.py`:

```python
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
    return ax.Focused(workspace_name="Certain QMS", workspace_index=1, surface_title=title)


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
    assert ev.data == {"app": "Certain QMS", "title": "refine reports",
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
    client, warnings = _drive(monkeypatch, [("x", AX_ERROR)] * (loop.WARN_AFTER_CONSECUTIVE_ERRORS + 5))
    assert len(warnings) == 1
    assert "structure" in warnings[0].lower()


def test_run_no_source_does_not_warn(monkeypatch):
    client, warnings = _drive(monkeypatch, [(None, NO_SOURCE)] * (loop.WARN_AFTER_CONSECUTIVE_ERRORS + 5))
    assert warnings == [] and client.heartbeats == 0


def test_run_heartbeats_on_ok(monkeypatch):
    ev = build_event(_focused(), Normalizer())
    client, warnings = _drive(monkeypatch, [(ev, OK)] * 3)
    assert client.heartbeats == 3 and warnings == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_loop.py -v`
Expected: FAIL — `ImportError` for `NOT_TRUSTED`/`AX_ERROR` and `ax.Focused` usage in `build_event` (main.py not updated yet).

- [ ] **Step 3: Rewrite `main.py`**

Replace the entire contents of `aw_watcher_cmux/main.py`:

```python
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
        "a fresh tree with `aw-watcher-cmux --snapshot` and file an issue. "
        "(consecutive failures: %s)"
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


def poll_once(config, normalizer: Normalizer) -> tuple[object, str]:
    """One poll tick → (event_or_None, status). On OK the first element is the
    Event; otherwise it's None (or a count for AX_ERROR logging)."""
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
    while True:
        time.sleep(config.poll_interval)
        result, status = poll_once(config, normalizer)

        if status == OK:
            consecutive = 0
            warned_status = None
            client.heartbeat(bucket_id, result, pulsetime=config.pulsetime, queued=True)
            logger.debug("heartbeat: app=%r title=%r is_agent=%s",
                         result.data["app"], result.data["title"], result.data["is_agent"])
        elif status == NO_SOURCE:
            consecutive = 0
            warned_status = None
            logger.debug("cmux not running; skipping tick (gap)")
        else:  # NOT_TRUSTED or AX_ERROR
            consecutive += 1
            if consecutive >= WARN_AFTER_CONSECUTIVE_ERRORS and warned_status != status:
                logger.warning(_HINTS[status], consecutive)
                warned_status = status
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_loop.py -v`
Expected: PASS (all loop tests).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (test_ax, test_loop, test_normalize, test_config, test_parse).

- [ ] **Step 6: Commit**

```bash
git add aw_watcher_cmux/main.py tests/test_loop.py
git commit -m "feat: drive the poll loop from AX; new statuses + event schema"
```

---

## Task 4: `__main__` — startup trust check, `--selfcheck`, `--snapshot`

**Files:**
- Modify: `aw_watcher_cmux/__main__.py`

- [ ] **Step 1: Add the three behaviours to `__main__.py`**

In `aw_watcher_cmux/__main__.py`, add imports near the top (after the existing `from . import main as loop`):

```python
import json

from . import ax
from . import cmux
```

Add these argparse flags inside `parse_args`, before the final `return`:

```python
    p.add_argument("--selfcheck", action="store_true",
                   help="compare AX reading against the cmux socket oracle and exit "
                        "(run inside a cmux surface)")
    p.add_argument("--snapshot", action="store_true",
                   help="print cmux's serialized AX tree as JSON and exit "
                        "(for capturing test fixtures)")
```

Add these helpers at module level (above `main`):

```python
def run_snapshot() -> int:
    pid = ax.cmux_pid()
    if pid is None:
        print("cmux is not running")
        return 1
    print(json.dumps(ax.snapshot_app(pid), ensure_ascii=False, indent=2))
    return 0


def run_selfcheck(config) -> int:
    """Assert AX agrees with the cmux socket oracle on workspace + surface."""
    ax_f = ax.get_focused()
    try:
        sock = cmux.get_focused(config.cmux_bin)
    except cmux.CmuxError as exc:
        print(f"socket oracle unavailable (run inside a cmux surface): {exc}")
        return 1
    print(f"AX     : {ax_f}")
    print(f"SOCKET : workspace={sock.workspace_name!r} surface={sock.surface_title!r}")
    ok = (ax_f is not None
          and ax_f.workspace_name == sock.workspace_name
          and ax_f.surface_title == sock.surface_title)
    print("MATCH" if ok else "MISMATCH")
    return 0 if ok else 2
```

In `main()`, immediately after `config = load_config(args)`, handle the early-exit modes:

```python
    if args.snapshot:
        return run_snapshot()
    if args.selfcheck:
        return run_selfcheck(config)
    if not ax.is_trusted():
        logger.error(loop._HINTS[loop.NOT_TRUSTED])
```

(The `logger.error` is a startup hint; the loop still runs and will retry once permission is granted, surfacing the warn-once message too.)

- [ ] **Step 2: Verify `--snapshot` works (inside a cmux surface)**

Run: `.venv/bin/python -m aw_watcher_cmux --snapshot | head -5`
Expected: prints JSON beginning with `{` and a `"workspace":` key.

- [ ] **Step 3: Capture the live snapshot as a refreshed fixture (optional but recommended)**

Run: `.venv/bin/python -m aw_watcher_cmux --snapshot > tests/fixtures/ax_tree_live.json`
Then sanity-check extraction against it:
Run: `.venv/bin/python -c "import json,aw_watcher_cmux.ax as a; print(a.extract_focused(json.load(open('tests/fixtures/ax_tree_live.json'))))"`
Expected: prints a `Focused(...)` matching the currently focused cmux tab. If it prints `None`, adjust `extract_focused` (Task 1) and re-run its tests. Delete the temp fixture afterward unless you want to keep it: `git checkout -- . ` is not needed since it's untracked — `rm tests/fixtures/ax_tree_live.json` if discarding.

- [ ] **Step 4: Verify `--selfcheck` (inside a cmux surface)**

Run: `.venv/bin/python -m aw_watcher_cmux --selfcheck`
Expected: prints `AX     : Focused(...)`, `SOCKET : ...`, and `MATCH`.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add aw_watcher_cmux/__main__.py
git commit -m "feat: --selfcheck (AX vs socket oracle), --snapshot, startup trust check"
```

---

## Task 5: Deployment docs & packaging

**Files:**
- Modify: `packaging/com.activitywatch.aw-watcher-cmux.plist`
- Modify: `README.md`
- Modify: `scripts/verify.sh`

- [ ] **Step 1: Remove the cmuxOnly warning from the plist**

In `packaging/com.activitywatch.aw-watcher-cmux.plist`, replace the `⚠️ KNOWN LIMITATION ...` comment block (the one added in commit 52366f1) with:

```
  Runs as a normal LaunchAgent. aw-watcher-cmux reads cmux focus via the macOS
  Accessibility API, so it must run in the GUI (Aqua) session and the executable
  (or aw-qt, if it manages this watcher) needs Accessibility permission in
  System Settings > Privacy & Security > Accessibility.
```

- [ ] **Step 2: Rewrite the README "must run inside a cmux surface" section**

In `README.md`, replace the entire `## ⚠️ Must run inside a cmux surface` section with:

```markdown
## Requires Accessibility permission

aw-watcher-cmux reads cmux's focused workspace and selected tab via the macOS
**Accessibility API** (it does not use cmux's access-gated control socket). Grant
the permission once:

- **System Settings → Privacy & Security → Accessibility** → add the
  `aw-watcher-cmux` executable, or **aw-qt** if it manages the watcher (aw-qt
  usually already has it for the window watcher).

The watcher must run in your GUI login session (launchd LaunchAgent or aw-qt —
both do). Without the permission it records nothing and logs a one-time warning
telling you to grant it.
```

- [ ] **Step 3: Update the README Install section**

In `README.md`, replace the `### Run it (inside a cmux surface)` and
`### External socket auth (experimental)` subsections with:

```markdown
### As an aw-qt-managed watcher (recommended)

`pipx install .` then let aw-qt auto-discover it (it starts executables named
`aw-watcher-*`). Ensure aw-qt has Accessibility permission.

### Standalone / launchd

`pipx install .` then run `aw-watcher-cmux`, or install the LaunchAgent at
[`packaging/`](packaging/) (edit paths, `launchctl bootstrap gui/$(id -u) <plist>`).
Grant the executable Accessibility permission.

### Verify your setup

From inside a cmux tab, confirm the AX reading matches cmux's own answer:

    aw-watcher-cmux --selfcheck      # prints AX vs SOCKET and MATCH/MISMATCH
```

- [ ] **Step 4: Update the verify.sh header note**

In `scripts/verify.sh`, replace the comment line
`# cmux terminal tab. If you run it detached, cmux rejects every query (SIGPIPE)`
and the surrounding `IMPORTANT:` paragraph with:

```bash
# aw-watcher-cmux reads cmux focus via the macOS Accessibility API, so this
# works whether or not it runs inside a cmux surface — but the process needs
# Accessibility permission (System Settings > Privacy & Security > Accessibility).
```

Also remove the `if grep -q "exited -13\|cmuxOnly" ...` failure block (the
SIGPIPE symptom no longer applies) and replace its trigger string check with a
permission check:

```bash
if grep -q "Accessibility permission" /tmp/aw-verify-watcher.log; then
  echo
  echo "FAIL: missing Accessibility permission. Grant it in System Settings >"
  echo "      Privacy & Security > Accessibility, then re-run."
  exit 2
fi
```

- [ ] **Step 5: Run the full suite + a detached verify**

Run: `.venv/bin/pytest -q`
Expected: PASS.
Run: `PY=.venv/bin/python DURATION=6 scripts/verify.sh`
Expected: `PASS: N event(s)` — and it should now also pass if launched detached.

- [ ] **Step 6: Commit**

```bash
git add packaging/com.activitywatch.aw-watcher-cmux.plist README.md scripts/verify.sh
git commit -m "docs: AX deployment (Accessibility permission), drop cmuxOnly caveats"
```

---

## Task 6: Final verification & spec sync

**Files:**
- Modify: `README.md` (event-data table)
- Modify: `docs/superpowers/specs/2026-06-16-ax-primary-data-source-design.md` (mark Implemented)

- [ ] **Step 1: Update the README event-data table**

In `README.md`, update the `### Event data` table to the AX schema: rows `app`
(workspace name), `title` (normalized tab label), `is_agent` (bool),
`workspace_index` (int, diagnostic). Remove the `workspace_ref`/`surface_ref`
rows.

- [ ] **Step 2: Mark the spec implemented**

In the spec header, change `Status: Approved (brainstorm)` to
`Status: Implemented`.

- [ ] **Step 3: Full suite + selfcheck gate**

Run: `.venv/bin/pytest -q`
Expected: PASS.
Run: `.venv/bin/python -m aw_watcher_cmux --selfcheck`
Expected: `MATCH`.

- [ ] **Step 4: Commit and push**

```bash
git add README.md docs/
git commit -m "docs: sync README event schema + mark AX spec implemented"
git push origin main
```

---

## Self-review notes

- **Spec coverage:** §3 architecture → Tasks 1-3; §4 extraction contract → Task 1 (+live Task 2); §5 schema → Task 3 `build_event` + Task 6 docs; §6 error handling → Task 3 statuses/warn-once; §7 testing (fixtures + `--selfcheck` + verify.sh) → Tasks 1, 4, 5; §8 deps/deployment → Tasks 2, 5. §9 future work → no task (correct; deferred).
- **AXFocusedUIElement note:** the spec lists it as the *preferred* selector with AXSelected-in-main-content as fallback. This plan implements the validated AXSelected-main-content rule only. If `--selfcheck` shows MISMATCH on split-pane layouts, add an AXFocusedUIElement resolution pass to `get_focused` (capture the focused element's `AXDescription` and prefer it) — a follow-up task, not needed for the happy path.
- **Type consistency:** `ax.Focused(workspace_name, workspace_index, surface_title)` is used identically in `extract_focused`, `build_event`, and the tests. `cmux.Focused` (oracle) is distinct and only compared field-wise in `run_selfcheck`.
