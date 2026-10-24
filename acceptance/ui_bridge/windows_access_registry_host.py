"""Positive OS-protected registry acceptance, requiring an elevated host.

All state belongs to an isolated synthetic installation outside the repository.
The ordinary-process companion verifies that readers cannot become writers.
No real application installation or workspace is enrolled by this harness.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse.access.authentication import AccessConnection
from synapse.access.authority import WorkspaceAccessAuthority
from synapse.access.windows_registry import WindowsAccessRegistry, installation_root
from synapse.access.windows_objects import WindowsObjects
from synapse.access.records import AccessRefused


def prepare(workspace: Path, report: Path):
    objects = WindowsObjects()
    objects.administrator()
    root = installation_root().with_name("SynapseAccessAcceptance-" + str(uuid4()))
    resource_root = workspace / ("access-resource-" + str(uuid4()))
    resource_root.mkdir()
    resource = objects.identify(resource_root)
    registry = WindowsAccessRegistry(root)
    registry.enroll()
    authority = WorkspaceAccessAuthority(registry, objects)
    private = Ed25519PrivateKey.generate()
    application = authority.register(private.public_key().public_bytes_raw().hex())
    grant = authority.issue(application.application_id, "synthetic-workspace", resource,
                            ("invocation.inspect",), ("synthetic-invocation",), expires_at=None)
    reopened = WindowsAccessRegistry(root)
    with reopened.transaction(administrative=False) as transaction:
        assert transaction.registry.applications[application.application_id] == application
        assert transaction.registry.grants[grant.grant_id] == grant
    connection = AccessConnection("synthetic-installation", "synthetic-pipe")
    authority.authenticate(connection, application.public_key, private.sign(connection.challenge()))
    with authority.authorize(connection, grant.grant_id, 1, "invocation.inspect", grant.objects) as (scope, _):
        assert scope.principal == application.application_id
    revoked = authority.revoke(grant.grant_id)
    assert revoked.revoked and revoked.version == 2
    with reopened.transaction(administrative=False) as transaction:
        assert transaction.registry.grants[grant.grant_id] == revoked
    try:
        with authority.authorize(connection, grant.grant_id, 1, "invocation.inspect", grant.objects):
            raise AssertionError("revocation did not stop access")
    except AccessRefused as error:
        assert error.code == "ACCESS_DENIED"
    # Private runtime coordinates stay only in the external host report. The
    # publication report must contain named outcomes, never these coordinates.
    report.write_text(json.dumps({"root": str(root), "resource": str(resource_root),
        "prepare": "PASS", "checks": ["protected-enrollment", "durable-registration", "durable-grant",
        "reopened-registry", "signed-connection", "authorized-resource", "durable-revocation",
        "revoked-connection-denied"]}, indent=2), encoding="utf-8")
    print("PASS protected registry enrollment, persistence, authentication and revocation")
    return root, resource_root


def ordinary(report: Path):
    value = json.loads(report.read_text(encoding="utf-8"))
    registry = WindowsAccessRegistry(Path(value["root"]))
    with registry.transaction(administrative=False) as transaction:
        assert len(transaction.registry.applications) == 1
        assert len(transaction.registry.grants) == 1
        assert next(iter(transaction.registry.grants.values())).revoked
        try:
            transaction.commit()
        except AccessRefused as error:
            assert error.code == "ADMIN_REQUIRED"
        else:
            raise AssertionError("read transaction acquired administrative rights")
    with ExitStack() as stack:
        try:
            stack.enter_context(registry.transaction(administrative=True))
        except AccessRefused as error:
            assert error.code == "ADMIN_REQUIRED"
        else:
            raise AssertionError("ordinary process acquired administrative context")
    original = (registry.root / "registry.json").read_bytes()
    try:
        with (registry.root / "registry.json").open("r+b") as stream:
            stream.write(b"invalid")
    except PermissionError:
        pass
    else:
        raise AssertionError("OS permitted ordinary registry modification")
    assert (registry.root / "registry.json").read_bytes() == original
    value["ordinary"] = "PASS"
    value["checks"] += ["ordinary-protected-read", "ordinary-admin-refused", "ordinary-OS-write-refused"]
    report.write_text(json.dumps(value, indent=2), encoding="utf-8")
    print("PASS ordinary read, administrative refusal and actual OS write refusal")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "ordinary"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--wait-for-reader", action="store_true")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[2]
    if args.workspace.resolve().is_relative_to(source) or args.report.resolve().is_relative_to(source):
        parser.error("runtime state and host report must be outside the checkout")
    if args.phase == "prepare":
        root, resource = prepare(args.workspace, args.report)
        if args.wait_for_reader:
            marker, deadline = args.report.with_suffix(".ordinary-ok"), monotonic() + 600
            while not marker.exists() and monotonic() < deadline:
                sleep(0.25)
            if not marker.exists():
                raise TimeoutError("ordinary-process verification has not completed")
            # Use only paths retained by this process, never paths reloaded from
            # the ordinary-writable report. Remove only the known test objects.
            for name in ("registry.json", "registry.lock"):
                (root / name).unlink()
            root.rmdir()
            resource.rmdir()
            print("PASS isolated protected registry cleaned up")
    else:
        ordinary(args.report)


if __name__ == "__main__":
    main()
