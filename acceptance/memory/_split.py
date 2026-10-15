"""Training and exam data split by group (plan §17).

A card, its copies and its paraphrases are one group: they go to the same side
of the split, so an exam never asks about something whose copy or paraphrase
was learned. The side of a group is decided by the digest of its name and a
declared salt, never by the order of the items or by their content.
"""
from __future__ import annotations

import hashlib
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")


def side(group: str, salt: str, exam_share: float) -> str:
    value = int.from_bytes(hashlib.sha256(f"{salt}:{group}".encode()).digest()[:8], "big") / 2 ** 64
    return "exam" if value < exam_share else "train"


def split(items: Iterable[T], group: Callable[[T], str], *, salt: str, exam_share: float) -> dict[str, list[T]]:
    found: dict[str, list[T]] = {"train": [], "exam": []}
    for item in items:
        found[side(group(item), salt, exam_share)].append(item)
    return found
