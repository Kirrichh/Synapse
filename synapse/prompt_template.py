"""The prompt template language, one for the interpreter and the compiled VM.

A template interpolates ``{identifier}``; ``{{`` and ``}}`` stand for literal
braces. A placeholder whose name has no value stays as written, so a template
meant for later rendering survives an earlier one.
"""
from __future__ import annotations

import re
from typing import Any, Callable, List

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_OPEN, _CLOSE = "\x00OB\x00", "\x00CB\x00"


def _escaped(template: str) -> str:
    return template.replace("{{", _OPEN).replace("}}", _CLOSE)


def placeholders(template: str) -> List[str]:
    """The names the template interpolates, in order of first use."""
    names: List[str] = []
    for name in _PLACEHOLDER.findall(_escaped(template)):
        if name not in names:
            names.append(name)
    return names


def render(template: str, value_of: Callable[[str], Any]) -> str:
    """The template with each placeholder replaced; ``value_of`` raises ``KeyError`` for a name without value."""
    if "{" not in template:
        return template

    def substitute(match: "re.Match[str]") -> str:
        try:
            return str(value_of(match.group(1)))
        except KeyError:
            return match.group(0)

    return _PLACEHOLDER.sub(substitute, _escaped(template)).replace(_OPEN, "{").replace(_CLOSE, "}")
