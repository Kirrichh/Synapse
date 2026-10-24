"""Schema-bound connection authentication and real owner dispatch/recovery."""
from dataclasses import replace
from uuid import uuid4

import pytest

from acceptance.ui_bridge.test_authorized_invocations_acceptance import setup
from acceptance.ui_bridge.access_support import authorized
from synapse.access.records import METHODS
from synapse.commands.jcs import canonical_bytes
from synapse.machine import domain_contract as wire
from synapse.machine.domain_session import DomainSession
from synapse.machine.contract import ProtocolError
from synapse.access.records import AccessRefused
from synapse.access.registry_codec import decode_registry, encode_registry
import json


def request(method, params, grant=None):
    value = {"schema_version": wire.VERSION, "type": "request", "request_id": str(uuid4()),
             "method": method, "params": params}
    if grant:
        value.update(grant_id=grant.grant_id, grant_version=grant.version)
    return value


def exchange(session, method, params, grant=None, ready=True):
    value = request(method, params, grant)
    with session.response(value, ready=ready) as response:
        assert wire.validate(response) == response
        assert response["request_id"] == value["request_id"]
        return response


def test_domain_profile_is_explicit_and_authentication_required():
    authority, _, _, app, _, _, private = authorized()
    session = DomainSession(authority)
    assert exchange(session, "access.open", {"profile": "synapse.application/v1"}, ready=False)["code"] == "NOT_READY"
    assert session.connection is None
    assert exchange(session, "access.grants", {})["code"] == "AUTHENTICATION_FAILED"
    opened = exchange(session, "access.open", {"profile": "synapse.application/v1"})
    assert opened["result"]["profile"] == "synapse.application/v1"
    challenge = canonical_bytes(opened["result"]["challenge"])
    assert exchange(session, "access.open", {"profile": "synapse.application/v1"})["code"] == "ACCESS_INVALID"
    signed = {"public_key": app.public_key, "signature": private.sign(challenge).hex()}
    assert exchange(session, "access.authenticate", signed)["result"]["state"] == "AUTHENTICATED"
    assert exchange(session, "access.authenticate", signed)["code"] == "AUTHENTICATION_FAILED"


def test_domain_owner_route_retains_a_lost_reply_and_denies_revoked_recovery(tmp_path):
    store, command, invocation, authority, registry, app, grant, _ = setup(tmp_path)
    # Register the actual signed connection with this existing synthetic
    # registry; use a new application because a different key is not the old app.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    private = Ed25519PrivateKey.generate()
    app = authority.register(private.public_key().public_bytes_raw().hex())
    grant = authority.issue(app.application_id, grant.workspace_id, grant.resource, grant.methods,
                            grant.objects, expires_at=None)
    session = DomainSession(authority)
    opened = exchange(session, "access.open", {"profile": "synapse.application/v1"})
    signature = private.sign(canonical_bytes(opened["result"]["challenge"])).hex()
    assert exchange(session, "access.authenticate", {"public_key": app.public_key, "signature": signature})["type"] == "result"
    grants = exchange(session, "access.grants", {})["result"]["grants"]
    assert [item["grant_id"] for item in grants] == [grant.grant_id]
    assert all("resource" not in item and "public_key" not in item for item in grants)
    cancelled = exchange(session, "invocation.cancel", {"command": command}, grant)
    assert cancelled["result"]["outcome"] == "CANCEL_REQUESTED"
    assert store.is_cancelled(invocation.invocation_id)
    recovered = exchange(session, "command.status", {"command_id": command["command_id"], "recipient": invocation.invocation_id}, grant)
    assert recovered["result"]["replayed"]
    authority.revoke(grant.grant_id)
    assert exchange(session, "access.grants", {})["result"] == {"grants": []}
    assert exchange(session, "command.status", {"command_id": command["command_id"], "recipient": invocation.invocation_id}, grant)["code"] == "ACCESS_DENIED"
    session.close()
    assert exchange(session, "access.grants", {})["code"] == "AUTHENTICATION_FAILED"


def test_domain_schema_preserves_closed_vocabulary_and_nested_command_contract():
    assert set(wire.SCHEMA["$defs"]["operation"]["enum"]) == METHODS
    authority, _, _, _, grant, _, _ = authorized()
    session = DomainSession(authority)
    base = request("access.open", {"profile": "synapse.application/v1"})
    for value in ({**base, "principal": "administrator"}, {**base, "params": {"profile": "future"}},
                  {**base, "request_id": "bad-id"}, {**base, "method": "invocation.future"}):
        with pytest.raises(ProtocolError, match="^SCHEMA_INVALID$"):
            with session.response(value, ready=True):
                pytest.fail("unknown authority input accepted")
    command = {"command_id": str(uuid4()), "method": "invocation.cancel", "method_version": "1",
               "recipient": "invocation-one", "expected_revision": "a" * 64,
               "params": {"invocation_id": "invocation-one"}}
    assert wire.validate(request("invocation.cancel", {"command": command}, grant))
    for bad in ({**command, "method_version": "2"}, {**command, "params_hash": "a" * 64}):
        with pytest.raises(ProtocolError):
            wire.validate(request("invocation.cancel", {"command": bad}, grant))


