"""Delegation, forgery, revocation and protected-result authority boundaries."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from time import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from acceptance.ui_bridge.access_support import RESOURCE, authorized
from synapse.access.authentication import AccessConnection
from synapse.access.authority import WorkspaceAccessAuthority
from synapse.access.records import AccessRefused, Grant, READ_METHODS
from synapse.access.registry_codec import decode_registry, encode_registry


def test_exact_read_preset_and_application_binding():
    authority, registry, resources, app, grant, connection, _ = authorized(tuple(READ_METHODS))
    with authority.authorize(connection, grant.grant_id, 1, "invocation.inspect", ("invocation-one",)) as (scope, root):
        assert scope.principal == app.application_id
        assert scope.workspace == "workspace-one" and root == RESOURCE and resources.active == 1
    assert resources.active == 0
    before = registry.commits
    for method, objects in [("invocation.cancel", ("invocation-one",)), ("workspace.future", ("invocation-one",)),
                            ("invocation.inspect", ("invocation-two",))]:
        with pytest.raises(AccessRefused, match="^ACCESS_DENIED$"):
            with authority.authorize(connection, grant.grant_id, 1, method, objects):
                pytest.fail("forbidden scope entered")
    assert registry.commits == before


def test_revoked_grant_blocks_saved_results_and_recovery_without_cancelling_work():
    authority, registry, _, _, grant, connection, _ = authorized()
    completed = []
    with authority.authorize(connection, grant.grant_id, 1, "invocation.cancel", ("invocation-one",)):
        completed.append("durable acceptance")
    revoked = authority.revoke(grant.grant_id)
    assert revoked.version == 2 and revoked.revoked
    completed.append("owner completion independent of app rights")
    for method in ("invocation.inspect", "command.status", "invocation.cancel"):
        with pytest.raises(AccessRefused, match="^ACCESS_DENIED$"):
            with authority.authorize(connection, grant.grant_id, 1, method, ("invocation-one",)):
                completed.append("forbidden disclosure")
    assert len(completed) == 2
    commits = registry.commits
    assert authority.revoke(grant.grant_id) == revoked and registry.commits == commits


def test_regrant_does_not_resurrect_a_queued_old_grant():
    authority, _, _, app, grant, connection, _ = authorized()
    authority.revoke(grant.grant_id)
    new = authority.issue(app.application_id, grant.workspace_id, RESOURCE, grant.methods, grant.objects, expires_at=None)
    with pytest.raises(AccessRefused):
        with authority.authorize(connection, grant.grant_id, 1, "invocation.inspect", grant.objects):
            pytest.fail("old request accepted")
    with authority.authorize(connection, new.grant_id, 1, "invocation.inspect", new.objects):
        pass


def test_revocation_and_acceptance_have_one_serial_order():
    authority, _, _, _, grant, connection, _ = authorized()
    accepting, release, revoking, revoked = Event(), Event(), Event(), Event()

    def accept():
        with authority.authorize(connection, grant.grant_id, 1, "invocation.cancel", grant.objects):
            accepting.set()
            assert release.wait(5)
            assert not revoked.is_set()
        return "accepted"

    def revoke():
        revoking.set()
        authority.revoke(grant.grant_id)
        revoked.set()

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(accept)
        assert accepting.wait(5)
        second = pool.submit(revoke)
        assert revoking.wait(5)
        assert not revoked.wait(0.05)
        release.set()
        assert first.result() == "accepted"
        second.result()
    assert revoked.is_set()
    with pytest.raises(AccessRefused):
        with authority.authorize(connection, grant.grant_id, 1, "invocation.cancel", grant.objects):
            pytest.fail("revocation lost the acceptance race")


def test_admin_context_required_for_every_registry_mutation():
    authority, registry, _, app, grant, _, private = authorized()
    before = encode_registry(registry.registry)
    registry.administrative = False
    for action in (lambda: authority.register(private.public_key().public_bytes_raw().hex()),
                   lambda: authority.issue(app.application_id, "workspace-two", RESOURCE, grant.methods, grant.objects, expires_at=None),
                   lambda: authority.revoke(grant.grant_id)):
        with pytest.raises(AccessRefused, match="^ADMIN_REQUIRED$"):
            action()
    assert encode_registry(registry.registry) == before


def test_resource_change_expiry_and_foreign_application_refuse_without_disclosure():
    authority, registry, resources, app, grant, connection, _ = authorized()
    resources.current = replace(RESOURCE, file_id="replacement")
    with pytest.raises(AccessRefused, match="^RESOURCE_CHANGED$"):
        with authority.authorize(connection, grant.grant_id, 1, "invocation.inspect", grant.objects):
            pytest.fail("changed resource disclosed")
    resources.current = RESOURCE
    for changed in (replace(grant, expires_at=int(time()) - 1), replace(grant, application_id="foreign-app"),
                    replace(grant, version=2)):
        registry.registry.grants[grant.grant_id] = changed
        with pytest.raises(AccessRefused, match="^ACCESS_DENIED$"):
            with authority.authorize(connection, grant.grant_id, 1, "invocation.inspect", grant.objects):
                pytest.fail("expired or foreign grant disclosed")
    registry.registry.grants[grant.grant_id] = grant
    registry.registry.applications[app.application_id] = replace(app, revoked=True)
    with pytest.raises(AccessRefused):
        with authority.authorize(connection, grant.grant_id, 1, "invocation.inspect", grant.objects):
            pytest.fail("revoked application disclosed")


def test_challenge_cannot_be_replayed_or_used_on_another_authority():
    authority, registry, resources, app, grant, _, private = authorized()
    first, second = AccessConnection("installation-one", "pipe-one"), AccessConnection("installation-one", "pipe-two")
    signature = private.sign(first.challenge())
    authority.authenticate(first, app.public_key, signature)
    with pytest.raises(AccessRefused, match="^AUTHENTICATION_FAILED$"):
        authority.authenticate(first, app.public_key, signature)
    with pytest.raises(AccessRefused, match="^AUTHENTICATION_FAILED$"):
        authority.authenticate(second, app.public_key, signature)
    with pytest.raises(AccessRefused):
        second.challenge()
    other = WorkspaceAccessAuthority(registry, resources)
    with pytest.raises(AccessRefused):
        with other.authorize(first, grant.grant_id, 1, "invocation.inspect", grant.objects):
            pytest.fail("session crossed authority instances")
    first.close()
    with pytest.raises(AccessRefused):
        with authority.authorize(first, grant.grant_id, 1, "invocation.inspect", grant.objects):
            pytest.fail("closed pipe retained access")


def test_client_identifier_and_wrong_signature_are_not_authentication():
    authority, _, _, app, grant, _, _ = authorized()
    connection = AccessConnection("installation-one", "pipe-one")
    with pytest.raises(AccessRefused):
        with authority.authorize(connection, grant.grant_id, 1, "invocation.inspect", grant.objects):
            pytest.fail("unproved client obtained data")
    signature = Ed25519PrivateKey.generate().sign(connection.challenge())
    with pytest.raises(AccessRefused, match="^AUTHENTICATION_FAILED$"):
        authority.authenticate(connection, app.public_key, signature)


def test_strict_registry_roundtrip_and_corrupt_state_is_not_absence():
    _, registry, _, _, grant, _, _ = authorized()
    raw = encode_registry(registry.registry)
    assert decode_registry(raw) == registry.registry
    for changed in (b"{}", raw.replace(b'"revoked":false', b'"revoked":0'),
                    raw.replace(b'"version":1', b'"version":true'), raw + b"x"):
        with pytest.raises(AccessRefused, match="^ACCESS_UNAVAILABLE$"):
            decode_registry(changed)
    for methods in (("invocation.*",), (), ("invocation.inspect", "invocation.inspect")):
        with pytest.raises(AccessRefused):
            replace(grant, methods=methods)
