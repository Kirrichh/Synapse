"""Palace recall returns candidates; only admission makes a fact (refinement §11, §18 "Извлечение").

A labelled probe set runs through the canonical launch (``python -m synapse run
--record``): cards are imprinted into a palace room, each probe recalls
candidates and asks ``admit`` for one claim. The checker knows the hidden
answers; the palace's own scores are never its reference:

* similar words about another entity never answer for it;
* high confidence with a weak match does not pass the minimum conditions;
* a poisoned copy with the same keys, written later and with more confidence,
  does not win the answer;
* an unchanged statement does not grow stronger with repeated copies;
* two confirmed statements that disagree are a conflict, not a winner;
* a useful card the search cannot find (a paraphrase with no shared word) is
  counted as a loss of information even though abstaining was safe.

The sensitivity lattice over the admission threshold and the stored confidence
is published with the share of wrong admissions and of lost useful knowledge,
next to the same probes under the previous rule (a confident top candidate):
the delta says which admissions disappeared and why.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

REPOSITORY = Path(__file__).resolve().parents[2]
TRUE_RESTART = "pg_ctl restart -m fast"

CARDS = [
    {"entity": "postgresql", "attribute": "restart_command", "value": TRUE_RESTART, "source": "kb:wiki",
     "status": "confirmed", "content": "postgresql restart command pg_ctl restart fast"},
    {"entity": "mysql", "attribute": "restart_command", "value": "systemctl restart mysql", "source": "kb:wiki",
     "status": "confirmed", "content": "mysql restart command systemctl restart"},
    {"entity": "postgresql", "attribute": "vacuum_command", "value": "vacuumdb", "source": "kb:wiki",
     "status": "confirmed", "content": "vacuum"},
    {"entity": "postgresql", "attribute": "port", "value": "5432", "source": "kb:wiki", "status": "confirmed",
     "content": "default listening port 5432"},
    {"entity": "postgresql", "attribute": "max_connections", "value": "100", "source": "kb:wiki",
     "status": "confirmed", "content": "postgresql max connections limit 100"},
    {"entity": "postgresql", "attribute": "max_connections", "value": "200", "source": "runbook:ops",
     "status": "confirmed", "content": "postgresql max connections limit 200"},
    # Written last, most confident, unverified: the poisoned copy of the restart card.
    {"entity": "postgresql", "attribute": "restart_command", "value": "pg_ctl kill", "source": "mirror:kb",
     "status": "provisional", "content": "postgresql restart command pg_ctl kill", "poisoned": True},
]
# probe -> (query, claim, the hidden right answer or None when no answer is right)
PROBES = {
    "restart": ("postgresql restart command", {"entity": "postgresql", "attribute": "restart_command",
                                               "keys": ["postgresql", "restart", "command"]}, TRUE_RESTART),
    "partial_keys": ("postgresql restart procedure steps", {
        "entity": "postgresql", "attribute": "restart_command",
        "keys": ["postgresql", "restart", "procedure", "steps"]}, TRUE_RESTART),
    "other_entity": ("mariadb restart command", {"entity": "mariadb", "attribute": "restart_command",
                                                 "keys": ["mariadb", "restart", "command"]}, None),
    "weak_match": ("vacuum schedule window", {"entity": "postgresql", "attribute": "vacuum_command",
                                              "keys": ["vacuum", "schedule", "window"]}, "vacuumdb"),
    "paraphrase": ("which tcp socket number does postgres use", {
        "entity": "postgresql", "attribute": "port", "keys": ["tcp", "socket", "number"]}, "5432"),
    "conflict": ("postgresql max connections", {"entity": "postgresql", "attribute": "max_connections",
                                                "keys": ["postgresql", "max", "connections"]}, None),
}
THRESHOLDS = (0.34, 0.5, 0.67, 1.0)
CONFIDENCES = (0.1, 0.5, 0.99)


def _literal(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _program(confidence: float, copies: int) -> str:
    lines = ['memory palace "kb" { rooms { semantic } backend sqlite bind palace }']
    cards = CARDS[:1] * copies + CARDS[1:]
    for card in cards:
        fields = " ".join(f"{key} {_literal(value)}" for key, value in card.items() if key != "poisoned")
        lines.append(f"imprint into palace.semantic {{ {fields} confidence {confidence} }}")
    for name, (query, claim, _) in sorted(PROBES.items()):
        for threshold in THRESHOLDS:
            lines.append(f'recall from palace.semantic {{ query {_literal(query)} limit 20 bind found }}')
            lines.append(f"let decision = admit(found, {_literal({**claim, 'threshold': threshold})})")
    lines.append('print("probed")')
    return "\n".join(lines) + "\n"


def _run(tmp_path, confidence, copies=1) -> list[dict]:
    """The recall and admission events of one canonical run, in order."""
    name = f"probe-{confidence}-{copies}"
    source = tmp_path / f"{name}.syn"
    source.write_text(_program(confidence, copies))
    output = tmp_path / name
    completed = subprocess.run([sys.executable, "-B", "-m", "synapse", "run", str(source), "--record", "--output",
                                str(output)], cwd=REPOSITORY, env={**os.environ, "PYTHONPATH": str(REPOSITORY)},
                               capture_output=True, text=True, timeout=600)
    assert completed.returncode == 0, completed.stderr
    return [event for event in json.loads((output / "history.json").read_text())
            if event.get("type") in {"memory_imprinted", "memory_admission"}]


def _decisions(events) -> dict[tuple[str, float], dict]:
    values = {event["imprint_id"]: event["fields"] for event in events if event["type"] == "memory_imprinted"}
    admissions = iter(event for event in events if event["type"] == "memory_admission")
    decided = {}
    for name in sorted(PROBES):
        for threshold in THRESHOLDS:
            event = next(admissions)
            fact = values.get(event["fact"]) if event["fact"] else None
            decided[(name, threshold)] = {"decision": event["decision"], "value": None if fact is None else
                                          fact["value"], "checked": event["checked"], "conflict": event["conflict"]}
    return decided


def _previous_rule(confidence) -> dict[str, str | None]:
    """The previous palace rule on the same probes: the top candidate by (substring overlap + confidence) / 2,
    later insertion first on ties — what a program took as the answer before admission existed."""
    answers = {}
    for name, (query, _, _) in PROBES.items():
        words = query.lower().split()
        scored = []
        for order, card in enumerate(CARDS):
            text = json.dumps({**card, "confidence": confidence}, ensure_ascii=False).lower()
            overlap = sum(1 for word in words if word in text) / len(words)
            confidence_of = 0.99 if card.get("poisoned") else confidence
            scored.append(((overlap + confidence_of) / 2.0, order, card["value"]))
        answers[name] = max(scored)[2] if max(scored)[0] >= 0.5 else None
    return answers


def _rates(decided, threshold) -> dict[str, float]:
    wrong = lost = 0
    for name, (_, _, truth) in PROBES.items():
        item = decided[(name, threshold)]
        admitted = item["decision"] == "admitted"
        wrong += admitted and item["value"] != truth
        lost += truth is not None and not (admitted and item["value"] == truth)
    return {"wrong_admission_share": wrong / len(PROBES), "lost_useful_share": lost / len(PROBES)}


def test_admission_separates_candidates_from_facts_and_publishes_its_sensitivity(tmp_path, record_property):
    lattice = {}
    runs = {confidence: _decisions(_run(tmp_path, confidence)) for confidence in CONFIDENCES}
    for confidence, decided in runs.items():
        for threshold in THRESHOLDS:
            lattice[f"confidence={confidence},threshold={threshold}"] = _rates(decided, threshold)
    decided = runs[0.99]

    # The true card is admitted; the poisoned later, more confident copy never is.
    restart = decided[("restart", 0.5)]
    assert restart["decision"] == "admitted" and restart["value"] == TRUE_RESTART
    poisoned = [item for item in restart["checked"] if "not_confirmed:provisional" in item["reasons"]]
    assert len(poisoned) == 1
    # Similar words about other entities do not answer for this one.
    assert all(decided[("other_entity", t)]["decision"] == "abstained" for t in THRESHOLDS)
    assert all("another_entity" in item["reasons"] for item in decided[("other_entity", 0.5)]["checked"])
    # A confident weak match does not pass the minimum conditions.
    weak = decided[("weak_match", 0.34)]
    assert weak["decision"] == "abstained" and "too_few_key_tokens" in weak["checked"][0]["reasons"]
    # Disagreeing confirmed statements are a conflict: nothing is admitted, neither one wins.
    conflict = decided[("conflict", 0.5)]
    assert conflict["decision"] == "conflict" and conflict["value"] is None and len(conflict["conflict"]) == 2
    # A paraphrase with no shared word finds nothing: a safe abstention, counted as lost useful knowledge.
    assert decided[("paraphrase", 0.34)]["decision"] == "abstained" and decided[("paraphrase", 0.34)]["checked"] == []

    # Copies of an unchanged statement do not strengthen it.
    copied = _decisions(_run(tmp_path, 0.99, copies=4))
    for threshold in THRESHOLDS:
        once, many = decided[("restart", threshold)], copied[("restart", threshold)]
        assert (once["decision"], once["value"], once["conflict"]) == (many["decision"], many["value"], many["conflict"])
        assert {item["score"] for item in many["checked"] if not item["reasons"]} <= \
            {item["score"] for item in once["checked"]}

    # The lattice: no wrong admission anywhere, and the stored confidence changes nothing.
    assert all(rates["wrong_admission_share"] == 0.0 for rates in lattice.values())
    for threshold in THRESHOLDS:
        rows = {json.dumps(lattice[f"confidence={c},threshold={threshold}"]) for c in CONFIDENCES}
        assert len(rows) == 1
    losses = [lattice[f"confidence=0.99,threshold={t}"]["lost_useful_share"] for t in THRESHOLDS]
    assert losses == sorted(losses) and losses[0] < losses[-1]  # A stricter threshold only loses knowledge.
    assert decided[("partial_keys", 0.5)]["value"] == TRUE_RESTART
    assert decided[("partial_keys", 0.67)]["decision"] == "abstained"

    # The delta against the previous rule on the same probes.
    previous = _previous_rule(0.99)
    delta = {name: {"previous": previous[name], "now": decided[(name, 0.5)]["value"],
                    "truth": PROBES[name][2]} for name in sorted(PROBES)}
    assert delta["restart"]["previous"] == "pg_ctl kill"  # The poisoned copy used to win on confidence.
    assert delta["other_entity"]["previous"] is not None  # Another entity used to answer.
    record_property("admission_lattice", json.dumps(lattice, sort_keys=True))
    record_property("admission_delta", json.dumps(delta, sort_keys=True))
    print(json.dumps({"lattice": lattice, "delta": delta}, sort_keys=True, indent=1))
