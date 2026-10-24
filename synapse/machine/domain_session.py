"""One authenticated application session on the canonical machine byte pipe."""
from contextlib import contextmanager, ExitStack

from synapse.access.records import AccessRefused
from synapse.commands.binding import CommandRefused
from synapse.commands.jcs import decode
from . import domain_contract as wire
from .contract import ProtocolError
from .invocations import AuthorizedInvocations
from .subscriptions import Subscriptions, DeliveryRefused


class DomainSession:
    def __init__(self, authority):
        self.authority = authority
        self.connection = None
        self.invocations = AuthorizedInvocations(authority)
        self.subscriptions = Subscriptions(self.invocations)

    @contextmanager
    def response(self, incoming: dict, *, ready: bool):
        request = wire.validate(incoming)
        if request["type"] != "request":
            raise ProtocolError("INVALID_SEQUENCE")
        if not ready:
            yield wire.refusal(request, "NOT_READY")
            return
        with ExitStack() as custody:
            try:
                method, params = request["method"], request["params"]
                if method == "access.close":
                    self.close()
                    self.connection = None
                    self.subscriptions = Subscriptions(self.invocations)
                    result = {"state": "CLOSED"}
                elif method == "access.open":
                    if self.connection is not None:
                        raise AccessRefused("ACCESS_INVALID")
                    self.connection = self.authority.open_connection()
                    result = {"profile": "synapse.application/v1", "challenge": decode(self.connection.challenge())}
                elif self.connection is None:
                    raise AccessRefused("AUTHENTICATION_FAILED")
                elif method == "access.authenticate":
                    app_id = self.authority.authenticate(self.connection, params["public_key"], bytes.fromhex(params["signature"]))
                    result = {"state": "AUTHENTICATED", "application_id": app_id}
                elif method == "access.grants":
                    grants = custody.enter_context(self.authority.visible_grants(self.connection))
                    result = {"grants": grants}
                elif method == "delivery.reset":
                    # Reset delivery resources only, including after renderer
                    # replacement. Owner commands and receipts are unaffected.
                    self.connection.application(self.authority)
                    self.subscriptions.close()
                    self.subscriptions = Subscriptions(self.invocations)
                    result = {"type": "delivery.reset"}
                elif method.startswith("delivery."):
                    result = custody.enter_context(self.subscriptions.response(self.connection, method, params,
                        request.get("grant_id"), request.get("grant_version")))
                else:
                    result = custody.enter_context(self.invocations.response(self.connection, request["grant_id"],
                                                   request["grant_version"], method, params))
                response = wire.reply(request, result)
            except (AccessRefused, CommandRefused, DeliveryRefused) as error:
                response = wire.refusal(request, error.code)
            # Transport failures and custody-release errors must not cause a
            # second response after a reply was already delivered.
            yield response

    def close(self):
        self.subscriptions.close()
        if self.connection is not None:
            self.connection.close()
