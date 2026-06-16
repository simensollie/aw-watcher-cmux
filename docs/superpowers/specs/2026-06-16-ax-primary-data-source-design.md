# aw-watcher-cmux — AX-primary data source (design)

Status: Approved (brainstorm) · Date: 2026-06-16 · Supersedes the socket-poller
data source in the v0.1.0 spec (§3, §6, §12, §13).

## 1. Why this change

The v0.1.0 watcher reads cmux focus via the cmux CLI socket. Empirically
verified during testing:

- cmux's control socket uses `access_mode: cmuxOnly` and authorizes callers by
  their **cmux-surface context** (`cmux identify` returns a `caller` surface).
- A **detached** caller (launchd LaunchAgent, aw-qt-spawned process, `nohup`/`&`)
  has no caller surface, so cmux rejects every query and the CLI dies with
  SIGPIPE (exit `-13`/`141`). Confirmed even under a real LaunchAgent in the GUI
  session.
- cmux's `set-hook` accepts event names but fires **no focus events** (tested 11
  candidate names against a real focus change); there is no event-stream method.
  So cmux cannot push focus changes either.

Net: the socket only works when the watcher runs **inside a cmux surface**,
which blocks the standard ActivityWatch deployment (launchd / aw-qt).

The macOS **Accessibility API** reads cmux's UI tree from any process holding
Accessibility permission, independent of `cmuxOnly`. Cross-validated live: AX
yields the **same** focused workspace and selected surface title as the socket,
including agent glyphs:

```
SOCKET : workspace='Personal'  surface='⠐ Specify aw-watcher-cmux for ActivityWatch'
AX     : workspace='Personal'  selected='⠐ Specify aw-watcher-cmux for ActivityWatch'
```

AX is therefore the data source: full fidelity **and** a clean detached install.

## 2. Goals / non-goals

Goals
- Read focused workspace + selected surface title + agent-ness via AX.
- Run as a standard detached launchd/aw-qt service (no in-surface requirement).
- Tame UI-tree fragility with fixture-based tests and a live socket oracle.
- Preserve the existing bucket type and the meaningful event fields so aw
  categorization keeps working.

Non-goals
- No multi-app generalization (see §9). Build concrete for cmux.
- No event-driven capture (cmux exposes no focus events).
- No browser-URL capture (future).

## 3. Architecture & data flow

```
 poll (2s)   ┌────────────────────────────────────┐
 ┌─────────► │       aw-watcher-cmux (python)      │
 │  macOS    │  ax.py   : read cmux UI via AX      │ heartbeat
 │  Accessibility (pyobjc)  normalize.py            │──────────► aw-server
 │           │  main.py : poll loop                │   bucket: aw-watcher-cmux_<host>
 └───────────┤  cmux.py : socket = test oracle only│
             └────────────────────────────────────┘
```

The watcher over-emits (always reports cmux's internal focus) and "active cmux
time" is recovered query-side by intersecting with the window watcher
(app == cmux) and the AFK watcher, exactly as in the v0.1.0 spec §8. Unchanged.

## 4. Components

| Module | Role | Status |
|---|---|---|
| `ax.py` | NEW. Locate the cmux app (`com.cmuxterm.app` via `NSRunningApplication`), read focused workspace (focused window `AXTitle`) and selected surface title (focused tab in main content), `is_trusted()`, `snapshot()` (serialize the AX subtree to JSON for fixtures/diagnostics). Returns `Focused(workspace_name, workspace_index, surface_title)` or `None`. | new |
| `normalize.py` | title → `(label, is_agent)`; glyph rules incl. braille range. | unchanged |
| `main.py` | poll loop; calls `ax.get_focused()`; status `OK / NO_SOURCE / NOT_TRUSTED / AX_ERROR`; warn-once diagnostics. | adapt |
| `cmux.py` | socket parser, demoted to a verification oracle used by `--selfcheck` and tests. Off the hot path. | kept |
| `__main__.py` | add `--selfcheck`; check `ax.is_trusted()` on startup. | adapt |

### `ax.py` extraction contract

- **App:** first `NSRunningApplication` with bundleIdentifier `com.cmuxterm.app`;
  `None` if not running (→ `NO_SOURCE`).
- **Workspace:** `AXFocusedWindow.AXTitle`. `workspace_index` parsed when an
  `AXDescription` like `"<name>, workspace N of M"` is available for the focused
  workspace; else `None`.
- **Selected surface:** prefer `app.AXFocusedUIElement` and resolve its owning
  surface/tab title; fall back to the single `AXSelected == True` tab in the
  focused window's **main content** (exclude sidebar entries, whose
  `AXDescription` matches the `"…, workspace N of M"` pattern). If neither
  yields a title → `AX_ERROR` (do not guess).
