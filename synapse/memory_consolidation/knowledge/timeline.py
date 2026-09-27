"""The timeline of statements: what was true then, and what is known now (refinement §15).

Every version the court folded carries two times (Snodgrass's bitemporal
model, SQL:2011 system-versioned application-time tables): the interval of
the world it is about (valid time, from the statement) and the interval of
windows in which memory held it (transaction time, from the court:
``known_from`` and, once a correction replaced it, ``known_until``). A
contradiction never deletes a version; a correction closes its transaction
time, so both questions stay answerable: *what was true at T* reads the valid
time, *as memory knew it at window K* reads the transaction time.

Resolution at a valid time ``T`` as known at window ``K`` (pure):

* only versions memory held at ``K`` count;
* a ``state`` holds from its start until the next start of the same slot or
  its own declared end (inertia): the versions with the latest start at or
  before ``T`` hold, all of them — disagreeing ones are a conflict for
  admission, never a winner by insertion time or confidence; a report of the
  past received late takes its place by its start, never over a later one;
* a ``bounded`` state is resolved the same way and is of unknown currency past
  its declared days — unknown, not false;
* an ``event`` holds exactly at its moment;
* without ``T`` a state resolves to its latest start and a bounded state is of
  unknown currency (no clock is assumed); events are listed as history;
* an undeclared property is of unknown currency.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

_EARLIEST = ""  # Sorts before every canonical instant: a statement without a start holds from the beginning.


def known_at(entry: Mapping[str, Any], window: int | None) -> bool:
    """Whether memory held this version at ``window`` (``None``: now)."""
    if window is None:
        return entry["known_until"] is None
    return entry["known_from"] <= window and (entry["known_until"] is None or entry["known_until"] > window)


def _start(entry) -> str:
    return entry["record"]["valid"]["from"] or _EARLIEST


def _plus_days(moment: str, days: int) -> str:
    value = datetime.strptime(moment, "%Y-%m-%dT%H:%M:%SZ") + timedelta(days=days)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolved(entry, valid_until, freshness) -> dict[str, Any]:
    return {"entry": entry, "valid_from": entry["record"]["valid"]["from"], "valid_until": valid_until,
            "freshness": freshness}


def _event(entries, valid_at) -> list[dict[str, Any]]:
    return [_resolved(entry, entry["record"]["valid"]["until"], "event") for entry in entries
            if valid_at is None or entry["record"]["valid"]["from"] == valid_at]


def _state(entries, valid_at, rule) -> list[dict[str, Any]]:
    starts = sorted({_start(entry) for entry in entries})
    eligible = [start for start in starts if valid_at is None or start <= valid_at]
    if not eligible:
        return []
    latest = eligible[-1]
    later = [start for start in starts if start > latest]
    found = []
    for entry in sorted((item for item in entries if _start(item) == latest), key=lambda item: item["record"]["id"]):
        own_end = entry["record"]["valid"]["until"]
        next_start = later[0] if later else None
        valid_until = min(end for end in (own_end, next_start) if end is not None) \
            if own_end is not None or next_start is not None else None
        if valid_at is not None and valid_until is not None and valid_at >= valid_until:
            continue  # The fluent was terminated before T.
        if rule["freshness"] == "bounded":
            if valid_at is None or entry["record"]["valid"]["from"] is None:
                freshness = "unknown"
            else:
                expiry = _plus_days(entry["record"]["valid"]["from"], rule["ttl_days"])
                freshness = "unknown" if valid_at >= expiry else "current"
                valid_until = expiry if valid_until is None else min(valid_until, expiry)
        else:
            freshness = "current"
        found.append(_resolved(entry, valid_until, freshness))
    return found


def resolve(entries: Iterable[Mapping[str, Any]], rule: Mapping[str, Any] | None, *, valid_at: str | None,
            known_as_of: int | None) -> list[dict[str, Any]]:
    """The versions of one slot that hold at ``valid_at`` as memory knew it at ``known_as_of``."""
    held = [entry for entry in entries if known_at(entry, known_as_of)]
    if not held:
        return []
    if rule is None:
        return [_resolved(entry, entry["record"]["valid"]["until"], "undeclared")
                for entry in sorted(held, key=lambda item: item["record"]["id"])]
    if rule["freshness"] == "event":
        return _event(sorted(held, key=lambda item: item["record"]["id"]), valid_at)
    return _state(held, valid_at, rule)
