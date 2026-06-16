# aw-watcher-cmux

An [ActivityWatch](https://activitywatch.net/) watcher that records which
[cmux](https://cmux.app/) **workspace** (project) and **tab/surface** (terminal
or agent session) is focused over time.

It reads the focused cmux workspace and tab from the macOS Accessibility API,
normalizes noisy terminal titles into agent-aware labels, and pushes heartbeats
to the local `aw-server`. Actual "active time" is derived at query time by
intersecting with the window watcher (cmux frontmost) and the AFK watcher (user
present).

macOS only (cmux is macOS only).

## What you get

- **Per-workspace dwell time** — e.g. Acme Web vs Billing API vs Infra.
- **Per-tab granularity** that distinguishes meaningful agent sessions
  (`✳ refine reports`) from plain-shell churn.
- **Zero configuration** to start; a standard `currentwindow` bucket so the
  existing aw UI and categorization rules just work.

## How it works

```
   poll (2s)     ┌─────────────────────────────┐
   ┌──────────►  │   aw-watcher-cmux (python)   │
   │             │  ax.py: read cmux UI via AX  │
 macOS Accessibility  normalize.py: title rules │ heartbeat
   │  (pyobjc)   │  main.py: poll loop          │──────────►  aw-server
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
| `app` | string | `Acme Web` | Focused workspace name (the project dimension) |
| `title` | string | `✳ refine reports` / `terminal` | Normalized tab label |
| `is_agent` | bool | `true` | Whether the surface is an agent session |
| `workspace_index` | int | `1` | 1-based position of the workspace (diagnostic) |

Using `app`/`title` (not custom keys) is deliberate: aw heartbeat-merges
consecutive identical events and its categorization rules match these keys.
Plain-shell live command lines are collapsed to a single `terminal` label so
they merge into long blocks instead of fragmenting the timeline.

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

## Install

```bash
git clone https://github.com/simensollie/aw-watcher-cmux
cd aw-watcher-cmux
./scripts/install.sh
```

This creates a self-contained venv at `~/.local/share/aw-watcher-cmux`, installs
a launchd LaunchAgent that auto-starts the watcher at login (and survives
ActivityWatch updates — nothing is written into the AW app), and triggers the
macOS Accessibility prompt. Then do the one manual step it prints:

1. **System Settings → Privacy & Security → Accessibility** → enable the toggle
   for `aw-watcher-cmux` (the installer already added it).
2. Restart it: `launchctl kickstart -k gui/$(id -u)/com.activitywatch.aw-watcher-cmux`

Uninstall any time with `./scripts/uninstall.sh`.

> **Why launchd and not the ActivityWatch tray?** The bundled `aw-qt` only
> discovers watchers inside its own app bundle and on the bare GUI `PATH`, so
> registering there means hand-editing `/Applications/ActivityWatch.app` — which
> breaks on every AW update. launchd is more robust for a custom watcher.

### Verify your setup

From inside a cmux tab, confirm the AX reading matches cmux's own answer:

    ~/.local/share/aw-watcher-cmux/venv/bin/aw-watcher-cmux --selfcheck   # prints MATCH

### Manual / development

```bash
make install        # editable venv install for hacking
make test
~/.local/share/aw-watcher-cmux/venv/bin/aw-watcher-cmux --verbose   # or run it by hand in a cmux tab
```

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
| `cmux_bin` | `cmux` | Path to the cmux CLI (used by `--selfcheck` oracle) |
| `socket_path` | `$CMUX_SOCKET_PATH` or `/tmp/cmux.sock` | cmux socket for the `--selfcheck` oracle (the watcher itself uses AX, not the socket) |

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
your real data on 5600):

```bash
make verify        # or: scripts/verify.sh
```

It starts `aw-server --testing`, runs the watcher for a few seconds, and asserts
events landed in `aw-watcher-cmux_<host>-testing`. If the Accessibility permission
is missing, the script says so explicitly.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Timeline has a permanent gap; `--verbose` logs a warning about Accessibility permission | The watcher lacks Accessibility permission. Grant it in System Settings → Privacy & Security → Accessibility, then restart. |
| No bucket created | aw-server isn't reachable. Heartbeats are queued and flushed on reconnect; start aw-server. |
| Plain-shell tabs all show as `terminal` | Expected — non-agent titles collapse to one label (set `keep_command_name = true` to keep the command). |

## Development

```bash
make install       # editable install + dev deps into .venv
make test
```

## License

[MPL-2.0](LICENSE), matching the ActivityWatch ecosystem.
