"""Bounded advice and similarity for the court, recorded by the gateway.

The advisor and the similarity scorer are ``reason`` tools of the memory
configuration. Every question goes through the gateway under the
consolidation's own run identity with a deterministic ordinal, so evaluating
the same inputs again consumes the recorded answers instead of asking again.
A question is asked twice, with different phrasing and input order; an answer
outside the bounded vocabulary counts as disagreement, and disagreement is
uncertainty (И7). The free-text reason never influences a decision.
"""
from __future__ import annotations

from typing import Any

from ..configuration import MemoryConfiguration
from ..tools.gateway import Gateway


class Counsel:
    """Questions of one consolidation to its configured advisor and scorer."""

    def __init__(self, gateway: Gateway, configuration: MemoryConfiguration, consolidation_id: str) -> None:
        self.gateway = gateway
        self.configuration = configuration
        self.run_id = f"court:{consolidation_id}"
        self.ordinal = 0
        self.questions = 0
        self.agreed = 0

    def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.ordinal += 1
        return self.gateway.invoke({
            "run_id": self.run_id, "ordinal": f"reason:{self.ordinal}", "episode": "court",
            "op_scope": f"court:{self.ordinal}", "path": "court", "tool": tool, "args": arguments,
            "retry_of": None, "task_id": None, "segment_marker_id": None, "habit_id": None})

    def similarity(self, left: str, right: str) -> float | None:
        """Configured similarity in [0, 1], or ``None`` without a scorer or a valid answer."""
        scorer = self.configuration.scorer
        if scorer is None:
            return None
        view = self._call(scorer.tool, {"a": left, "b": right})["view"]
        value = view["payload"].get("similarity") if view["ok"] and isinstance(view["payload"], dict) else None
        if type(value) not in {int, float} or not 0.0 <= value <= 1.0:
            return None
        return float(value)

    def ask(self, question: str, answers: tuple[str, ...], variants: tuple[dict, dict]) -> dict[str, Any]:
        """Two differently phrased and ordered calls; agreement or explicit disagreement."""
        advisor = self.configuration.advisor
        if advisor is None:
            return {"asked": False, "calls": [], "answer": None, "agreed": False, "reasons": [], "refs": []}
        calls, reasons, refs = [], [], []
        for index, variant in enumerate(variants):
            outcome = self._call(advisor.tool, {"question": question, "variant": index,
                                                "answers": list(answers), "input": variant})
            view = outcome["view"]
            payload = view["payload"] if view["ok"] and isinstance(view["payload"], dict) else {}
            answer = payload.get("answer")
            calls.append(answer if answer in answers else None)
            reason = payload.get("reason")
            reasons.append(reason[:512] if isinstance(reason, str) else None)
            refs.append(outcome["ref"])
        self.questions += 1
        agreed = calls[0] is not None and calls[0] == calls[1]
        self.agreed += int(agreed)
        return {"asked": True, "calls": calls, "answer": calls[0] if agreed else None, "agreed": agreed,
                "reasons": reasons, "refs": refs, "component": advisor.to_dict()}
