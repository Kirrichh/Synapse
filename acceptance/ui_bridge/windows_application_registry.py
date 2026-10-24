"""Host coordination for a synthetic canonical installation-access acceptance.

The elevated guardian refuses any existing default registry. It enrolls only a
new temporary installation, grants only the ordinary process's synthetic owner,
and retains cleanup authority/coordinates in privileged memory. No path or
command from an ordinary-writable coordination file is executed.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
from time import monotonic, sleep

from acceptance.ui_bridge.test_invocation_commands_acceptance import pending
from synapse.access.windows_registry import WindowsAccessRegistry, installation_root
from synapse.access.records import METHODS, AccessRefused


def wait(path, seconds=900):
    deadline = monotonic() + seconds
    if (path.parent / "abort").exists():
        raise RuntimeError("host acceptance aborted")
    while not path.exists():
        if (path.parent / "abort").exists():
            raise RuntimeError("host acceptance aborted")
        if monotonic() >= deadline:
            raise TimeoutError("host coordination deadline")
        sleep(0.1)


def administer(run, installed):
    registry = WindowsAccessRegistry()
    registry.objects.administrator()
    if registry.root.exists():
        raise RuntimeError("existing installation access state must not be changed")
    wait(run / "registration.json")
    key = json.loads((run / "registration.json").read_text())["public_key"]
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("invalid public registration key")
    wait(run / "owner-prepared")
    resource = run / "synthetic-owner" / "evidence"
    python = installed / "core" / "python.exe"
    def cli(*arguments):
        result = subprocess.run([str(python), "-I", "-B", "-m", "synapse", "access", *arguments],
                                cwd=run, capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise RuntimeError("canonical administrative command refused")
        return json.loads(result.stdout)
    assert cli("enroll") == {"state":"ENROLLED"}
    # From here this process owns only the newly enrolled registry. Never load
    # privileged cleanup targets from the ordinary-writable report.
    try:
        application = cli("register", "--public-key", key)
        args = ["grant", "--application", application["application_id"], "--workspace", "synthetic-installed-workspace",
                "--invocation-store", str(resource), "--object", "invocation", "--until-revoked"]
        for method in sorted(METHODS):
            args += ["--method", method]
        grant = cli(*args)
        (run / "registered.json").write_text(json.dumps({"grant_id":grant["grant_id"],
            "invocation_id":"invocation", "workspace":grant["workspace_id"]}), encoding="utf-8")
        print("PASS installed canonical enrollment, registration and exact synthetic grant", flush=True)
        wait(run / "reader-held")
        (run / "revoke-started").touch()
        revoked = cli("revoke", "--grant", grant["grant_id"])
        assert revoked["revoked"] and revoked["version"] == 2
        (run / "revoked").touch()
        print("PASS canonical cross-process revocation", flush=True)
        wait(run / "done")
    finally:
        for name in ("registry.json", "registry.lock"):
            (registry.root / name).unlink()
        registry.root.rmdir()
        (run / "cleaned").touch()
        print("PASS temporary canonical registry cleaned", flush=True)


def reader(run):
    registry = WindowsAccessRegistry()
    with registry.transaction(administrative=False):
        (run / "reader-held").touch()
        wait(run / "revoke-started", 30)
        sleep(0.3)
        assert not (run / "revoked").exists(), "administrator bypassed active read custody"
    wait(run / "revoked", 30)
    (run / "reader-pass").touch()
    print("PASS real cross-process shared/exclusive custody", flush=True)


def prepare(run):
    # The administrator grants access; it must not create the application's
    # private owner store under an elevated identity with administrator-only ACLs.
    try:
        WindowsAccessRegistry().objects.administrator()
    except AccessRefused as error:
        assert error.code == "ADMIN_REQUIRED"
    else:
        raise RuntimeError("synthetic owner preparation requires an ordinary process")
    resource = run / "synthetic-owner"
    resource.mkdir()
    _, _, _, invocation, _ = pending(resource)
    assert invocation.invocation_id == "invocation"
    (run / "owner-prepared").touch()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("administer", "reader", "prepare"))
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--installed", type=Path)
    args = parser.parse_args()
    args.run = args.run.resolve()
    if args.run.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        parser.error("coordination and synthetic state must remain outside the checkout")
    if args.phase == "administer":
        administer(args.run, args.installed)
    elif args.phase == "reader":
        reader(args.run)
    else:
        prepare(args.run)
