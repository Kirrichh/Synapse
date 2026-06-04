"""Standalone StateOverlay for Alpha3g Integrate I1/I1.1.

This module implements isolated copy-on-write state tracking and draft
write-set generation. It is deliberately not wired into ``evaluate_integrate``
or the replay applier. Since P0.4.8 / SI4 it supports an explicit
profile selector while preserving the legacy Alpha3g local JSON profile as the
default.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import json
from typing import Any, Callable, Iterator, Mapping

from synapse.canonical_values import (
    CanonicalSerializationError as StableCanonicalSerializationError,
    canonicalize as stable_canonicalize,
)
from synapse.canonical_service import (
    ALPHA3G_LOCAL_JSON_PROFILE,
    STABLE_CANONICAL_PROFILE,
    stable_canonical_hash,
)
from synapse.canonical_path import CanonicalPath, CanonicalPathError, parse_canonical_path


class StateOverlayError(RuntimeError):
    """Base error for standalone StateOverlay failures."""


class OverlayPathError(StateOverlayError, CanonicalPathError):
    """Raised when a path cannot be used by StateOverlay."""


class CanonicalSerializationError(StateOverlayError, TypeError):
    """Raised when a changed value cannot enter an integrate write_set."""


_DELETED = object()
_MISSING = object()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def canonical_value_hash(value: Any, *, profile: str = ALPHA3G_LOCAL_JSON_PROFILE) -> str:
    """Return a canonical value hash for the selected StateOverlay profile.

    The default remains ``alpha3g.local-json.v1`` for backwards compatibility
    with existing Integrate Category B artifacts and legacy StateOverlay tests.
    ``stable-canonical.v1`` is opt-in and delegates to the approved Stable
    Canonical Identity value service. Unsupported values fail closed with this
    module's ``CanonicalSerializationError`` so callers do not need to know
    which profile produced the rejection.
    """

    if profile == ALPHA3G_LOCAL_JSON_PROFILE:
        payload = _canonical_json(_canonicalize_value(value))
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if profile == STABLE_CANONICAL_PROFILE:
        try:
            return stable_canonical_hash(value)
        except StableCanonicalSerializationError as exc:
            raise CanonicalSerializationError(str(exc)) from exc
    raise CanonicalSerializationError(f"unsupported StateOverlay canonical profile: {profile}")


def _canonicalize_value_for_profile(value: Any, *, profile: str) -> Any:
    if profile == ALPHA3G_LOCAL_JSON_PROFILE:
        return _canonicalize_value(value)
    if profile == STABLE_CANONICAL_PROFILE:
        try:
            return stable_canonicalize(value)
        except StableCanonicalSerializationError as exc:
            raise CanonicalSerializationError(str(exc)) from exc
    raise CanonicalSerializationError(f"unsupported StateOverlay canonical profile: {profile}")


def _is_unsupported_callable(value: Any) -> bool:
    return callable(value)


def _canonicalize_value(value: Any) -> Any:
    """Return a JSON-serializable canonical value or raise.

    Alpha3g Integrate v1 intentionally rejects executable values and arbitrary
    host objects in write sets. Agent snapshots, canonical time, set encoding,
    and function canonicalization remain future RFC/implementation work.
    """

    if _is_unsupported_callable(value):
        raise CanonicalSerializationError("functions/callables are forbidden in integrate write_set values")
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                raise CanonicalSerializationError("NaN and Infinity are not canonical JSON values")
        return value
    if isinstance(value, list):
        return [_canonicalize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize_value(item) for item in value]
    if isinstance(value, dict):
        canonical: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalSerializationError("dict keys in write_set values must be strings")
            canonical[key] = _canonicalize_value(item)
        return canonical
    raise CanonicalSerializationError(f"unsupported write_set value type: {type(value).__name__}")


@dataclass(frozen=True)
class WriteSetEntry:
    """Immutable top-level draft write-set entry produced by StateOverlay.

    ``value_profile`` is omitted for legacy Alpha3g local write-sets to preserve
    existing serialized event shapes. Opt-in ``stable-canonical.v1`` write-sets
    include it so the hash profile is explicit at the migration boundary.
    """

    path: str
    granularity: str
    op: str
    old_value_hash: str | None
    new_value: Any | None = None
    new_value_hash: str | None = None
    value_profile: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "path": self.path,
            "granularity": self.granularity,
            "op": self.op,
            "old_value_hash": self.old_value_hash,
        }
        if self.op != "delete":
            data["new_value"] = self.new_value
            data["new_value_hash"] = self.new_value_hash
        if self.value_profile is not None:
            data["value_profile"] = self.value_profile
        return data


@dataclass(frozen=True)
class WriteSet:
    """Immutable draft write-set returned by ``StateOverlay.commit()``.

    I1/I1.1 still treats this as a draft infrastructure object: it is not
    applied to base state and it is not emitted to execution history. The
    immutable wrapper exists so future I2/I3 code does not depend on a raw
    ``list[dict]`` shape. Use ``to_list()`` only at serialization/event
    boundaries. Entries are required to be sorted by canonical path so event
    hashing cannot depend on construction order.
    """

    entries: tuple[WriteSetEntry, ...]

    def __post_init__(self) -> None:
        paths = [entry.path for entry in self.entries]
        if paths != sorted(paths):
            raise ValueError("WriteSet entries must be sorted by canonical path")

    def __iter__(self) -> Iterator[WriteSetEntry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def __getitem__(self, index: int) -> WriteSetEntry:
        return self.entries[index]

    def to_list(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self.entries]


class StateOverlay:
    """Copy-on-write overlay for v1 Integrate namespaces.

    ``base_state`` is a mapping with optional ``env`` and ``memory`` namespace
    dictionaries. Reads of mutable values are materialized into the overlay so
    mutations cannot leak into the parent before ``commit()``.

    ``profile`` selects the value hashing / canonicalization profile used for
    dirty detection, write-set hashes, overlay summaries, and canonical state
    hashes. The default is the legacy ``alpha3g.local-json.v1`` profile. The
    approved ``stable-canonical.v1`` profile is opt-in in P0.4.8 / SI4; this is
    a compatibility boundary, not a hard switch.
    """

    def __init__(
        self,
        base_state: Mapping[str, Mapping[str, Any]] | None = None,
        *,
        clone: Callable[[Any], Any] = copy.deepcopy,
        profile: str = ALPHA3G_LOCAL_JSON_PROFILE,
    ) -> None:
        base_state = base_state or {}
        if profile not in (ALPHA3G_LOCAL_JSON_PROFILE, STABLE_CANONICAL_PROFILE):
            raise CanonicalSerializationError(f"unsupported StateOverlay canonical profile: {profile}")
        self.profile = profile
        self._base: dict[str, Mapping[str, Any]] = {
            "env": base_state.get("env", {}) or {},
            "memory": base_state.get("memory", {}) or {},
        }
        self._overlay: dict[str, Any] = {}
        self._parsed: dict[str, CanonicalPath] = {}
        self._clone = clone
        self._discarded = False

    @property
    def dirty_paths(self) -> tuple[str, ...]:
        return tuple(sorted(path for path in self._overlay if self._is_dirty(path)))

    def get(self, path: str) -> Any:
        self._ensure_active()
        canonical = self._parse(path)
        if canonical.path in self._overlay:
            value = self._overlay[canonical.path]
            if value is _DELETED:
                raise KeyError(path)
            return value

        value = self._base_value(canonical)
        if value is _MISSING:
            raise KeyError(path)
        materialized = self._clone(value)
        if isinstance(materialized, (dict, list, set, tuple)):
            self._overlay[canonical.path] = materialized
            return materialized
        return materialized

    def set(self, path: str, value: Any) -> None:
        """Set a top-level overlay value after eager canonical validation.

        Unsupported functions, callables, non-string dict keys, NaN/Inf,
        and arbitrary host objects fail at ``set()`` time. Under the legacy
        profile, sets and bytes also fail closed; under ``stable-canonical.v1``
        they are encoded through typed wrappers. No-op elision is
        evaluated later by canonical value hash equality, not by ``is`` identity
        or Python's raw ``==`` alone.
        """

        self._ensure_active()
        canonical = self._parse(path)
        # Validate early so unsupported values never sit in overlay state.
        _canonicalize_value_for_profile(value, profile=self.profile)
        self._overlay[canonical.path] = self._clone(value)

    def delete(self, path: str) -> None:
        """Mark a top-level path as deleted in the overlay.

        The tombstone is internal only. In the materialized state the key is
        absent. In the draft write-set, deletes are serialized as entries with
        ``op == "delete"`` and no ``new_value`` / ``new_value_hash`` fields.
        """

        self._ensure_active()
        canonical = self._parse(path)
        if self._base_value(canonical) is _MISSING and canonical.path not in self._overlay:
            raise KeyError(path)
        self._overlay[canonical.path] = _DELETED

    def discard(self) -> None:
        self._overlay.clear()
        self._parsed.clear()
        self._discarded = True

    def commit(self) -> WriteSet:
        """Return a sorted, deduplicated immutable draft ``WriteSet``.

        I1/I1.1 does not apply the write-set to base state and does not emit
        history events. Future I2/I3 patches own commit application and event
        emission. Calling ``commit()`` is terminal for this overlay; subsequent
        operations raise ``StateOverlayError``.
        """

        self._ensure_active()
        entries: list[WriteSetEntry] = []
        for path in sorted(self._overlay):
            if not self._is_dirty(path):
                continue
            parsed = self._parse(path)
            old_value = self._base_value(parsed)
            old_hash = None if old_value is _MISSING else self._value_hash(old_value)
            new_value = self._overlay[path]
            if new_value is _DELETED:
                entries.append(
                    WriteSetEntry(
                        path=path,
                        granularity="top_level",
                        op="delete",
                        old_value_hash=old_hash,
                        value_profile=self._write_set_value_profile(),
                    )
                )
                continue
            canonical_new = self._canonicalize(new_value)
            entries.append(
                WriteSetEntry(
                    path=path,
                    granularity="top_level",
                    op="replace",
                    old_value_hash=old_hash,
                    new_value=canonical_new,
                    new_value_hash=self._value_hash(canonical_new),
                    value_profile=self._write_set_value_profile(),
                )
            )
        self.discard()
        return WriteSet(tuple(entries))

    def canonical_hash(self) -> str:
        """Hash the materialized merged view using this overlay's profile.

        With the default ``alpha3g.local-json.v1`` profile, this preserves the
        historical I1 local subset. With ``stable-canonical.v1``, this delegates
        to the approved Stable Canonical Identity value core through the SI3
        service boundary.
        """

        state = {
            "env": dict(self._base.get("env", {})),
            "memory": dict(self._base.get("memory", {})),
        }
        for path, value in self._overlay.items():
            parsed = self._parse(path)
            namespace = state[parsed.namespace]
            if value is _DELETED:
                namespace.pop(parsed.key, None)
            else:
                namespace[parsed.key] = self._canonicalize(value)
        return self._value_hash(state)


    def overlay_summary(self) -> dict[str, Any]:
        """Return deterministic forensic metadata for dirty overlay paths.

        The summary intentionally excludes concrete new values. It is safe for
        ``integrate_aborted`` events and future audit tooling. Dirty paths are
        sorted by canonical path and include old / overlay value hashes only.
        """

        paths = self.dirty_paths
        items: list[dict[str, Any]] = []
        for path in paths:
            parsed = self._parse(path)
            old_value = self._base_value(parsed)
            overlay_value = self._overlay[path]
            item: dict[str, Any] = {
                "path": path,
                "granularity": "top_level",
                "old_value_hash": None if old_value is _MISSING else self._value_hash(old_value),
            }
            if overlay_value is _DELETED:
                item["overlay_value_hash"] = None
                item["op"] = "delete"
            else:
                item["overlay_value_hash"] = self._value_hash(overlay_value)
                item["op"] = "replace"
            items.append(item)
        return {
            "dirty_count": len(items),
            "dirty_paths": items,
            "orphaned_resources_count": 0,
        }

    def _write_set_value_profile(self) -> str | None:
        # Preserve legacy serialized write-set shape by omitting the profile for
        # the default Alpha3g local profile. Opt-in stable write-sets identify
        # their value hash profile explicitly for future migration / replay gates.
        return None if self.profile == ALPHA3G_LOCAL_JSON_PROFILE else self.profile

    def _canonicalize(self, value: Any) -> Any:
        return _canonicalize_value_for_profile(value, profile=self.profile)

    def _value_hash(self, value: Any) -> str:
        return canonical_value_hash(value, profile=self.profile)

    def _ensure_active(self) -> None:
        if self._discarded:
            raise StateOverlayError("StateOverlay has been discarded")

    def _parse(self, path: str) -> CanonicalPath:
        if path not in self._parsed:
            try:
                self._parsed[path] = parse_canonical_path(path)
            except CanonicalPathError as exc:
                raise OverlayPathError(str(exc)) from exc
        return self._parsed[path]

    def _base_value(self, parsed: CanonicalPath) -> Any:
        namespace = self._base.get(parsed.namespace, {})
        if parsed.key not in namespace:
            return _MISSING
        return namespace[parsed.key]

    def _is_dirty(self, path: str) -> bool:
        """Return True when overlay value differs by canonical hash equality.

        Dirty/no-op classification uses the overlay profile's value hash on the
        base and overlay values. It intentionally does not rely on object
        identity and is stricter than ad-hoc Python equality for unsupported
        values.
        """

        parsed = self._parse(path)
        old_value = self._base_value(parsed)
        new_value = self._overlay[path]
        if old_value is _MISSING:
            return new_value is not _DELETED
        if new_value is _DELETED:
            return True
        return self._value_hash(old_value) != self._value_hash(new_value)


__all__ = [
    "CanonicalSerializationError",
    "OverlayPathError",
    "StateOverlay",
    "StateOverlayError",
    "WriteSet",
    "WriteSetEntry",
    "ALPHA3G_LOCAL_JSON_PROFILE",
    "STABLE_CANONICAL_PROFILE",
    "canonical_value_hash",
]
