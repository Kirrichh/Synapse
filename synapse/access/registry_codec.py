"""Bounded persistent access records; malformed state never means absence."""
from dataclasses import asdict, fields
from uuid import UUID

from synapse.commands.jcs import InvalidCommandValue, canonical_bytes, decode
from .ports import Registry
from .records import AccessRefused, Application, Grant, ResourceIdentity


def encode_registry(registry: Registry) -> bytes:
    if len(registry.applications) > 128 or len(registry.grants) > 128:
        raise AccessRefused("ACCESS_UNAVAILABLE")
    value = {"schema_version": "synapse.access.registry/v1",
             "installation_id": registry.installation_id,
             "applications": [asdict(app) for app in registry.applications.values()],
             "grants": [{**asdict(grant), "methods": list(grant.methods), "objects": list(grant.objects)}
                        for grant in registry.grants.values()]}
    try:
        return canonical_bytes(value)
    except InvalidCommandValue as exc:
        raise AccessRefused("ACCESS_UNAVAILABLE") from exc


def decode_registry(raw: bytes) -> Registry:
    try:
        value = decode(raw)
        if (type(value) is not dict or set(value) != {"schema_version", "installation_id", "applications", "grants"}
                or value["schema_version"] != "synapse.access.registry/v1"):
            raise ValueError()
        if type(value["installation_id"]) is not str or str(UUID(value["installation_id"])) != value["installation_id"]:
            raise ValueError()
        if any(type(value[key]) is not list or len(value[key]) > 128 for key in ("applications", "grants")):
            raise ValueError()
        apps, grants = {}, {}
        for item in value["applications"]:
            if type(item) is not dict or set(item) != {field.name for field in fields(Application)}:
                raise ValueError()
            app = Application(**item)
            if app.application_id in apps or any(old.public_key == app.public_key for old in apps.values()):
                raise ValueError()
            apps[app.application_id] = app
        for item in value["grants"]:
            if type(item) is not dict or set(item) != {field.name for field in fields(Grant)}:
                raise ValueError()
            if type(item["methods"]) is not list or type(item["objects"]) is not list:
                raise ValueError()
            grant = Grant(**{**item, "resource": ResourceIdentity(**item["resource"]),
                             "methods": tuple(item["methods"]), "objects": tuple(item["objects"])})
            if grant.grant_id in grants or grant.application_id not in apps:
                raise ValueError()
            grants[grant.grant_id] = grant
        return Registry(apps, grants, value["installation_id"])
    except (ValueError, TypeError, KeyError) as exc:
        raise AccessRefused("ACCESS_UNAVAILABLE") from exc
