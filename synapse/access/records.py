"""Closed access records; identifiers and scopes are never credentials."""
from __future__ import annotations

from dataclasses import dataclass
import re


class AccessRefused(ValueError):
    CODES = frozenset({"ACCESS_DENIED", "ACCESS_INVALID", "ACCESS_UNAVAILABLE",
                       "ADMIN_REQUIRED", "RESOURCE_CHANGED", "AUTHENTICATION_FAILED"})

    def __init__(self, code="ACCESS_DENIED"):
        self.code = code if code in self.CODES else "ACCESS_DENIED"
        super().__init__(self.code)


# Expand a preset at issuance. Neither a wildcard nor future methods inherit it.
READ_METHODS = frozenset({"invocation.inspect", "invocation.snapshot",
                          "invocation.changes", "command.status"})
METHODS = READ_METHODS | {"invocation.cancel"}


def identifier(value: str) -> str:
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,191}", value) is None:
        raise AccessRefused("ACCESS_INVALID")
    return value


@dataclass(frozen=True)
class ResourceIdentity:
    """The platform's opened-object identity, never a path-only permission."""
    path: str
    volume: str
    file_id: str

    def __post_init__(self):
        if any(type(value) is not str or not 1 <= len(value) <= 4096
               for value in (self.path, self.volume, self.file_id)):
            raise AccessRefused("ACCESS_INVALID")


@dataclass(frozen=True)
class Application:
    application_id: str
    public_key: str
    administrator: str
    revoked: bool = False

    def __post_init__(self):
        identifier(self.application_id)
        identifier(self.administrator)
        if (type(self.public_key) is not str or re.fullmatch("[0-9a-f]{64}", self.public_key) is None
                or type(self.revoked) is not bool):
            raise AccessRefused("ACCESS_INVALID")


@dataclass(frozen=True)
class Grant:
    grant_id: str
    version: int
    application_id: str
    workspace_id: str
    resource: ResourceIdentity
    methods: tuple[str, ...]
    objects: tuple[str, ...]
    expires_at: int | None
    administrator: str
    revoked: bool = False

    def __post_init__(self):
        for value in (self.grant_id, self.application_id, self.workspace_id, self.administrator):
            identifier(value)
        if type(self.version) is not int or not 1 <= self.version <= 9007199254740991:
            raise AccessRefused("ACCESS_INVALID")
        if type(self.resource) is not ResourceIdentity or type(self.revoked) is not bool:
            raise AccessRefused("ACCESS_INVALID")
        if (type(self.methods) is not tuple or not self.methods
                or any(type(method) is not str or method not in METHODS for method in self.methods)
                or len(set(self.methods)) != len(self.methods)):
            raise AccessRefused("ACCESS_INVALID")
        if type(self.objects) is not tuple or not 1 <= len(self.objects) <= 128:
            raise AccessRefused("ACCESS_INVALID")
        for value in self.objects:
            identifier(value)
        if len(set(self.objects)) != len(self.objects):
            raise AccessRefused("ACCESS_INVALID")
        if self.expires_at is not None and (type(self.expires_at) is not int
                                           or not 0 < self.expires_at <= 9007199254740991):
            raise AccessRefused("ACCESS_INVALID")
