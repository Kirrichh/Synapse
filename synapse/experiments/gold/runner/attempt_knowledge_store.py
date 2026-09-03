"""Canonical run-record key for one attempt's durable knowledge basis.

Basis bytes are published and read through the run-record owner. This module
owns only the key format shared by controller, recovery and state
reconstruction; it deliberately exposes no second storage adapter.
"""

from __future__ import annotations

from .vocabulary import GoldRunFailureCode, GoldRunViolation


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def basis_record_key(attempt_index: int) -> str:
    """One basis per attempt, named by the attempt it describes."""

    if type(attempt_index) is not int or attempt_index < 1:
        raise _fail(
            GoldRunFailureCode.MALFORMED_IDENTITY,
            "attempt index must be one-based",
        )
    return f"attempt-{attempt_index}"


__all__ = ["basis_record_key"]
