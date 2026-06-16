"""Title normalization: turn a raw surface title into (label, is_agent).

Agent sessions set stable, meaningful titles (e.g. "✳ refine reports") worth
keeping verbatim. Plain shells set the live command line, which churns on every
keystroke and would fragment the timeline. We collapse non-agent titles to a
single generic label so plain-shell activity merges into long terminal blocks
and dwell time stays accurate. See spec §7.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Agent-session glyphs: the ✳-family stars some agents use, plus the braille
# range U+2800–U+28FF that Claude Code (and other CLIs) animate as a spinner.
# Verified against a live cmux: the focused Claude Code surface showed "⠐ ...".
_AGENT_GLYPHS = r"✳✶✻✽*⠀-⣿"

# Default regexes marking a surface as an agent session. Configurable (§9).
DEFAULT_AGENT_PATTERNS: list[str] = [
    rf"^\s*[{_AGENT_GLYPHS}]",
    r"^\s*(claude|codex|aider|gemini)\b",
]

# Leading agent glyph stripped from kept titles. Must cover every glyph in the
# detection set, or animating spinner frames would fragment the stored title.
STRIP_LEADING = rf"^\s*[{_AGENT_GLYPHS}]\s*"

DEFAULT_GENERIC_LABEL = "terminal"


@dataclass
class Normalizer:
    """Compiles the configured patterns once and applies them per title."""

    agent_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_AGENT_PATTERNS))
    generic_terminal_label: str = DEFAULT_GENERIC_LABEL
    keep_command_name: bool = False

    def __post_init__(self) -> None:
        self._compiled = [re.compile(p, re.IGNORECASE) for p in self.agent_patterns]
        self._strip = re.compile(STRIP_LEADING)

    def normalize(self, raw: str) -> tuple[str, bool]:
        """Return (title, is_agent) for a raw surface title."""
        raw = raw or ""
        if any(p.search(raw) for p in self._compiled):
            return (self._strip.sub("", raw).strip(), True)
        if self.keep_command_name:
            first = raw.strip().split()
            if first:
                return (first[0], False)
        return (self.generic_terminal_label, False)


def normalize(raw: str) -> tuple[str, bool]:
    """Convenience wrapper using default configuration."""
    return Normalizer().normalize(raw)
