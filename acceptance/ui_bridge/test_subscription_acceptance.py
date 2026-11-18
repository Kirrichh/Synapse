"""Real retained-owner reads, bounded class loss and current delivery authority."""
from dataclasses import replace
from threading import Event, Thread

import pytest

from acceptance.ui_bridge.test_authorized_invocations_acceptance import setup
from acceptance.ui_bridge.test_domain_session_acceptance import exchange, request
from synapse.machine.domain_session import DomainSession
from synapse.machine.contract import PROFILE, ProtocolError
from synapse.machine import domain_contract


def connected(tmp_path):
    store, command, invocation, authority, registry, app, grant, connection = setup(tmp_path)
    session = DomainSession(authority)
    # The fixture supplies an authenticated connection; no production bypass.
    session.connection = connection
    return session, store, command, invocation, authority, registry, grant


def subscribe(session, grant, invocation, source="invocation.state"):
    return exchange(session, "delivery.subscribe", {"source": source,
        "invocation_ids": [invocation.invocation_id]}, grant)["result"]


def action(session, method, confirm):
    return exchange(session, "delivery." + method,
        {"subscription_id": confirm["identity"]["subscription_id"]})


def test_confirm_snapshot_then_owner_coverage_and_quiet_progress(tmp_path):
    session, store, command, invocation, _, _, grant = connected(tmp_path)
    confirm = subscribe(session, grant, invocation)
    assert confirm["type"] == "subscription.confirm"
    assert confirm["identity"]["delivery_class"] == "StateSync"
    assert action(session, "collect", confirm)["code"] == "NOT_READY"
    snapshot = action(session, "snapshot", confirm)["result"]
    assert snapshot["snapshot"]["position"] >= confirm["deliver_from"]
    assert snapshot["snapshot"]["generation"] == confirm["identity"]["generation"]
    assert action(session, "snapshot", confirm)["code"] == "COMMAND_INVALID"
    exchange(session, "invocation.cancel", {"command": command}, grant)
    assert action(session, "collect", confirm)["result"]["queued"]
    event = exchange(session, "delivery.next", {})["result"]
    assert event["identity"] == confirm["identity"] and not event["gap"]
    assert event["payload"]["from"] == snapshot["snapshot"]["position"]
    assert event["payload"]["changes"][0]["item"]["cancel_requested"]
    assert action(session, "collect", confirm)["result"]["queued"]
    quiet = exchange(session, "delivery.next", {})["result"]["payload"]
    assert quiet == {"generation": event["payload"]["generation"],
                     "from": event["payload"]["to"], "to": event["payload"]["to"], "changes": []}
    assert exchange(session, "delivery.next", {})["result"]["type"] == "delivery.empty"


@pytest.mark.parametrize("source,reason", [("invocation.state", "RESNAPSHOT"), ("invocation.record", "REREAD")])
def test_loss_invalidates_stream_and_requires_new_identity(tmp_path, source, reason):
    session, _, _, invocation, _, _, grant = connected(tmp_path)
    confirm = subscribe(session, grant, invocation, source)
    if source == "invocation.state":
        action(session, "snapshot", confirm)
    for _ in range(PROFILE["projection_capacity"]):
        assert action(session, "collect", confirm)["result"]["queued"]
    assert not action(session, "collect", confirm)["result"]["queued"]
    assert action(session, "collect", confirm)["code"] == "CURSOR_UNAVAILABLE"
    lost = exchange(session, "delivery.next", {})["result"]
    assert lost == {"type":"delivery.invalidated", "identity":confirm["identity"], "reason":reason}
    assert exchange(session, "delivery.next", {})["result"]["type"] == "delivery.empty"
    assert action(session, "collect", confirm)["code"] == "CURSOR_UNAVAILABLE"
    action(session, "unsubscribe", confirm)
    new = subscribe(session, grant, invocation, source)
    assert new["identity"]["epoch"] != confirm["identity"]["epoch"]
    assert new["identity"]["subscription_id"] != confirm["identity"]["subscription_id"]


def test_live_overflow_marks_gap_and_does_not_claim_invocation_liveness(tmp_path):
    session, store, command, invocation, _, _, grant = connected(tmp_path)
    confirm = subscribe(session, grant, invocation, "connection.health")
    for _ in range(PROFILE["projection_capacity"]):
        assert action(session, "collect", confirm)["result"]["queued"]
    assert not action(session, "collect", confirm)["result"]["queued"]
    # A full projection queue cannot prevent command acceptance.
    assert exchange(session, "invocation.cancel", {"command":command}, grant)["result"]["outcome"] == "CANCEL_REQUESTED"
    event = exchange(session, "delivery.next", {})["result"]
    assert event["gap"] and event["identity"]["delivery_class"] == "Live"
    assert set(event["payload"]) == {"core", "monotonic_ms"}
    assert event["payload"]["core"] == "RESPONDING"
    assert not exchange(session, "delivery.next", {})["result"]["gap"]
    action(session, "unsubscribe", confirm)
    assert store.is_cancelled(invocation.invocation_id)
    assert exchange(session, "delivery.next", {})["result"]["type"] == "delivery.empty"


