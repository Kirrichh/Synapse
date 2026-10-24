"""Single-use application proof bound to one trusted byte connection."""
from __future__ import annotations

import secrets
from time import monotonic

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from synapse.commands.jcs import canonical_bytes
from .records import AccessRefused, Application


class AccessConnection:
    """Owned by the core transport. Never constructed from renderer fields.

    A new instance is required after pipe loss. The signed, random challenge
    includes the installation and connection identity. A failed proof consumes
    it too, so retry requires a new connection.
    """
    def __init__(self, installation: str, connection_id: str):
        self._challenge = canonical_bytes({"schema_version": "synapse.access.challenge/v1",
            "installation": installation, "connection": connection_id,
            "nonce": secrets.token_hex(32)})
        self._deadline = monotonic() + 60
        self._consumed = False
        self._application: Application | None = None
        self._authority: object | None = None
        self._closed = False

    def challenge(self) -> bytes:
        if self._closed or self._consumed or monotonic() >= self._deadline:
            raise AccessRefused("AUTHENTICATION_FAILED")
        return self._challenge

    def authenticate(self, authority: object, application: Application, signature: bytes) -> None:
        challenge = self.challenge()
        self._consumed = True
        if application.revoked or type(signature) is not bytes or len(signature) != 64:
            raise AccessRefused("AUTHENTICATION_FAILED")
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(application.public_key)).verify(signature, challenge)
        except (InvalidSignature, ValueError) as exc:
            raise AccessRefused("AUTHENTICATION_FAILED") from exc
        self._application, self._authority = application, authority

    def application(self, authority: object) -> Application:
        if self._closed or self._authority is not authority or self._application is None:
            raise AccessRefused("AUTHENTICATION_FAILED")
        return self._application

    def close(self) -> None:
        self._closed = True
        self._application = None
        self._authority = None
