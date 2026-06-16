"""Tests for config loading and CLI-over-file override precedence (spec §9)."""

from aw_watcher_cmux import __main__ as cli


def test_defaults_when_no_flags(monkeypatch):
    # Isolate from any real user config file.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {})
    cfg = cli.load_config(cli.parse_args([]))
    assert cfg.poll_interval == 2.0
    assert cfg.pulsetime == 5.0
    assert cfg.generic_terminal_label == "terminal"
    assert cfg.keep_command_name is False


def test_file_values_applied(monkeypatch):
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-cmux": {"poll_interval": 4.0, "pulsetime": 9.0,
                            "generic_terminal_label": "shell", "keep_command_name": True}
    })
    cfg = cli.load_config(cli.parse_args([]))
    assert cfg.poll_interval == 4.0
    assert cfg.pulsetime == 9.0
    assert cfg.generic_terminal_label == "shell"
    assert cfg.keep_command_name is True


def test_cli_flags_override_file(monkeypatch):
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-cmux": {"poll_interval": 4.0, "pulsetime": 9.0,
                            "generic_terminal_label": "shell"}
    })
    cfg = cli.load_config(cli.parse_args(
        ["--poll-interval", "1.5", "--pulsetime", "6", "--generic-terminal-label", "tty",
         "--keep-command-name", "--cmux-bin", "/opt/cmux", "--socket-path", "/run/c.sock"]))
    assert cfg.poll_interval == 1.5
    assert cfg.pulsetime == 6.0
    assert cfg.generic_terminal_label == "tty"
    assert cfg.keep_command_name is True
    assert cfg.cmux_bin == "/opt/cmux"
    assert cfg.socket_path == "/run/c.sock"


def test_unset_flags_do_not_override_file(monkeypatch):
    # keep_command_name absent on CLI must leave the file's True intact.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-cmux": {"keep_command_name": True}
    })
    cfg = cli.load_config(cli.parse_args([]))
    assert cfg.keep_command_name is True


def test_poll_interval_zero_is_honored(monkeypatch):
    # `is not None` guard: an explicit 0 must not be dropped by truthiness.
    monkeypatch.setattr(cli, "load_config_toml", lambda *a, **k: {
        "aw-watcher-cmux": {"poll_interval": 2.0}
    })
    cfg = cli.load_config(cli.parse_args(["--poll-interval", "0"]))
    assert cfg.poll_interval == 0.0
