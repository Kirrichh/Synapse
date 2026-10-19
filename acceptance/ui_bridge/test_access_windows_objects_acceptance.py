"""Actual Windows file custody and ordinary-process administrative refusal."""
from dataclasses import replace
import os

import pytest

from synapse.access.records import AccessRefused
from synapse.access.windows_objects import WindowsObjects
from synapse.access.windows_registry import WindowsAccessRegistry
from synapse.access.windows_invocations import WindowsInvocationResources


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows security objects")


def test_open_resource_is_pinned_and_replacement_requires_a_new_identity(tmp_path):
    objects = WindowsObjects()
    root = tmp_path / "workspace"
    root.mkdir()
    identity = objects.identify(root)
    with objects.hold(identity):
        with pytest.raises(OSError):
            root.rename(tmp_path / "moved")
    root.rename(tmp_path / "old")
    root.mkdir()
    with pytest.raises(AccessRefused, match="^RESOURCE_CHANGED$"):
        with objects.hold(identity):
            pytest.fail("replaced path was authorized")
    with objects.hold(objects.identify(root)):
        pass
    with pytest.raises(AccessRefused):
        with objects.hold(replace(identity, volume="other-volume")):
            pytest.fail("foreign volume authorized")


def test_unprotected_registry_is_not_accepted_or_initialized(tmp_path):
    root = tmp_path / "registry"
    root.mkdir()
    registry = WindowsAccessRegistry(root)
    with pytest.raises(AccessRefused, match="^ACCESS_UNAVAILABLE$"):
        with registry.transaction(administrative=False):
            pytest.fail("ordinary writable directory became an authority")
    assert list(root.iterdir()) == []


def test_ordinary_process_cannot_enroll_or_issue_admin_transactions(tmp_path):
    objects = WindowsObjects()
    try:
        objects.administrator()
    except AccessRefused as error:
        assert error.code == "ADMIN_REQUIRED"
    else:
        pytest.fail("this negative acceptance must run as an ordinary process")
    registry = WindowsAccessRegistry(tmp_path / "registry")
    with pytest.raises(AccessRefused, match="^ADMIN_REQUIRED$"):
        registry.enroll()
    with pytest.raises(AccessRefused, match="^ADMIN_REQUIRED$"):
        with registry.transaction(administrative=True):
            pytest.fail("ordinary process obtained administrative context")
    assert not registry.root.exists()


def test_actual_owner_transaction_works_under_database_custody(tmp_path):
    from acceptance.ui_bridge.access_support import authorized
    from acceptance.ui_bridge.test_invocation_commands_acceptance import pending
    from synapse.access.authority import WorkspaceAccessAuthority
    from synapse.access.authentication import AccessConnection
    from synapse.machine.invocations import AuthorizedInvocations

    _, store, command, request, _ = pending(tmp_path)
    resources = WindowsInvocationResources()
    resource = resources.objects.identify(store.root)
    _, registry, _, app, grant, _, private = authorized(resource=resource, objects=(request.invocation_id,))
    authority = WorkspaceAccessAuthority(registry, resources)
    connection = AccessConnection("synthetic-installation", "synthetic-pipe")
    authority.authenticate(connection, app.public_key, private.sign(connection.challenge()))
    with AuthorizedInvocations(authority).response(connection, grant.grant_id, 1, "invocation.cancel", {"command": command}) as reply:
        assert reply["outcome"] == "CANCEL_REQUESTED"
        with pytest.raises(OSError):
            store.database.rename(store.root / "replacement.sqlite3")
    assert store.is_cancelled(request.invocation_id)
