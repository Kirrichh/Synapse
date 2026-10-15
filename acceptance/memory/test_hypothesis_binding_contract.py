"""A check decides only the claim it answered about, over plain data (review F1).

An independent source is not yet a source that answered about this claim. The
operator's contract of the checking tool (``verifies``) binds the claim to the
check — which request argument and which answer field name its subject, its
scope (or the one scope the service answers for) and each condition:

* a claim about account A checked by a service asked about, and answering
  about, account B is neither confirmed nor refuted — whatever the value says;
  asked and answering about A, the same check confirms or refutes it;
* a check whose contract binds nothing, whose request lacks the bound argument
  or whose answer lacks the bound field decides nothing;
* the subject is compared by the exact identity rules: another namespace or
  another incarnation is another object;
* the scope is the service's (a declared value) or read from the request or
  answer; another scope decides nothing;
* every condition of the claim must be bound and equal; a condition the claim
  does not state but the check answered under is a narrower context;
* a status the court recorded is reused only under the rule and the check
  basis it was decided on — the checking tool's contract, the provenance
  relation of checker and source, the identity rules: under another rule or
  basis the claim is checked again;
* admission takes a status only from such a check: the foreign check admits
  nothing.
"""
from __future__ import annotations

import copy
import dataclasses

import pytest

from synapse.memory_consolidation import hypotheses
from synapse.memory_consolidation.configuration import parse_memory_configuration
from synapse.palace_admission import admit

LEDGER = {"subject": {"request": "account", "answer": "account"}, "scope": {"value": "bank"},
          "conditions": {"currency": {"request": "currency"}}}


def _configuration(verifies=LEDGER):
    tools = []
    for name, source, contract in (("source", "core:bank", {}),
                                   ("check", "ledger:bank", {} if verifies is None else {"verifies": verifies})):
        tools.append({"name": name, "server": "bank", "descriptor_sha256": "0" * 64, "input_schema": {"type": "object"},
                      "output_schema": {"type": "object"}, "source": source, "contract": contract})
    return parse_memory_configuration({
        "schema_version": "synapse.memory.configuration/v1", "advisor": None, "scorer": None, "element": "bank",
        "court": {"decision_rule": "threshold", "parameters": {}},
        "tools": {"schema_version": "synapse.memory.tool-configuration/v2", "servers": [{"id": "bank", "argv": ["x"]}],
                  "tools": tools, "provenance": {"core:bank": {"ancestors": []}, "ledger:bank": {"ancestors": []}}}})


def _claim(configuration, *, subject="A", check_args=None, conditions=None, scope="bank"):
    claim = {"aspect": "content", "subject": subject, "statement": {"balance": 20}, "scope": scope,
             "source": {"tool": "source", "args": {"account": "A"}},
             "check": {"tool": "check", "args": {"account": "A"} if check_args is None else check_args}}
    if conditions is not None:
        claim["conditions"] = conditions
    return hypotheses.declare(claim, configuration, "ev-source")


def _view(**payload):
    return {"ok": True, "source": "ledger:bank", "payload": {"ok": True, **payload}}


def _status(record, view, configuration):
    decided = hypotheses.resolve(record, view, configuration)
    assert decided["rule"] == hypotheses.CHECK_RULE
    return decided["status"], decided["reason"]


@pytest.mark.parametrize("balance", [20, 99])
def test_a_check_about_another_object_decides_nothing(balance):
    configuration = _configuration()
    record = _claim(configuration, check_args={"account": "B"})
    assert _status(record, _view(account="B", balance=balance), configuration) == (
        "provisional", "check_about_another:subject")
    # Asked about A, the service answering about B is no answer about A either.
    asked_a = _claim(configuration)
    assert _status(asked_a, _view(account="B", balance=balance), configuration) == (
        "provisional", "check_about_another:subject")


@pytest.mark.parametrize("balance, decided", [(20, ("confirmed", "check_agrees")),
                                              (99, ("refuted", "contradicted:balance"))])
def test_a_check_about_this_object_decides(balance, decided):
    configuration = _configuration()
    assert _status(_claim(configuration), _view(account="A", balance=balance), configuration) == decided


@pytest.mark.parametrize("verifies, check_args, payload, reason", [
    (None, None, {"account": "A", "balance": 20}, "check_unbound"),
    (LEDGER, {"id": "A"}, {"account": "A", "balance": 20}, "check_unbound:subject"),
    (LEDGER, None, {"balance": 20}, "check_silent_on:subject"),
    ({**LEDGER, "scope": {"value": "cards"}}, None, {"account": "A", "balance": 20}, "check_about_another:scope"),
    ({**LEDGER, "scope": {"answer": "bank"}}, None, {"account": "A", "balance": 20}, "check_silent_on:scope"),
    ({**LEDGER, "scope": {"answer": "bank"}}, None, {"account": "A", "bank": "other", "balance": 20},
     "check_about_another:scope"),
])
def test_a_check_without_its_binding_decides_nothing(verifies, check_args, payload, reason):
    configuration = _configuration(verifies)
    assert _status(_claim(configuration, check_args=check_args), _view(**payload), configuration) == (
        "provisional", reason)


