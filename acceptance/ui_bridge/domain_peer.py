"""Cross-language peer using production session/owner code and synthetic grants.

This is acceptance-only composition, not the installed canonical entry point.
It persists synthetic authorization so native restart/recovery can be observed.
"""
import argparse
from pathlib import Path
import os
import sys

from acceptance.ui_bridge.access_support import RegistryAdapter, Resources
from acceptance.ui_bridge.test_invocation_commands_acceptance import pending
from synapse.access.authority import WorkspaceAccessAuthority
from synapse.access.records import ResourceIdentity, METHODS
from synapse.access.registry_codec import encode_registry, decode_registry
from synapse.machine.codec import decode, encode, read_frame, write_frame
from synapse.machine.contract import PROFILE
from synapse.machine.domain_session import DomainSession
from synapse.machine.session import ControlSession


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--public-key', required=True)
    parser.add_argument('--lose-cancel-reply', action='store_true')
    parser.add_argument('--revoke', action='store_true')
    parser.add_argument('--read-only', action='store_true')
    parser.add_argument('--deny-status', action='store_true')
    args = parser.parse_args()
    if os.name == 'nt':
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    registry, resources = RegistryAdapter(), Resources()
    authority = WorkspaceAccessAuthority(registry, resources)
    state = args.root / 'synthetic-registry.json'
    if state.exists():
        registry.registry = decode_registry(state.read_bytes())
        resources.current = next(iter(registry.registry.grants.values())).resource
    else:
        _, store, _, invocation, _ = pending(args.root)
        resources.current = ResourceIdentity(str(store.root), 'synthetic-volume', 'synthetic-object')
        app = authority.register(args.public_key)
        methods = METHODS - ({'invocation.cancel'} if args.read_only else set())
        authority.issue(app.application_id, 'workspace-one', resources.current, tuple(sorted(methods)),
                        (invocation.invocation_id,), expires_at=None)
        state.write_bytes(encode_registry(registry.registry))
    if args.revoke:
        for grant in list(registry.registry.grants.values()):
            authority.revoke(grant.grant_id)
        state.write_bytes(encode_registry(registry.registry))
    control, domain = ControlSession(), DomainSession(authority)
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    def send(value):
        write_frame(destination, encode(value, PROFILE['max_frame_bytes']))
    announcement = control.announcement()
    announcement['component_version'] = 'acceptance-peer'
    send(announcement)
    while not control.closed:
        raw = read_frame(source, PROFILE['max_frame_bytes'])
        if raw is None:
            break
        request = decode(raw)
        if request.get('schema_version') == 'synapse.machine.domain/v1':
            with domain.response(request, ready=control.phase == 'ready') as reply:
                if args.deny_status and request.get('method') == 'command.status':
                    from synapse.machine.domain_contract import refusal
                    reply = refusal(request, 'ACCESS_DENIED')
                if args.lose_cancel_reply and request.get('method') == 'invocation.cancel' and reply['type'] == 'result':
                    return
                send(reply)
        else:
            reply = control.receive(request)
            if reply is not None:
                send(reply)
    domain.close()


if __name__ == '__main__':
    main()