- The exact attribute path is validated against the socket oracle (§7) and
  captured as fixtures; treat the precise walk as an implementation detail that
  tests pin down.

## 5. Event schema

AX cannot supply cmux's `workspace_ref`/`surface_ref`, so those diagnostic-only
fields are **dropped** (breaking vs v0.1.0 draft, but v0.1.0 is unreleased, so
impact is nil). `workspace_index` is added as a lightweight diagnostic. The
meaningful fields are unchanged, so aw categorization rules keep matching.

```json
{ "app": "Personal", "title": "Specify aw-watcher-cmux…", "is_agent": true, "workspace_index": 9 }
```

Bucket: `aw-watcher-cmux_<hostname>`, type `currentwindow` (unchanged).

## 6. Error handling

| Condition | Behaviour |
|---|---|
| cmux app not running | `NO_SOURCE` → skip tick → timeline gap (correct) |
| Process not AX-trusted | `NOT_TRUSTED` → loud one-time error with how-to-grant; keep retrying (permission may be granted while running) |
| AX tree present but expected nodes missing (cmux UI changed) | `AX_ERROR` → warn-once "cmux UI structure unexpected"; skip tick — never emit guessed data |
| aw-server down | `queued=True` heartbeats buffer and flush on reconnect (unchanged) |

The warn-once mechanism from v0.1.0 `main.run()` is reused: count consecutive
non-OK ticks while a source should be present; emit one actionable WARNING, then
stay quiet until recovery.

## 7. Testing strategy (this tames UI-tree fragility)

- **Fixture-based unit tests (`test_ax.py`):** `ax.snapshot()` serializes a real
  cmux AX subtree to `tests/fixtures/ax_tree_*.json` (focused-workspace,
  split-panes, browser-surface variants). Extraction runs against these offline,
  no live cmux. These snapshots are the contract; a cmux UI change that breaks
  extraction fails a test.
- **Live oracle (`--selfcheck`):** when run inside a cmux surface, asserts
  `ax.get_focused()` agrees with `cmux.get_focused()` (socket). Makes the manual
  cross-check we ran a permanent, repeatable regression guard.
- **e2e (`scripts/verify.sh`):** unchanged flow against an isolated test server,
  but now also passes when run **detached** — proving the install story.
- **Carried over:** `test_normalize.py`, `test_loop.py`, `test_config.py`.

## 8. Dependencies & deployment

- Add `pyobjc-framework-Cocoa` and `pyobjc-framework-ApplicationServices`
  (macOS-only; fits the existing macOS-only constraint).
- Deployment is the standard ActivityWatch path again: launchd LaunchAgent or
  aw-qt auto-discovery (`aw-watcher-*`). One-time **Accessibility** grant for the
  watcher (or aw-qt, which already holds it). Remove the `cmuxOnly` warning from
  the plist; document the permission grant in the README.

## 9. Future work

- **Other apps (deliberately deferred):** the AX plumbing (locate app → walk
  tree → emit `currentwindow` event) is reusable, but each app needs a bespoke
  extractor, and the high-value targets (browsers, editors) already have more
  robust official watchers (`aw-watcher-web`, editor plugins). Keep cmux
  extraction behind a small seam (app-locator + tree-walker → `Focused`) so a
  future per-app extractor can slot in, but do not abstract for a hypothetical
  second consumer now (YAGNI).
- **Browser surfaces:** capture URL/title for cmux browser surfaces.
- **cmux focus hooks:** if cmux later emits real focus events, add an
  event-driven adapter behind the same bucket to replace polling.
