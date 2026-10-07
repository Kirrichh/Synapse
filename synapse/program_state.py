"""The program's data as one state: captured, compared and put back in place.

A transaction (``integrate``) and a policy guard must leave program data either
committed or exactly as it was. That data is everything reachable from the
scopes a body can write to: the enclosing scope chain, the scopes captured by
functions reachable from there (closures), and every list, dict and set nested
in their values. Each container is recorded by identity with a shallow copy of
its contents; restoring puts every container's contents and every scope's
bindings back in place, so object identity and shared references survive (two
names bound to one dict still share it afterwards), and anything created inside
is simply no longer referenced.

Scopes are recognised by shape (``variables`` and ``parent``) and functions by
their ``closure``; this module imports no runtime class.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

_SCALARS = (int, float, complex, str, bytes, bool, type(None))


def _is_scope(value: Any) -> bool:
    return hasattr(value, "variables") and hasattr(value, "parent")


def _same(a: Any, b: Any) -> bool:
    return a is b or (type(a) is type(b) and isinstance(a, _SCALARS) and a == b)


def _same_mapping(current: Dict[Any, Any], recorded: Dict[Any, Any]) -> bool:
    return current.keys() == recorded.keys() and all(_same(current[key], recorded[key]) for key in recorded)


def _differs(value: Any, contents: Any) -> bool:
    """Whether a captured container holds something else than its recorded contents."""
    if isinstance(value, dict):
        return not _same_mapping(value, contents)
    if isinstance(value, list):
        return len(value) != len(contents) or not all(_same(a, b) for a, b in zip(value, contents))
    return value != contents


class ProgramState:
    """The data reachable from ``scopes`` (and from ``values``) at the moment of capture."""

    def __init__(self, scopes: Iterable[Any], values: Iterable[Any] = ()):
        self._scopes: Dict[int, Tuple[Any, Dict[str, Any]]] = {}
        self._containers: Dict[int, Tuple[Any, Any]] = {}
        pending_scopes: List[Any] = list(scopes)
        pending_values: List[Any] = list(values)
        while pending_scopes or pending_values:
            while pending_scopes:
                scope = pending_scopes.pop()
                while scope is not None and id(scope) not in self._scopes:
                    self._scopes[id(scope)] = (scope, dict(scope.variables))
                    pending_values.extend(scope.variables.values())
                    pending_values.extend(getattr(scope, "functions", {}).values())
                    scope = scope.parent
            while pending_values:
                value = pending_values.pop()
                if isinstance(value, _SCALARS):
                    continue
                closure = getattr(value, "closure", None)
                if _is_scope(closure):
                    pending_scopes.append(closure)
                if id(value) in self._containers:
                    continue
                if isinstance(value, dict):
                    self._containers[id(value)] = (value, dict(value))
                    pending_values.extend(value.values())
                elif isinstance(value, list):
                    self._containers[id(value)] = (value, list(value))
                    pending_values.extend(value)
                elif isinstance(value, set):
                    self._containers[id(value)] = (value, set(value))
                elif isinstance(value, tuple):
                    pending_values.extend(value)

    def changed(self) -> bool:
        """Whether any captured binding or container holds something else now."""
        return (any(not _same_mapping(scope.variables, bindings) for scope, bindings in self._scopes.values())
                or any(_differs(value, contents) for value, contents in self._containers.values()))

    def restore(self) -> None:
        """Every captured scope and container holds again exactly what it held, in place."""
        for value, contents in self._containers.values():
            if _differs(value, contents):
                if isinstance(value, list):
                    value[:] = contents
                else:
                    value.clear()
                    value.update(contents)
        for scope, bindings in self._scopes.values():
            if not _same_mapping(scope.variables, bindings):
                scope.variables.clear()
                scope.variables.update(bindings)
