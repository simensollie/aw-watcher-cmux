"""Tests for title normalization (spec §7)."""

import pytest

from aw_watcher_cmux.normalize import Normalizer, normalize


@pytest.mark.parametrize(
    "raw, expected_title",
    [
        ("✳ refine reports", "refine reports"),
        ("  ✶ build pipeline", "build pipeline"),
        ("* deploy", "deploy"),
        # Braille spinner frames (Claude Code et al.); verified against live cmux.
        ("⠐ Specify aw-watcher-cmux", "Specify aw-watcher-cmux"),
        ("⠋ refine reports", "refine reports"),
    ],
)
def test_agent_glyph_titles_kept_and_stripped(raw, expected_title):
    title, is_agent = normalize(raw)
    assert is_agent is True
    assert title == expected_title


def test_braille_spinner_frames_collapse_to_stable_title():
    # Different spinner frames must normalize to the same title, or the
    # timeline fragments (the churn §7 prevents).
    titles = {normalize(f"{frame} working")[0] for frame in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"}
    assert titles == {"working"}


@pytest.mark.parametrize("raw", ["claude refactor", "Codex review", "aider fix", "gemini chat"])
def test_agent_command_names_detected(raw):
    title, is_agent = normalize(raw)
    assert is_agent is True
    # No leading glyph to strip, so the title is kept verbatim.
    assert title == raw


@pytest.mark.parametrize("raw", ["git log --oneline", "vim main.py", "npm run build", ""])
def test_plain_shell_collapses_to_generic_label(raw):
    title, is_agent = normalize(raw)
    assert is_agent is False
    assert title == "terminal"


def test_keep_command_name_stores_first_token():
    n = Normalizer(keep_command_name=True)
    title, is_agent = n.normalize("git log --oneline")
    assert is_agent is False
    assert title == "git"


def test_keep_command_name_empty_falls_back_to_label():
    n = Normalizer(keep_command_name=True)
    title, is_agent = n.normalize("   ")
    assert title == "terminal"
    assert is_agent is False


def test_custom_generic_label():
    n = Normalizer(generic_terminal_label="shell")
    assert n.normalize("ls -la") == ("shell", False)


def test_custom_agent_pattern():
    n = Normalizer(agent_patterns=[r"^bot:"])
    assert n.normalize("bot: working") == ("bot: working", True)
    assert n.normalize("✳ refine") == ("terminal", False)
