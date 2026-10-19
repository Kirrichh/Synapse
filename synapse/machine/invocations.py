"""Translate authorized application requests to the retained invocation owner.

The registered resource is this workspace's invocation evidence store. No path,
principal or workspace from the renderer selects an owner. Reading and command
reconciliation never assemble an execution world or load an agent plugin.
"""
from contextlib import contextmanager
from pathlib import Path

from synapse.access.authority import WorkspaceAccessAuthority
from synapse.access.authentication import AccessConnection
from synapse.agents.retention import InvocationStore
from synapse.agents.invocation_commands import InvocationCommands
from synapse.agents.invocation_observation import InvocationObservation
from synapse.commands.binding import CommandRefused


class AuthorizedInvocations:
    def __init__(self, authority: WorkspaceAccessAuthority):
        self.authority = authority

    @contextmanager
    def observation(self, connection, grant_id, version, method, objects):
        """Current access and real membership held through observation delivery."""
        with self.authority.authorize(connection, grant_id, version, method, objects) as (scope, resource):
            store = InvocationStore.open_existing(Path(resource.path))
            store.require_membership(objects)
            yield scope, InvocationObservation(store)

    @contextmanager
    def response(self, connection: AccessConnection, grant_id: str, version: int,
                 method: str, params: dict):
        """Keep current authorization until the bounded reply has been written."""
        if type(params) is not dict:
            raise CommandRefused("COMMAND_INVALID")
        fields = {"invocation.inspect": {"invocation_id"},
                  "invocation.snapshot": {"invocation_ids"},
                  "invocation.changes": {"invocation_ids", "generation", "position"},
                  "command.status": {"command_id", "recipient"},
                  "invocation.cancel": {"command"}}
        if method not in fields or set(params) != fields[method]:
            raise CommandRefused("COMMAND_INVALID")
        if method == "invocation.cancel":
            command = params["command"]
            if type(command) is not dict or type(command.get("recipient")) is not str:
                raise CommandRefused("COMMAND_INVALID")
            objects = (command["recipient"],)
        elif method == "command.status":
            objects = (params["recipient"],)
        elif method == "invocation.inspect":
            objects = (params["invocation_id"],)
        else:
            if type(params["invocation_ids"]) is not list:
                raise CommandRefused("COMMAND_INVALID")
            objects = tuple(params["invocation_ids"])
        with self.authority.authorize(connection, grant_id, version, method, objects) as (scope, resource):
            root = Path(resource.path)
            store = InvocationStore.open_existing(root)
            store.require_membership(objects)
            if method == "invocation.cancel":
                result = InvocationCommands(InvocationStore.open_commands(root)).cancel(scope, command)
            elif method == "command.status":
                result = InvocationCommands(store).status(scope, params["command_id"], params["recipient"])
            else:
                observation = InvocationObservation(store)
                if method == "invocation.inspect":
                    result = observation.inspect(objects[0])
                elif method == "invocation.snapshot":
                    result = observation.snapshot(objects)
                else:
                    result = observation.after(params["generation"], params["position"], objects)
            yield result
