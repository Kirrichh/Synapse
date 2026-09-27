"""Identity of entities and the basis of admission, over plain data (review R3, R4).

Search tokens find candidates; they never decide which entity a record is
about, and a record never grants itself the authority to be treated as
established. Each refusal stands next to the correct case that still passes:

* identity — ``account-1`` is not ``account-2``, ``a-b`` is not ``b-a``, the
  same name in another namespace or of another type is another entity, case
  is folded only where the operator declares a namespace case-insensitive,
  ``"1"``, ``1`` and ``true`` are three identifiers, an object recreated under
  its name is another incarnation, and a reference that names no incarnation
  cannot stand for one that does; an alias is a confirmed entity hypothesis —
  revoked, unknown or linking other entities, it links nothing;
* basis — a ``status`` the record states grants nothing, outside a memory
  session there is no basis at all, and a confirmed hypothesis establishes
  only the very statement: the same entity, property and typed value (a
  boolean denial is entailed by the negated value), the same conditions, the
  claim's scope and the source version the record was read from; a hypothesis
  about an entity is no confirmation of a content, and a basis that is no
  longer confirmed no longer admits.
"""
from __future__ import annotations

import pytest

from synapse import entity_identity
from synapse.palace_admission import admit

KEYS = ["account", "balance"]


def _record(entity="account-1", **changes):
    return {"id": "card-1", "entity": entity, "attribute": "balance", "value": 20, "source": "bank:ledger",
            "content": "account balance", "hypothesis": "hyp_value", **changes}


def _basis(**changes):
    return {"status": "confirmed", "aspect": "content", "subject": "account-1", "statement": {"balance": 20},
            "scope": "bank", "conditions": {}, "source_ref": None, **changes}


def _decide(record, entity="account-1", journal=None, rules=None, **claim):
    journal = {"hyp_value": _basis()} if journal is None else journal
    return admit([record], {"entity": entity, "attribute": "balance", "keys": KEYS, **claim},
                 hypothesis_of=journal.get, identity_rules=rules)


def _reasons(record, entity="account-1", journal=None, rules=None, **claim):
    return _decide(record, entity, journal, rules, **claim)["checked"][0]["reasons"]


@pytest.mark.parametrize("left, right, reason", [
    ("account-1", "account-2", "another_entity"),
    ("a-b", "b-a", "another_entity"),
    ({"namespace": "bank", "id": "acct"}, {"namespace": "shop", "id": "acct"}, "another_namespace"),
    ({"type": "user", "id": "7"}, {"type": "group", "id": "7"}, "another_type"),
    ("Account-1", "account-1", "another_entity"),
    ({"id": "1"}, {"id": 1}, "another_entity"),
    ({"id": 1}, {"id": True}, "another_entity"),
    ({"id": "db", "uid": "u-1"}, {"id": "db", "uid": "u-2"}, "another_incarnation"),
    ({"id": "db"}, {"id": "db", "uid": "u-2"}, "incarnation_unknown"),
])
def test_identity_is_exact(left, right, reason):
    assert entity_identity.compare(left, right) == reason
    assert entity_identity.compare(left, left) is None and entity_identity.compare(right, right) is None


def test_case_folds_only_in_a_namespace_the_operator_declares_insensitive():
    rules = entity_identity.parse_rules({"namespaces": {"dns": {"case": "insensitive"}}})
    assert entity_identity.compare({"namespace": "dns", "id": "Example.org"},
                                   {"namespace": "dns", "id": "example.org"}, rules) is None
    assert entity_identity.compare({"namespace": "bank", "id": "Acct"}, {"namespace": "bank", "id": "acct"},
                                   rules) == "another_entity"
    with pytest.raises(entity_identity.EntityViolation):
        entity_identity.parse_rules({"namespaces": {"dns": {"case": "loose"}}})


def test_similar_names_of_other_entities_never_answer():
    # Search tokens of both are {account}: the lexical filter passes, identity does not.
    assert _reasons(_record("account-2"), journal={"hyp_value": _basis(subject="account-2")}) == ["another_entity"]
    assert _decide(_record("account-1"))["decision"] == "admitted"
    recreated = {"id": "db", "uid": "u-2"}
    assert _reasons(_record(recreated), entity={"id": "db", "uid": "u-1"},
                    journal={"hyp_value": _basis(subject=recreated)}) == ["another_incarnation"]


