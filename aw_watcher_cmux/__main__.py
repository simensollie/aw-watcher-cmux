"""Entry point: parse args, load config, set up the bucket, run the loop."""

from __future__ import annotations

import argparse
import logging
import socket as socketlib
from dataclasses import dataclass, field

from aw_client import ActivityWatchClient
from aw_core.config import load_config_toml
from aw_core.log import setup_logging

from . import __version__
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

    if args.cmux_bin:
        cfg.cmux_bin = args.cmux_bin
    if args.socket_path:
        cfg.socket_path = args.socket_path
    if args.poll_interval:
        cfg.poll_interval = args.poll_interval
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
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(
        CLIENT_NAME,
        testing=args.testing,
        verbose=args.verbose,
        log_stderr=True,
        log_file=True,
    )
    config = load_config(args)

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
