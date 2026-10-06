"""Which entity a fact is about, through the canonical launch (review R4, R3).

Memory knows the balance of two accounts whose names share every search token
(``account-1`` and ``account-2``): asked about the second, the first account's
statement is a candidate the search cannot tell apart, and admission refuses
it as another entity while the second account's own statement, confirmed by
the independent ledger, is admitted.

A database is deleted and created again under its old name: the statement
about the old incarnation is found, its value is even the one the new
incarnation has, and it is still not admitted for the new one — and the
billing service, which quotes the old incarnation, confirms nothing about the
new one.

An alias is a confirmed link, never a similarity: a directory names what
``acct-7`` stands for and an independent registry confirms it, so knowledge
about ``account-1`` answers a question about ``acct-7``. When the registry
contradicts the directory, the link is refuted and the same question is
not answered.
"""
from __future__ import annotations

from acceptance.memory import _catalog as catalog

AT = "2026-03-01"
BALANCE = "Balance of {} is {} euro"


def _account(world, name, value, entity=None):
    catalog.publish(world, "catalog", name, "balance", value, text=BALANCE.format(name, value), start="2026-01-01")
    catalog.publish(world, "billing", name, "balance", value, entity=name if entity is None else entity)


def _checked(asked, record_id):
    reasons, = [item["reasons"] for item in asked["admission"]["checked"] if item["id"] == record_id]
    return reasons


def test_similar_names_of_other_accounts_never_answer(tmp_path):
    world = catalog.world(tmp_path)
    _account(world, "account-1", 20)
    _account(world, "account-2", 35)
    first = catalog.learn(world, "learn-1", "account-1")["knowledge"]["declared"][0]["statement"]
    second = catalog.learn(world, "learn-2", "account-2")["knowledge"]["declared"][0]["statement"]

    asked = catalog.ask(world, "ask-2", "account-2", "balance", "account balance", at=AT, channels=["lexical"])
    found = {item["id"]: item for item in asked["search"]["candidates"]}
    assert set(found) == {first, second}  # The search cannot tell them apart.
    assert asked["admission"]["decision"] == "admitted" and asked["admission"]["fact"] == second
    assert _checked(asked, first) == ["another_entity", "basis_about_another_entity", "basis_states_another_value",
                                      "basis_for_another_version"]


def test_an_object_recreated_under_its_name_is_another_entity(tmp_path):
    world = catalog.world(tmp_path)
    old = {"namespace": "db", "type": "database", "id": "orders", "uid": "u-1"}
    new = {**old, "uid": "u-2"}
    _account(world, "orders", 20, entity=old)  # Billing quotes the old incarnation.
    learned = catalog.learn(world, "learn-old", "orders", subject=old)["knowledge"]["declared"][0]["statement"]
    # Recreated with the same balance: the old statement's value is the new object's value too.
    asked = catalog.ask(world, "ask-new", "orders", "balance", "orders balance", at=AT, subject=new,
                        channels=["lexical"])
    candidate, = asked["search"]["candidates"]
    assert candidate["id"] == learned and candidate["entity"] == old and candidate["value"] == 20
    assert asked["admission"]["decision"] == "abstained"
    # Billing quotes the old incarnation: its answer is no check of the new one, which stays provisional.
    assert _checked(asked, learned) == ["another_incarnation", "not_confirmed:provisional",
                                        "basis_about_another_entity"]
    # The old incarnation itself is still answered.
    same = catalog.ask(world, "ask-old", "orders", "balance", "orders balance", at=AT, subject=old,
                       channels=["lexical"])
    assert same["admission"]["decision"] == "admitted" and same["admission"]["fact"] == learned


def test_an_alias_answers_only_while_its_link_is_confirmed(tmp_path):
    world = catalog.world(tmp_path)
    _account(world, "account-1", 20)
    learned = catalog.learn(world, "learn-1", "account-1")["knowledge"]["declared"][0]["statement"]

    catalog.name(world, "acct-7", directory="account-1", registry="account-1")
    linked = catalog.identify(world, "ask-alias", "account-1", "acct-7", "balance", "account balance", at=AT)
    assert linked["admission"]["decision"] == "admitted" and linked["admission"]["fact"] == learned
    checked, = [item for item in linked["admission"]["checked"] if item["id"] == learned]
    assert checked["alias"] == linked["admission"]["claim"]["identified_by"]

    # The registry now says the name stands for another account: the link is refuted, nothing is answered.
    catalog.name(world, "acct-7", directory="account-1", registry="account-9")
    refuted = catalog.identify(world, "ask-refuted", "account-1", "acct-7", "balance", "account balance", at=AT)
    assert refuted["admission"]["decision"] == "abstained"
    assert _checked(refuted, learned) == ["another_entity", "alias_not_established:refuted"]
