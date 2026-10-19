"""Canonical administrative surface; OS evidence and decisions stay in core."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .authority import WorkspaceAccessAuthority
from .records import AccessRefused, METHODS, READ_METHODS


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="access_action", required=True)
    commands.add_parser("enroll", help="deliberately initialize protected installation access state")
    register = commands.add_parser("register", help="register one application's public Ed25519 key")
    register.add_argument("--public-key", required=True)
    grant = commands.add_parser("grant", help="delegate exact invocation-owner access")
    grant.add_argument("--application", required=True)
    grant.add_argument("--workspace", required=True)
    grant.add_argument("--invocation-store", required=True)
    grant.add_argument("--object", dest="objects", action="append", required=True)
    operations = grant.add_mutually_exclusive_group(required=True)
    operations.add_argument("--read-profile", action="store_true")
    operations.add_argument("--method", action="append", choices=sorted(METHODS))
    lifetime = grant.add_mutually_exclusive_group(required=True)
    lifetime.add_argument("--until-revoked", action="store_true")
    lifetime.add_argument("--expires-at", type=int)
    revoke = commands.add_parser("revoke", help="revoke a grant without cancelling accepted work")
    revoke.add_argument("--grant", required=True)


def run(args, stdout, stderr) -> int:
    from .windows_registry import WindowsAccessRegistry
    from .windows_invocations import WindowsInvocationResources

    try:
        registry, resources = WindowsAccessRegistry(), WindowsInvocationResources()
        authority = WorkspaceAccessAuthority(registry, resources)
        if args.access_action == "enroll":
            registry.enroll()
            result = {"state": "ENROLLED"}
        elif args.access_action == "register":
            result = asdict(authority.register(args.public_key))
        elif args.access_action == "grant":
            # Check administrative context before inspecting a requested path.
            registry.objects.administrator()
            resource = resources.objects.identify(Path(args.invocation_store))
            methods = tuple(sorted(READ_METHODS)) if args.read_profile else tuple(args.method)
            result = asdict(authority.issue(args.application, args.workspace, resource, methods,
                                           tuple(args.objects), expires_at=args.expires_at))
        elif args.access_action == "revoke":
            result = asdict(authority.revoke(args.grant))
        else:
            raise AccessRefused("ACCESS_INVALID")
        print(json.dumps(result, separators=(",", ":")), file=stdout)
        return 0
    except AccessRefused as error:
        print(error.code, file=stderr)
        return 1