@pytest.mark.parametrize("subject, answered", [
    ({"namespace": "core", "id": "A"}, "A"),
    ({"namespace": "core", "id": "A", "uid": "u-1"}, {"namespace": "core", "id": "A", "uid": "u-2"}),
    ({"namespace": "core", "id": "A", "uid": "u-1"}, {"namespace": "core", "id": "A"}),
])
def test_the_subject_is_compared_by_exact_identity(subject, answered):
    configuration = _configuration({"subject": {"answer": "account"}, "scope": {"value": "bank"}})
    record = _claim(configuration, subject=subject)
    assert _status(record, _view(account=answered, balance=20), configuration) == (
        "provisional", "check_about_another:subject")
    assert _status(record, _view(account=subject, balance=20), configuration) == ("confirmed", "check_agrees")


@pytest.mark.parametrize("conditions, check_args, decided", [
    ({"currency": "EUR"}, {"account": "A", "currency": "EUR"}, ("confirmed", "check_agrees")),
    ({"currency": "EUR"}, {"account": "A", "currency": "USD"}, ("provisional", "check_under_other_condition:currency")),
    ({"currency": "EUR"}, {"account": "A"}, ("provisional", "check_unbound:condition:currency")),
    ({"branch": "north"}, {"account": "A"}, ("provisional", "check_unbound:condition:branch")),
    (None, {"account": "A", "currency": "EUR"}, ("provisional", "check_under_condition:currency")),
    (None, {"account": "A"}, ("confirmed", "check_agrees")),
])
def test_every_condition_is_bound_and_equal(conditions, check_args, decided):
    configuration = _configuration()
    record = _claim(configuration, conditions=conditions, check_args=check_args)
    assert _status(record, _view(account="A", balance=20), configuration) == decided


@pytest.mark.parametrize("rule, basis, reason", [
    ("synapse.memory.hypothesis-check/v1", "current", "checked_under_another_rule"),
    (None, "current", "checked_under_another_rule"),
    (hypotheses.CHECK_RULE, "other", "check_basis_changed"),  # Decided under another contract or graph.
    (hypotheses.CHECK_RULE, None, "check_basis_changed"),
    (hypotheses.CHECK_RULE, "unadmitted", "check_basis_changed"),  # The checking tool is no longer admitted.
    (hypotheses.CHECK_RULE, "current", "court_record"),
])
def test_a_status_is_reused_only_under_its_rule_and_check_basis(rule, basis, reason):
    configuration = _configuration()
    record = _claim(configuration)
    current = hypotheses.check_basis(record, configuration)
    known = {"status": "confirmed", "window": 5, "run_id": "r-1", "check_ref": {"evidence": "ev-check"},
             "basis": None, **({"rule": rule} if rule is not None else {}),
             "check_basis": {"current": current, "other": "0" * 64, None: None, "unadmitted": None}[basis]}
    if basis == "unadmitted":
        raw = copy.deepcopy(configuration.raw)
        raw["tools"]["tools"] = [item for item in raw["tools"]["tools"] if item["name"] != "check"]
        current = hypotheses.check_basis(record, parse_memory_configuration(raw))
    found = hypotheses.reuse(record, known, {}, 6, configuration.parameters, current)
    assert (found["status"], found["reason"]) == (("confirmed" if reason == "court_record" else None), reason)


@pytest.mark.parametrize("change", ["contract", "provenance", "identity"])
def test_the_check_basis_names_the_contract_the_provenance_and_the_identity_rules(change):
    configuration = _configuration()
    record = _claim(configuration)
    raw = copy.deepcopy(configuration.raw)
    if change == "contract":
        raw["tools"]["tools"][1]["contract"]["verifies"]["scope"] = {"value": "cards"}
    elif change == "provenance":
        raw["tools"]["provenance"]["ledger:bank"]["ancestors"] = ["core:bank"]
    changed = (dataclasses.replace(configuration, identity={"bank": "insensitive"}) if change == "identity"
               else parse_memory_configuration(raw))
    assert hypotheses.check_basis(record, changed) != hypotheses.check_basis(record, configuration)
    assert hypotheses.check_basis(record, parse_memory_configuration(copy.deepcopy(configuration.raw))) == \
        hypotheses.check_basis(record, configuration)


def _admission(configuration, record, view):
    decision = hypotheses.resolve(record, view, configuration)
    known = {**record, **decision, "source_ref": record["source"]["ref"],
             "source": {"tool": "source", "name": "core:bank"},
             "verification": hypotheses.verification(record, configuration, method="probe", observation={
                 "evidence": "ev-check"}, checked_in="run-1", window=None, case=None, reason=decision["reason"])}
    card = {"id": "card-A", "entity": "A", "attribute": "balance", "value": 20, "source": "core:bank",
            "hypothesis": record["id"], "content": "account balance"}
    return admit([card], {"entity": "A", "attribute": "balance", "keys": ["account", "balance"], "scope": "bank"},
                 lambda _: known)["decision"]


def test_admission_takes_a_status_only_from_a_check_about_the_claim():
    configuration = _configuration()
    assert _admission(configuration, _claim(configuration, check_args={"account": "B"}),
                      _view(account="B", balance=20)) != "admitted"
    assert _admission(configuration, _claim(configuration), _view(account="A", balance=20)) == "admitted"
