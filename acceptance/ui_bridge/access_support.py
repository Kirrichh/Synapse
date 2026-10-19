"""Synthetic access adapters confined to acceptance, never bundled with core."""
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse.access.authentication import AccessConnection
from synapse.access.authority import WorkspaceAccessAuthority
from synapse.access.ports import Registry
from synapse.access.records import AccessRefused, ResourceIdentity


RESOURCE = ResourceIdentity("synthetic-workspace", "volume", "file-one")


class RegistryAdapter:
    def __init__(self):
        self.lock, self.registry = RLock(), Registry()
        self.administrative = True
        self.commits = 0

    @contextmanager
    def transaction(self, *, administrative):
        with self.lock:
            if administrative and not self.administrative:
                raise AccessRefused("ADMIN_REQUIRED")
            owner = self

            class Transaction:
                registry = deepcopy(owner.registry)
                administrator = "synthetic-admin" if administrative else None

                def commit(self):
                    assert self.administrator is not None
                    owner.registry = deepcopy(self.registry)
                    owner.commits += 1

            yield Transaction()


class Resources:
    current = RESOURCE
    active = 0

    @contextmanager
    def hold(self, resource):
        if resource != self.current:
            raise AccessRefused("RESOURCE_CHANGED")
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1


def authorized(methods=("invocation.inspect", "invocation.cancel", "command.status"), *, resource=RESOURCE,
               objects=("invocation-one",)):
    registry, resources = RegistryAdapter(), Resources()
    resources.current = resource
    authority = WorkspaceAccessAuthority(registry, resources)
    private = Ed25519PrivateKey.generate()
    app = authority.register(private.public_key().public_bytes_raw().hex())
    grant = authority.issue(app.application_id, "workspace-one", resource, methods,
                            objects, expires_at=None)
    connection = AccessConnection("installation-one", "pipe-one")
    authority.authenticate(connection, app.public_key, private.sign(connection.challenge()))
    return authority, registry, resources, app, grant, connection, private