def test_instance_registration_is_unique_and_duplicate_persistent_keys_are_refused():
    authority, registry, _, app, _, _, _ = authorized()
    before = encode_registry(registry.registry)
    with pytest.raises(AccessRefused, match="^ACCESS_INVALID$"):
        authority.register(app.public_key)
    assert encode_registry(registry.registry) == before
    state = json.loads(before)
    state["applications"].append({**state["applications"][0], "application_id": str(uuid4())})
    with pytest.raises(AccessRefused, match="^ACCESS_UNAVAILABLE$"):
        decode_registry(canonical_bytes(state))


def test_close_replaces_challenge_and_old_proof_never_authenticates_new_connection():
    authority, registry, _, app, _, _, private = authorized()
    session = DomainSession(authority)
    first = exchange(session, "access.open", {"profile": "synapse.application/v1"})["result"]["challenge"]
    assert first["installation"] == registry.registry.installation_id
    signature = private.sign(canonical_bytes(first)).hex()
    assert exchange(session, "access.close", {})["result"]["state"] == "CLOSED"
    second = exchange(session, "access.open", {"profile": "synapse.application/v1"})["result"]["challenge"]
    assert first["connection"] != second["connection"] and first["nonce"] != second["nonce"]
    assert exchange(session, "access.authenticate", {"public_key":app.public_key, "signature":signature})["code"] == "AUTHENTICATION_FAILED"
    assert exchange(session, "access.grants", {})["code"] == "AUTHENTICATION_FAILED"
    for key in (app.public_key + "\n", "G" * 64):
        with pytest.raises(ProtocolError):
            wire.validate(request("access.authenticate", {"public_key":key, "signature":signature}))


def test_scoped_grant_listing_refuses_revoked_application_and_excludes_other_apps():
    authority, registry, _, app, grant, _, private = authorized()
    session = DomainSession(authority)
    opened = exchange(session, "access.open", {"profile": "synapse.application/v1"})["result"]["challenge"]
    exchange(session, "access.authenticate", {"public_key":app.public_key,
             "signature":private.sign(canonical_bytes(opened)).hex()})
    other = authority.register("f" * 64)
    authority.issue(other.application_id, grant.workspace_id, grant.resource, grant.methods, grant.objects, expires_at=None)
    assert [item["grant_id"] for item in exchange(session, "access.grants", {})["result"]["grants"]] == [grant.grant_id]
    registry.registry.grants[grant.grant_id] = replace(grant, expires_at=1)
    assert exchange(session, "access.grants", {})["result"]["grants"] == []
    registry.registry.applications[app.application_id] = replace(app, revoked=True)
    assert exchange(session, "access.grants", {})["code"] == "ACCESS_DENIED"


def test_transport_failure_does_not_attempt_a_second_response_or_leak_custody(tmp_path):
    from threading import Event, Thread
    from time import sleep
    _, _, _, authority, registry, _, grant, _ = setup(tmp_path)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    private = Ed25519PrivateKey.generate()
    app = authority.register(private.public_key().public_bytes_raw().hex())
    grant = authority.issue(app.application_id, grant.workspace_id, grant.resource, grant.methods, grant.objects, expires_at=None)
    session = DomainSession(authority)
    opened = exchange(session, "access.open", {"profile":"synapse.application/v1"})["result"]["challenge"]
    exchange(session, "access.authenticate", {"public_key":app.public_key, "signature":private.sign(canonical_bytes(opened)).hex()})
    started, revoked = Event(), Event()
    def revoke():
        started.set()
        authority.revoke(grant.grant_id)
        revoked.set()
    thread = Thread(target=revoke)
    with pytest.raises(OSError, match="synthetic transport loss"):
        with session.response(request("access.grants", {}), ready=True) as result:
            assert result["result"]["grants"]
            thread.start()
            assert started.wait(1)
            assert not revoked.wait(0.05)
            raise OSError("synthetic transport loss")
    assert revoked.wait(2)
    thread.join(2)
    assert exchange(session, "access.grants", {})["result"]["grants"] == []
