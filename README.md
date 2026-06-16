# aw-watcher-cmux

An [ActivityWatch](https://activitywatch.net/) watcher that records which
[cmux](https://cmux.app/) **workspace** (project) and **tab/surface** (terminal
or agent session) is focused over time.

It polls the cmux Unix-socket CLI, normalizes noisy terminal titles into
agent-aware labels, and pushes heartbeats to the local `aw-server`. Actual
"active time" is derived at query time by intersecting with the window watcher
(cmux frontmost) and the AFK watcher (user present).

macOS only (cmux is macOS only).

## What you get

- **Per-workspace dwell time** — Certain QMS vs Pensieve vs Timesheet, etc.
- **Per-tab granularity** that distinguishes meaningful agent sessions
  (`✳ refine reports`) from plain-shell churn.
- **Zero configuration** to start; a standard `currentwindow` bucket so the
  existing aw UI and categorization rules just work.

## How it works

```
   poll (2s)     ┌─────────────────────────────┐
   ┌──────────►  │   aw-watcher-cmux (python)   │
   │             │  cmux.py: run + parse CLI    │
cmux Unix socket │  normalize.py: title rules   │ heartbeat
 (/tmp/cmux.sock)│  main.py: poll loop          │──────────►  aw-server
   ◄─────────────┘                              │             :5600
                 └─────────────────────────────┘   bucket: aw-watcher-cmux_<host>
```

The watcher is intentionally dumb: it always emits the focused workspace/surface,
even when cmux is not the frontmost macOS app (cmux has no "am I frontmost"
query). Correctness is restored at query time by intersecting with the window
and AFK watchers — the same separation of concerns ActivityWatch already uses
for AFK.

### Event data

| Field | Type | Example | Notes |
|---|---|---|---|
| `app` | string | `Certain QMS` | Focused workspace name (the project dimension) |
| `title` | string | `✳ refine reports` / `terminal` | Normalized tab label |
| `is_agent` | bool | `true` | Whether the surface is an agent session |
| `workspace_ref` | string | `workspace:1` | cmux ref (diagnostic) |
| `surface_ref` | string | `surface:32` | cmux ref (diagnostic) |

Using `app`/`title` (not custom keys) is deliberate: aw heartbeat-merges
consecutive identical events and its categorization rules match these keys.
Plain-shell live command lines are collapsed to a single `terminal` label so
they merge into long blocks instead of fragmenting the timeline.

## ⚠️ Must run inside a cmux surface

cmux's control socket authorizes callers by their **cmux-surface context**
(`cmux identify` reports a `caller` surface). Its default access mode is
`cmuxOnly`: only processes running **inside a cmux terminal surface** are
allowed. A detached process — a launchd `LaunchAgent`, an aw-qt-spawned
watcher, anything `nohup`/`&`-orphaned — has no caller surface and every query
is **rejected and killed with SIGPIPE**, so the watcher records nothing.

This was verified empirically (including under a real `LaunchAgent` in the GUI
session). Consequence: **run `aw-watcher-cmux` from inside a cmux tab.** The
classic ActivityWatch deployment paths (standalone launchd, aw-qt management)
do not work unless cmux exposes external socket auth (see below).

## Install

```bash
pipx install .          # or: make install   (into a local .venv)
```

### Run it (inside a cmux surface)

Open a dedicated cmux tab and run:

```bash
aw-watcher-cmux --verbose
```

To auto-start it, use a cmux startup command for a workspace, e.g.:

```bash
cmux new-workspace --command "aw-watcher-cmux"
```

so the watcher launches inside a real surface every time. (A `launchd` plist is
included at [`packaging/`](packaging/) for reference, but see the warning above
— it will not work until external socket auth is configured.)

### External socket auth (experimental)

cmux's CLI supports `--password` / `CMUX_SOCKET_PASSWORD` / a keychain password
and an `auth.login` method. If you set a socket password in cmux Settings and
switch the access mode away from `cmuxOnly`, a detached watcher *may* be able to
authenticate. This is unverified and depends on your cmux version — test with
`scripts/verify.sh` after configuring.

## Configuration

Config lives at
`~/Library/Application Support/activitywatch/aw-watcher-cmux/aw-watcher-cmux.toml`.
See [`aw-watcher-cmux.toml.example`](aw-watcher-cmux.toml.example). CLI flags
override the file.

| Key | Default | Meaning |
|---|---|---|
| `poll_interval` | `2.0` | Seconds between polls |
| `pulsetime` | `5.0` | Heartbeat merge window |
| `agent_patterns` | see example | Regexes marking a surface as an agent session |
| `generic_terminal_label` | `terminal` | Label for non-agent surfaces |
| `keep_command_name` | `false` | Store first command token instead of the generic label |
| `cmux_bin` | `cmux` | Path to the cmux CLI |
| `socket_path` | `$CMUX_SOCKET_PATH` or `/tmp/cmux.sock` | Override socket |

CLI flags override the file: `--testing` (aw test server on port 5666 +
`-testing` bucket suffix), `--verbose`, `--cmux-bin`, `--socket-path`,
`--poll-interval`, `--pulsetime`, `--generic-terminal-label`,
`--keep-command-name`. `agent_patterns` is a list of regexes and is configured
in the file only.

## Active cmux time (aw query)

The watcher over-emits; "active cmux time" is computed by intersecting three
buckets:

```python
afk     = flood(query_bucket(find_bucket("aw-watcher-afk_")))
window  = flood(query_bucket(find_bucket("aw-watcher-window_")))
cmux    = flood(query_bucket(find_bucket("aw-watcher-cmux_")))
not_afk = filter_keyvals(afk, "status", ["not-afk"])
in_cmux = filter_keyvals(window, "app", ["cmux"])
events  = filter_period_intersect(cmux, in_cmux)   # only when cmux is frontmost
events  = filter_period_intersect(events, not_afk) # only when user present
RETURN  = merge_events_by_keys(events, ["app", "title"])
```

Drop the `not_afk` intersection for a view of unattended agent time on purpose.

## Verify it works

Unit tests (no cmux or aw-server needed):

```bash
make test          # or: pytest -q
```

End-to-end, against an **isolated** aw test server on port 5666 (never touches
your real data on 5600). **Run this from inside a cmux tab:**

```bash
make verify        # or: scripts/verify.sh
```

It starts `aw-server --testing`, runs the watcher for a few seconds, and asserts
events landed in `aw-watcher-cmux_<host>-testing`. If cmux rejects the queries
(the `cmuxOnly` problem above), the script says so explicitly.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Timeline has a permanent gap; `--verbose` logs `cmux list-workspaces exited -13` and a warning mentioning `cmuxOnly` | The watcher is detached / outside a cmux surface. cmux is rejecting it. Run it from inside a cmux tab (see the warning at the top). |
| No bucket created | aw-server isn't reachable. Heartbeats are queued and flushed on reconnect; start aw-server. |
| Plain-shell tabs all show as `terminal` | Expected — non-agent titles collapse to one label (set `keep_command_name = true` to keep the command). |

## Development

```bash
make install       # editable install + dev deps into .venv
make test
```

## License

[MPL-2.0](LICENSE), matching the ActivityWatch ecosystem.