def test_revocation_after_collection_refuses_queued_data_and_keeps_owner_result(tmp_path):
    session, store, command, invocation, authority, _, grant = connected(tmp_path)
    confirm = subscribe(session, grant, invocation, "invocation.record")
    action(session, "collect", confirm)
    authority.revoke(grant.grant_id)
    assert exchange(session, "delivery.next", {})["code"] == "ACCESS_DENIED"
    assert action(session, "collect", confirm)["code"] == "SUBSCRIPTION_UNKNOWN"
    assert not store.is_cancelled(invocation.invocation_id)


def test_delivery_holds_revocation_custody_until_bounded_write_finishes(tmp_path):
    session, _, _, invocation, authority, _, grant = connected(tmp_path)
    confirm = subscribe(session, grant, invocation, "invocation.record")
    action(session, "collect", confirm)
    started, finished = Event(), Event()
    def revoke():
        started.set(); authority.revoke(grant.grant_id); finished.set()
    thread = Thread(target=revoke)
    with session.response(request("delivery.next", {}), ready=True) as reply:
        assert reply["result"]["payload"]["state"] == "EXECUTION_STATE_UNKNOWN"
        thread.start()
        assert started.wait(1) and not finished.wait(0.05)
    assert finished.wait(2)
    thread.join(2)


def test_expired_grant_owner_membership_generation_and_close_are_not_bypassed(tmp_path):
    session, store, _, invocation, _, registry, grant = connected(tmp_path)
    assert exchange(session, "delivery.subscribe", {"source":"invocation.record", "invocation_ids":["foreign-invocation"]}, grant)["code"] == "OBJECT_UNAVAILABLE"
    confirm = subscribe(session, grant, invocation)
    with store.connect() as db:
        db.execute("UPDATE observation_meta SET generation=?", ("7a1b6000-1111-4111-8111-000000000001",))
    assert action(session, "snapshot", confirm)["code"] == "CURSOR_UNAVAILABLE"
    assert action(session, "snapshot", confirm)["code"] == "SUBSCRIPTION_UNKNOWN"
    confirm = subscribe(session, grant, invocation, "invocation.record")
    action(session, "collect", confirm)
    registry.registry.grants[grant.grant_id] = replace(grant, expires_at=1)
    assert exchange(session, "delivery.next", {})["code"] == "ACCESS_DENIED"
    exchange(session, "access.close", {})
    assert exchange(session, "delivery.next", {})["code"] == "AUTHENTICATION_FAILED"


def test_subscription_capacity_disposal_and_closed_client_fields(tmp_path):
    session, _, _, invocation, _, _, grant = connected(tmp_path)
    confirmations = [subscribe(session, grant, invocation) for _ in range(PROFILE["subscription_capacity"])]
    assert exchange(session, "delivery.subscribe", {"source":"invocation.state", "invocation_ids":[invocation.invocation_id]}, grant)["code"] == "BUSY"
    action(session, "unsubscribe", confirmations[0])
    assert subscribe(session, grant, invocation)["type"] == "subscription.confirm"
    for extra in ({"workspace":"foreign"}, {"delivery_class":"Live"}, {"generation":"chosen"}):
        value = request("delivery.subscribe", {"source":"invocation.state", "invocation_ids":[invocation.invocation_id], **extra}, grant)
        with pytest.raises(ProtocolError): domain_contract.validate(value)


def test_reset_and_evidence_reread_do_not_rewrite_source_or_cancel_work(tmp_path):
    session, store, command, invocation, _, _, grant = connected(tmp_path)
    confirm = subscribe(session, grant, invocation, "invocation.record")
    action(session, "collect", confirm)
    first = exchange(session, "delivery.next", {})["result"]
    assert first["payload"]["cancel_requested"] is False
    exchange(session, "invocation.cancel", {"command":command}, grant)
    action(session, "collect", confirm)
    second = exchange(session, "delivery.next", {})["result"]
    assert second["payload"]["cancel_requested"] is True
    assert second["payload"]["revision"] != first["payload"]["revision"]
    action(session, "collect", confirm)
    assert exchange(session, "delivery.reset", {})["result"]["type"] == "delivery.reset"
    assert exchange(session, "delivery.next", {})["result"]["type"] == "delivery.empty"
    assert action(session, "collect", confirm)["code"] == "SUBSCRIPTION_UNKNOWN"
    new = subscribe(session, grant, invocation, "invocation.record")
    assert new["identity"]["session_id"] != confirm["identity"]["session_id"]
    assert store.is_cancelled(invocation.invocation_id)
