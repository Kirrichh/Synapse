"""Independence of witnesses in the declared provenance graph.

A witness is the source of a recorded successful action (service plus account
or key, fixed by the gateway when the result was written). Two witnesses of
the same supported claim are independent only when both are declared nodes,
every ancestor they name is itself declared, and their ancestor closures
(each including the node itself) do not intersect. The check returns one of
three results:

* ``independent`` — established relative to the completeness of the graph;
* ``dependent`` — the closures share an origin (copies, mirrors, one account);
* ``not_established`` — a node or an ancestor is unknown, or the graph has a
  cycle; strict birth treats it as no independence.

Different account names, mirrors, tools, runs or model votes are never
independent by themselves; only the declared graph decides.
"""
from __future__ import annotations

from itertools import combinations
from typing import Iterable, Mapping

INDEPENDENT = "independent"
DEPENDENT = "dependent"
NOT_ESTABLISHED = "not_established"


def closure(graph: Mapping[str, tuple[str, ...]], source: str) -> frozenset[str] | None:
    """The node with all its declared ancestors, or ``None`` when not fully declared."""
    seen: set[str] = set()
    on_path: set[str] = set()
    order: list[tuple[str, bool]] = [(source, False)]
    while order:
        node, leaving = order.pop()
        if leaving:
            on_path.discard(node)
            continue
        if node not in graph:
            return None
        if node in on_path:
            return None
        if node in seen:
            continue
        seen.add(node)
        on_path.add(node)
        order.append((node, True))
        for ancestor in graph[node]:
            if ancestor in on_path:
                return None
            order.append((ancestor, False))
    return frozenset(seen)


def relation(graph: Mapping[str, tuple[str, ...]], left: str, right: str) -> str:
    if left == right:
        return DEPENDENT
    a, b = closure(graph, left), closure(graph, right)
    if a is not None and b is not None:
        return DEPENDENT if a & b else INDEPENDENT
    if (a is not None and right in a) or (b is not None and left in b):
        return DEPENDENT
    return NOT_ESTABLISHED


def independent_witnesses(graph: Mapping[str, tuple[str, ...]], witnesses: Iterable[str], required: int) -> dict:
    """Whether ``required`` pairwise independent witnesses exist, with an explanation.

    The search is exhaustive over the (small) witness set and deterministic.
    """
    ordered = sorted(set(witnesses))
    pairs = {(a, b): relation(graph, a, b) for a, b in combinations(ordered, 2)}
    best: tuple[str, ...] = tuple(ordered[:1])
    for size in range(min(required, len(ordered)), 1, -1):
        for group in combinations(ordered, size):
            if all(pairs[(a, b)] == INDEPENDENT for a, b in combinations(group, 2)):
                best = group
                break
        if len(best) == size:
            break
    if len(best) >= required:
        verdict = INDEPENDENT
    elif pairs and all(value == DEPENDENT for value in pairs.values()):
        verdict = DEPENDENT
    else:
        # Too few witnesses, an unknown node or an unresolvable link: independence is not established.
        verdict = NOT_ESTABLISHED
    return {"verdict": verdict, "required": required, "witnesses": ordered, "independent_set": list(best),
            "pairs": [{"left": a, "right": b, "relation": value} for (a, b), value in sorted(pairs.items())]}
