"""Palace recall returns candidates; nothing a card says about itself makes a fact (§11, review R3).

A labelled probe set runs through the canonical launch (``python -m synapse run
--record``): cards are imprinted into a palace room — each stating
``status: confirmed`` about itself, the poisoned one ``provisional`` — each
probe recalls candidates and asks ``admit`` for one claim. The checker knows
the hidden answers; the palace's own scores are never its reference.

A plain run has no memory session and so no verification journal: a status
a card states describes its content and grants nothing, so every probe
abstains, and every candidate says why (``no_verified_basis``). The candidate
filter still reports every other check, and it is what this file measures:

* similar words about another entity never answer for it;
* high confidence with a weak match does not pass the minimum conditions;
* a poisoned copy with the same keys, written later and with more confidence,
  does not rank above the true card, and copies do not strengthen a card;
* a useful card the search cannot find (a paraphrase with no shared word) is
  counted as a loss of information.

The sensitivity lattice over the admission threshold and the stored confidence
is published for the filter — the share of probes where a wrong card would
pass every check but the basis, and of useful cards the filter loses — next to
the previous rule (a confident top candidate): the delta says which answers
disappeared and why. Facts are established by the knowledge path of a memory
session, where hypotheses are the basis (``test_knowledge_*``,
``test_admission_identity_acceptance.py``).
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


def _filtered(item) -> list:
    """Candidates that pass every check but the basis: what a basis would still have to establish."""
    return [check for check in item["checked"] if check["reasons"] == ["no_verified_basis"]]


def _rates(decided, threshold, values) -> dict[str, float]:
    wrong = lost = 0
    for name, (_, _, truth) in PROBES.items():
        passing = {values[check["id"]] for check in _filtered(decided[(name, threshold)])}
        wrong += any(value != truth for value in passing)
        lost += truth is not None and truth not in passing
    return {"wrong_candidate_share": wrong / len(PROBES), "lost_useful_share": lost / len(PROBES)}


def _values(events) -> dict:
    return {event["imprint_id"]: event["fields"]["value"] for event in events if event["type"] == "memory_imprinted"}


def test_a_card_never_grants_itself_admission_and_the_filter_publishes_its_sensitivity(tmp_path, record_property):
    lattice = {}
    events = {confidence: _run(tmp_path, confidence) for confidence in CONFIDENCES}
    runs = {confidence: _decisions(found) for confidence, found in events.items()}
    for confidence, decided in runs.items():
        for threshold in THRESHOLDS:
            lattice[f"confidence={confidence},threshold={threshold}"] = _rates(decided, threshold,
                                                                             _values(events[confidence]))
    decided, values = runs[0.99], _values(events[0.99])

    # Nothing is admitted: every card, the true one stating "confirmed" included, has no basis.
    assert {item["decision"] for item in decided.values()} == {"abstained"}
    restart = decided[("restart", 0.5)]
    assert all("no_verified_basis" in check["reasons"] and check["status"] is None for check in restart["checked"])
    assert {check["stated_status"] for check in restart["checked"]} == {"confirmed", "provisional"}
    # The true card passes every other check; the poisoned copy passes them too and is no answer either.
    assert {values[check["id"]] for check in _filtered(restart)} == {TRUE_RESTART, "pg_ctl kill"}
    # Similar words about other entities do not answer for this one.
    assert all("another_entity" in item["reasons"] for item in decided[("other_entity", 0.5)]["checked"])
    # A confident weak match does not pass the minimum conditions.
    weak = decided[("weak_match", 0.34)]
    assert "too_few_key_tokens" in weak["checked"][0]["reasons"]
    # A paraphrase with no shared word finds nothing: counted as lost useful knowledge.
    assert decided[("paraphrase", 0.34)]["checked"] == []

    # Copies of an unchanged statement do not strengthen it.
    copied = _decisions(_run(tmp_path, 0.99, copies=4))
    for threshold in THRESHOLDS:
        once, many = decided[("restart", threshold)], copied[("restart", threshold)]
        assert once["decision"] == many["decision"] == "abstained"
        assert {item["score"] for item in many["checked"]} <= {item["score"] for item in once["checked"]}

    # The lattice: the stored confidence changes nothing, and a stricter threshold only loses knowledge.
    for threshold in THRESHOLDS:
        rows = {json.dumps(lattice[f"confidence={c},threshold={threshold}"]) for c in CONFIDENCES}
        assert len(rows) == 1
    losses = [lattice[f"confidence=0.99,threshold={t}"]["lost_useful_share"] for t in THRESHOLDS]
    assert losses == sorted(losses) and losses[0] < losses[-1]
    assert TRUE_RESTART in {values[check["id"]] for check in _filtered(decided[("partial_keys", 0.5)])}
    assert _filtered(decided[("partial_keys", 0.67)]) == []

    # The delta against the previous rule on the same probes: nothing is answered without a basis now.
    previous = _previous_rule(0.99)
    delta = {name: {"previous": previous[name], "now": decided[(name, 0.5)]["value"],
                    "truth": PROBES[name][2]} for name in sorted(PROBES)}
    assert delta["restart"]["previous"] == "pg_ctl kill"  # The poisoned copy used to win on confidence.
    assert delta["other_entity"]["previous"] is not None  # Another entity used to answer.
    assert all(row["now"] is None for row in delta.values())
    record_property("admission_lattice", json.dumps(lattice, sort_keys=True))
    record_property("admission_delta", json.dumps(delta, sort_keys=True))
    print(json.dumps({"lattice": lattice, "delta": delta}, sort_keys=True, indent=1))
