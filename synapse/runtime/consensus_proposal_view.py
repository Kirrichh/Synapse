"""Immutable JSON-compatible proposal views for local consensus vote methods."""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any


class ProposalViewMutationError(RuntimeError):
    """A vote method attempted to change the read-only proposal view."""


class ProposalViewValueError(TypeError):
    """A proposal value is not safe to expose to an actor vote method."""


class FrozenDict(dict):
    """A recursively immutable dict that preserves Synapse member access."""

    def __init__(self, values: dict[str, Any]):
        dict.__init__(self)
        for key, value in values.items():
            if type(key) is not str:
                raise ProposalViewValueError("proposal keys must be strings")
            dict.__setitem__(self, key, freeze_json_value(value))

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    @staticmethod
    def _readonly(*_args: Any, **_kwargs: Any) -> None:
        raise ProposalViewMutationError("proposal view is read-only during consensus vote query")

    __setitem__ = _readonly
    __delitem__ = _readonly
    update = _readonly
    clear = _readonly
    pop = _readonly
    popitem = _readonly
    setdefault = _readonly
    __ior__ = _readonly


class FrozenList(list):
    """A recursively immutable list that surfaces mutation as a contract violation."""

    def __init__(self, values: Iterable[Any]):
        list.__init__(self)
        for value in values:
            list.append(self, freeze_json_value(value))

    @staticmethod
    def _readonly(*_args: Any, **_kwargs: Any) -> None:
        raise ProposalViewMutationError("proposal view is read-only during consensus vote query")

    __setitem__ = _readonly
    __delitem__ = _readonly
    append = _readonly
    clear = _readonly
    extend = _readonly
    insert = _readonly
    pop = _readonly
    remove = _readonly
    reverse = _readonly
    sort = _readonly
    __iadd__ = _readonly
    __imul__ = _readonly


def freeze_json_value(value: Any) -> Any:
    """Recursively freeze exact JSON values without invoking host-object hooks."""
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ProposalViewValueError("proposal floats must be finite")
        return value
    if type(value) is dict:
        return FrozenDict(value)
    if type(value) is list:
        return FrozenList(value)
    raise ProposalViewValueError("proposal view requires JSON-compatible values")


__all__ = [
    "FrozenDict",
    "FrozenList",
    "ProposalViewMutationError",
    "ProposalViewValueError",
    "freeze_json_value",
]
