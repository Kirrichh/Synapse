"""The sole issuer and checker of operator-delegated application rights."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from time import time
from uuid import uuid4

from synapse.commands.binding import CommandScope
from .authentication import AccessConnection
from .ports import AccessRegistry, ResourceCustody
from .records import AccessRefused, Application, Grant, ResourceIdentity, identifier


class WorkspaceAccessAuthority:
    def __init__(self, registry: AccessRegistry, resources: ResourceCustody):
        self._registry, self._resources = registry, resources

    def open_connection(self) -> AccessConnection:
        with self._registry.transaction(administrative=False) as transaction:
            return AccessConnection(transaction.registry.installation_id, str(uuid4()))

    @contextmanager
    def visible_grants(self, connection: AccessConnection):
        app = connection.application(self)
        with self._registry.transaction(administrative=False) as transaction:
            current = transaction.registry.applications.get(app.application_id)
            if current != app or current.revoked:
                raise AccessRefused()
            now = time()
            yield [{"grant_id": grant.grant_id, "version": grant.version, "workspace": grant.workspace_id,
                    "methods": list(grant.methods), "objects": list(grant.objects), "expires_at": grant.expires_at}
                   for grant in transaction.registry.grants.values()
                   if grant.application_id == app.application_id and not grant.revoked
                   and (grant.expires_at is None or now < grant.expires_at)]

    def register(self, public_key: str) -> Application:
        with self._registry.transaction(administrative=True) as transaction:
            if transaction.administrator is None:
                raise AccessRefused("ADMIN_REQUIRED")
            app = Application(str(uuid4()), public_key, transaction.administrator)
            if any(old.public_key == public_key for old in transaction.registry.applications.values()):
                raise AccessRefused("ACCESS_INVALID")
            if len(transaction.registry.applications) >= 128:
                raise AccessRefused("ACCESS_UNAVAILABLE")
            transaction.registry.applications[app.application_id] = app
            transaction.commit()
            return app

    def issue(self, application_id: str, workspace_id: str, resource: ResourceIdentity,
              methods: tuple[str, ...], objects: tuple[str, ...], *, expires_at: int | None) -> Grant:
        with self._registry.transaction(administrative=True) as transaction:
            if transaction.administrator is None:
                raise AccessRefused("ADMIN_REQUIRED")
            app = transaction.registry.applications.get(application_id)
            if app is None or app.revoked:
                raise AccessRefused()
            grant = Grant(str(uuid4()), 1, application_id, workspace_id, resource,
                          methods, objects, expires_at, transaction.administrator)
            if expires_at is not None and expires_at <= time():
                raise AccessRefused("ACCESS_INVALID")
            if len(transaction.registry.grants) >= 128:
                raise AccessRefused("ACCESS_UNAVAILABLE")
            # Existing workspace IDs cannot silently acquire a different root.
            if any(old.workspace_id == workspace_id and old.resource != resource
                   for old in transaction.registry.grants.values()):
                raise AccessRefused("RESOURCE_CHANGED")
            with self._resources.hold(resource):
                transaction.registry.grants[grant.grant_id] = grant
                transaction.commit()
            return grant

    def revoke(self, grant_id: str) -> Grant:
        identifier(grant_id)
        with self._registry.transaction(administrative=True) as transaction:
            if transaction.administrator is None:
                raise AccessRefused("ADMIN_REQUIRED")
            grant = transaction.registry.grants.get(grant_id)
            if grant is None:
                raise AccessRefused()
            if not grant.revoked:
                grant = replace(grant, version=grant.version + 1, revoked=True)
                transaction.registry.grants[grant_id] = grant
                transaction.commit()
            return grant

    def authenticate(self, connection: AccessConnection, public_key: str, signature: bytes) -> str:
        with self._registry.transaction(administrative=False) as transaction:
            matches = [app for app in transaction.registry.applications.values() if app.public_key == public_key]
            if len(matches) != 1:
                connection.close()
                raise AccessRefused("AUTHENTICATION_FAILED")
            app = matches[0]
            connection.authenticate(self, app, signature)
            return app.application_id

    @contextmanager
    def authorize(self, connection: AccessConnection, grant_id: str, version: int,
                  method: str, objects: tuple[str, ...]):
        """Hold authorization through acceptance/read/delivery, never execution.

        The domain adapter must verify each object's actual retained membership
        under this resource. Grant object IDs alone are not evidence it exists.
        """
        app = connection.application(self)
        identifier(grant_id)
        if (type(version) is not int or type(objects) is not tuple or not objects
                or len(objects) > 32):
            raise AccessRefused("ACCESS_INVALID")
        for value in objects:
            identifier(value)
        if len(set(objects)) != len(objects):
            raise AccessRefused("ACCESS_INVALID")
        with self._registry.transaction(administrative=False) as transaction:
            current = transaction.registry.applications.get(app.application_id)
            grant = transaction.registry.grants.get(grant_id)
            if current != app or current.revoked:
                raise AccessRefused()
            if (grant is None or grant.revoked or grant.version != version
                    or grant.application_id != app.application_id or method not in grant.methods
                    or any(value not in grant.objects for value in objects)
                    or (grant.expires_at is not None and time() >= grant.expires_at)):
                raise AccessRefused()
            with self._resources.hold(grant.resource):
                yield CommandScope(app.application_id, grant.workspace_id), grant.resource
