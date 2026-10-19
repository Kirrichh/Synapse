"""Current access intersected with real retained invocation-owner records."""
from dataclasses import replace
from uuid import uuid4

import pytest

from acceptance.ui_bridge.access_support import authorized
from acceptance.ui_bridge.test_invocation_commands_acceptance import pending
from synapse.access.records import AccessRefused, ResourceIdentity
from synapse.agents.invocation_commands import InvocationCommands
from synapse.agents.invocation_observation import InvocationObservation
from synapse.commands.binding import CommandRefused, CommandScope
from synapse.machine.invocations import AuthorizedInvocations


def setup(tmp_path):
    _, store, command, request, _ = pending(tmp_path)
    resource = ResourceIdentity(str(store.root), "synthetic-volume", "synthetic-file")
    authority, registry, resources, app, grant, connection, _ = authorized(
        ("invocation.inspect", "invocation.cancel", "command.status", "invocation.snapshot", "invocation.changes"),
        resource=resource, objects=(request.invocation_id, "foreign-invocation"))
    return store, command, request, authority, registry, app, grant, connection


def test_authorized_cancellation_lost_reply_and_revoked_recovery(tmp_path):
    store, command, request, authority, _, app, grant, connection = setup(tmp_path)
    adapter = AuthorizedInvocations(authority)
    with adapter.response(connection, grant.grant_id, 1, "invocation.cancel", {"command": command}) as result:
        assert result["outcome"] == "CANCEL_REQUESTED" and not result["replayed"]
    assert store.is_cancelled(request.invocation_id)
    params = {"command_id": command["command_id"], "recipient": request.invocation_id}
    with adapter.response(connection, grant.grant_id, 1, "command.status", params) as result:
        assert result["replayed"]
    authority.revoke(grant.grant_id)
    for method, payload in (("command.status", params), ("invocation.cancel", {"command": command}),
                            ("invocation.inspect", {"invocation_id": request.invocation_id})):
        with pytest.raises(AccessRefused, match="^ACCESS_DENIED$"):
            with adapter.response(connection, grant.grant_id, 1, method, payload):
                pytest.fail("revoked app received retained data")
    # Revocation cannot erase the owner's durable receipt or undo cancellation.
    retained = InvocationCommands(store).status(CommandScope(app.application_id, grant.workspace_id),
                                                command["command_id"], request.invocation_id)
    assert retained["replayed"] and store.is_cancelled(request.invocation_id)


def test_granted_object_name_does_not_invent_membership(tmp_path):
    store, command, request, authority, _, _, grant, connection = setup(tmp_path)
    adapter = AuthorizedInvocations(authority)
    # Even an administrator's declared object scope cannot invent a retained
    # owner's record. A matching client workspace string is insufficient.
    for method, payload in (("invocation.inspect", {"invocation_id": "foreign-invocation"}),
                            ("command.status", {"command_id": str(uuid4()), "recipient": "foreign-invocation"})):
        with pytest.raises(CommandRefused, match="^OBJECT_UNAVAILABLE$"):
            with adapter.response(connection, grant.grant_id, 1, method, payload):
                pytest.fail("foreign record disclosed")
    assert not store.is_cancelled(request.invocation_id)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM cancellation_commands").fetchone()[0] == 0


def test_real_owner_snapshot_and_change_coverage_are_authorized_each_time(tmp_path):
    store, command, request, authority, _, _, grant, connection = setup(tmp_path)
    adapter = AuthorizedInvocations(authority)
    selection = {"invocation_ids": [request.invocation_id]}
    with adapter.response(connection, grant.grant_id, 1, "invocation.snapshot", selection) as snapshot:
        assert snapshot["items"][0]["state"] == "EXECUTION_STATE_UNKNOWN"
    with adapter.response(connection, grant.grant_id, 1, "invocation.cancel", {"command": command}):
        pass
    params = {**selection, "generation": snapshot["generation"], "position": snapshot["position"]}
    with adapter.response(connection, grant.grant_id, 1, "invocation.changes", params) as change:
        assert change["from"] == snapshot["position"] and change["to"] > change["from"]
        assert change["changes"][0]["item"]["cancel_requested"]
    authority.revoke(grant.grant_id)
    with pytest.raises(AccessRefused):
        with adapter.response(connection, grant.grant_id, 1, "invocation.changes", params):
            pytest.fail("delivery continued after revocation")


def test_renderer_cannot_select_an_owner_path_or_principal(tmp_path):
    store, _, request, authority, _, _, grant, connection = setup(tmp_path)
    adapter = AuthorizedInvocations(authority)
    for extra in ({"principal": "administrator"}, {"workspace": "workspace-two"}, {"path": "foreign-store"}):
        with pytest.raises(CommandRefused, match="^COMMAND_INVALID$"):
            with adapter.response(connection, grant.grant_id, 1, "invocation.inspect",
                                  {"invocation_id": request.invocation_id, **extra}):
                pytest.fail("renderer selected authority")
    assert not store.is_cancelled(request.invocation_id)


def test_read_profile_cannot_cancel_a_real_invocation(tmp_path):
    store, command, request, authority, registry, _, grant, connection = setup(tmp_path)
    registry.registry.grants[grant.grant_id] = replace(grant, methods=("invocation.inspect",))
    adapter = AuthorizedInvocations(authority)
    with adapter.response(connection, grant.grant_id, 1, "invocation.inspect",
                          {"invocation_id": request.invocation_id}) as result:
        assert result["state"] == "EXECUTION_STATE_UNKNOWN"
    with pytest.raises(AccessRefused, match="^ACCESS_DENIED$"):
        with adapter.response(connection, grant.grant_id, 1, "invocation.cancel", {"command": command}):
            pytest.fail("read-only app reached cancellation")
    assert not store.is_cancelled(request.invocation_id)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM cancellation_commands").fetchone()[0] == 0