def _alias(**changes):
    return {"status": "confirmed", "aspect": "entity", "subject": "acct-7", "statement": {"same_as": "account-1"},
            "scope": "bank", "conditions": {}, "source_ref": "ev-alias", **changes}


def test_an_alias_is_a_confirmed_identity_link_and_nothing_else():
    journal = {"hyp_value": _basis(), "hyp_alias": _alias()}
    assert _decide(_record(), entity="acct-7", journal=journal, identified_by="hyp_alias")["decision"] == "admitted"
    assert _decide(_record(), entity="acct-7", journal=journal)["decision"] == "abstained"  # No link named.
    revoked = {**journal, "hyp_alias": _alias(status="provisional")}
    assert _reasons(_record(), entity="acct-7", journal=revoked, identified_by="hyp_alias") == [
        "another_entity", "alias_not_established:provisional"]
    elsewhere = {**journal, "hyp_alias": _alias(statement={"same_as": "account-9"})}
    assert "alias_links_other_entities" in _reasons(_record(), entity="acct-7", journal=elsewhere,
                                                     identified_by="hyp_alias")
    content = {**journal, "hyp_alias": _alias(aspect="content")}
    assert "alias_not_an_identity_link" in _reasons(_record(), entity="acct-7", journal=content,
                                                     identified_by="hyp_alias")


def test_a_record_never_grants_itself_the_authority_to_be_established():
    stated = _record(status="confirmed", hypothesis=None)
    assert _reasons(stated) == ["no_verified_basis"]
    # Outside a memory session there is no verification journal at all.
    decision = admit([_record(status="confirmed")], {"entity": "account-1", "attribute": "balance", "keys": KEYS})
    assert decision["decision"] == "abstained" and decision["checked"][0]["reasons"] == ["basis_unknown"]
    assert decision["checked"][0]["stated_status"] == "confirmed" and decision["checked"][0]["status"] is None


@pytest.mark.parametrize("basis, record, reason", [
    ({"subject": "account-2"}, {}, "basis_about_another_entity"),
    ({"statement": {"limit": 20}}, {}, "basis_about_another_property"),
    ({"statement": {"balance": "20"}}, {}, "basis_states_another_value"),
    ({"statement": {"balance": 21}}, {}, "basis_states_another_value"),
    ({"conditions": {"currency": "eur"}}, {}, "basis_under_other_conditions"),
    ({"scope": "shop"}, {"scope": "bank"}, "basis_for_another_scope"),
    ({"source_ref": "ev-old"}, {"source": {"tool": "ledger", "ref": "ev-new"}}, "basis_for_another_version"),
    ({"aspect": "entity"}, {}, "basis_not_about_content"),
    ({"status": "provisional"}, {}, "not_confirmed:provisional"),
    ({"status": "refuted"}, {}, "not_confirmed:refuted"),
])
def test_a_basis_establishes_only_its_own_statement(basis, record, reason):
    assert reason in _reasons(_record(**record), journal={"hyp_value": _basis(**basis)})


def test_the_same_value_of_another_statement_confirms_nothing():
    healthy = {"id": "card-h", "entity": "service-b", "attribute": "healthy", "value": True, "source": "ops:status",
               "content": "service healthy", "hypothesis": "hyp_a"}
    journal = {"hyp_a": _basis(subject="service-a", statement={"healthy": True})}
    reasons = admit([healthy], {"entity": "service-b", "attribute": "healthy", "keys": ["service", "healthy"]},
                    hypothesis_of=journal.get)["checked"][0]["reasons"]
    assert reasons == ["basis_about_another_entity"]


def test_a_boolean_denial_is_entailed_by_its_negated_value_and_the_right_basis_still_admits():
    denial = _record(attribute="overdrawn", value=True, polarity=False)
    journal = {"hyp_value": _basis(statement={"overdrawn": False})}
    decision = admit([denial], {"entity": "account-1", "attribute": "overdrawn", "keys": KEYS, "polarity": False},
                     hypothesis_of=journal.get)
    assert decision["decision"] == "admitted"
    wrong = {"hyp_value": _basis(statement={"overdrawn": True})}
    assert "basis_states_another_value" in admit(
        [denial], {"entity": "account-1", "attribute": "overdrawn", "keys": KEYS, "polarity": False},
        hypothesis_of=wrong.get)["checked"][0]["reasons"]
    versioned = _record(source={"tool": "ledger", "ref": "ev-1"})
    assert _decide(versioned, journal={"hyp_value": _basis(source_ref="ev-1")})["decision"] == "admitted"
