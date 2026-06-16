"""Entry point: parse args, load config, set up the bucket, run the loop."""

from __future__ import annotations

import argparse
import json
import logging
import socket as socketlib
from dataclasses import dataclass, field

from aw_client import ActivityWatchClient
from aw_core.config import load_config_toml
from aw_core.log import setup_logging

from . import __version__
from . import ax
from . import cmux
from . import main as loop
from .normalize import DEFAULT_AGENT_PATTERNS, DEFAULT_GENERIC_LABEL

logger = logging.getLogger(__name__)

CLIENT_NAME = "aw-watcher-cmux"

# Default config rendered into the user's toml on first run (aw-core convention).
DEFAULT_CONFIG = f"""
[aw-watcher-cmux]
poll_interval = 2.0
pulsetime = 5.0
generic_terminal_label = "{DEFAULT_GENERIC_LABEL}"
keep_command_name = false
cmux_bin = "cmux"
# socket_path = "/tmp/cmux.sock"   # defaults to $CMUX_SOCKET_PATH or /tmp/cmux.sock
agent_patterns = [{", ".join("'" + p + "'" for p in DEFAULT_AGENT_PATTERNS)}]
""".strip()


@dataclass
class Config:
    poll_interval: float = 2.0
    pulsetime: float = 5.0
    agent_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_AGENT_PATTERNS))
    generic_terminal_label: str = DEFAULT_GENERIC_LABEL
    keep_command_name: bool = False
    cmux_bin: str = "cmux"
    socket_path: str | None = None


def load_config(args: argparse.Namespace) -> Config:
    """Load config from aw-core toml, then apply CLI overrides (flags win, §9)."""
    cfg = Config()
    try:
        parsed = load_config_toml(CLIENT_NAME, DEFAULT_CONFIG)
        section = parsed.get(CLIENT_NAME, parsed)
        for key in (
            "poll_interval", "pulsetime", "agent_patterns",
            "generic_terminal_label", "keep_command_name", "cmux_bin", "socket_path",
        ):
            if key in section:
                setattr(cfg, key, section[key])
    except Exception as exc:  # noqa: BLE001 - config is best-effort, defaults are fine
        logger.warning("could not load config file, using defaults: %s", exc)

    # Flags override the file. Use `is not None` so an explicit 0 is honored and
    # not silently dropped by a truthiness check.
    if args.cmux_bin is not None:
        cfg.cmux_bin = args.cmux_bin
    if args.socket_path is not None:
        cfg.socket_path = args.socket_path
    if args.poll_interval is not None:
        cfg.poll_interval = args.poll_interval
    if args.pulsetime is not None:
        cfg.pulsetime = args.pulsetime
    if args.generic_terminal_label is not None:
        cfg.generic_terminal_label = args.generic_terminal_label
    if args.keep_command_name is not None:
        cfg.keep_command_name = args.keep_command_name
    return cfg


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog=CLIENT_NAME, description=__doc__)
    p.add_argument("--testing", action="store_true",
                   help="use the aw test server (port 5666) and a -testing bucket suffix")
    p.add_argument("--verbose", action="store_true", help="debug logging")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--cmux-bin", dest="cmux_bin", help="path to the cmux CLI")
    p.add_argument("--socket-path", dest="socket_path", help="override cmux socket path")
    p.add_argument("--poll-interval", dest="poll_interval", type=float,
                   help="seconds between polls")
    p.add_argument("--pulsetime", dest="pulsetime", type=float,
                   help="heartbeat merge window in seconds")
    p.add_argument("--generic-terminal-label", dest="generic_terminal_label",
                   help="label stored for non-agent surfaces")
    # store_const keeps the unset default at None so it doesn't override the file.
    p.add_argument("--keep-command-name", dest="keep_command_name",
                   action="store_const", const=True, default=None,
                   help="store the first command token instead of the generic label")
    # agent_patterns is a list of regexes; it stays file-only (impractical on the CLI).
    p.add_argument("--selfcheck", action="store_true",
                   help="compare AX reading against the cmux socket oracle and exit "
                        "(run inside a cmux surface)")
    p.add_argument("--snapshot", action="store_true",
                   help="print cmux's serialized AX tree as JSON and exit "
                        "(for capturing test fixtures)")
    return p.parse_args(argv)


def run_snapshot() -> int:
    pid = ax.cmux_pid()
    if pid is None:
        print("cmux is not running")
        return 1
    print(json.dumps(ax.snapshot_app(pid), ensure_ascii=False, indent=2))
    return 0


def run_selfcheck(config: Config) -> int:
    """Compare the AX reading against the cmux socket oracle and report
    MATCH/MISMATCH on workspace + surface (exit 0 match, 2 mismatch, 1 if the
    oracle is unavailable, e.g. run outside a cmux surface)."""
    ax_f = ax.get_focused()
    try:
        sock = cmux.get_focused(config.cmux_bin, config.socket_path)
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args)

    # One-shot diagnostic modes print to stdout and exit; handle them before
    # setup_logging so they don't spin up a rotating log file.
    if args.snapshot:
        return run_snapshot()
    if args.selfcheck:
        return run_selfcheck(config)

    setup_logging(
        CLIENT_NAME,
        testing=args.testing,
        verbose=args.verbose,
        log_stderr=True,
        log_file=True,
    )
    if not ax.is_trusted():
        logger.error(loop._HINTS[loop.NOT_TRUSTED])

    client = ActivityWatchClient(CLIENT_NAME, testing=args.testing)
    hostname = client.client_hostname or socketlib.gethostname()
    bucket_id = f"{CLIENT_NAME}_{hostname}"
    if args.testing:
        bucket_id += "-testing"

    # currentwindow reuses aw's window-activity views and categorization (§5).
    client.create_bucket(bucket_id, event_type="currentwindow", queued=True)

    with client:
        try:
            loop.run(client, bucket_id, config)
        except KeyboardInterrupt:
            logger.info("interrupted; shutting down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
