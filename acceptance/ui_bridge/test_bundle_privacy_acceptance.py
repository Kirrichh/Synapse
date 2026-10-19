"""Keep installer-specific local paths and their hashes out of shipped resources."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("private_bundle_builder", ROOT / "scripts/ui/build_core_bundle.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
CANARY = b"SYNTHETIC_BUILD_LOCATION"


def installed_packages(packages):
    files = {
        "sample/runtime.py": b"VALUE = 7\n",
        "sample/bin/runtime_data": b"required runtime data",
        "sample/direct_url.py": b"required runtime module",
        "sample-1.dist-info/direct_url.json": b'{"url":"file:///synthetic/SYNTHETIC_BUILD_LOCATION"}',
        "bin/sample.exe": CANARY,
        "Scripts/sample-script.py": CANARY,
    }
    for name, data in files.items():
        path = packages / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    record = packages / "sample-1.dist-info/RECORD"
    with record.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        for name, data in files.items():
            installed_name = "../../" + name if name.startswith(("bin/", "Scripts/")) else name
            writer.writerow([installed_name, "sha256=" + hashlib.sha256(data).hexdigest(), len(data)])
        writer.writerow(["sample-1.dist-info/RECORD", "", ""])
    return files


def assert_private_installation_data_absent(packages, originals):
    for name in ("bin/sample.exe", "Scripts/sample-script.py", "sample-1.dist-info/direct_url.json"):
        assert not (packages / name).exists()
    for name in ("sample/runtime.py", "sample/bin/runtime_data", "sample/direct_url.py"):
        assert (packages / name).read_bytes() == originals[name]
    record = packages / "sample-1.dist-info/RECORD"
    rows = list(csv.reader(record.read_text(encoding="utf-8").splitlines()))
    assert {row[0] for row in rows} == {
        "sample/runtime.py", "sample/bin/runtime_data", "sample/direct_url.py", "sample-1.dist-info/RECORD"
    }
    for path in packages.rglob("*"):
        if path.is_file():
            assert CANARY not in path.read_bytes()
            assert hashlib.sha256(CANARY).hexdigest().encode() not in path.read_bytes()


def test_pruning_removes_provenance_launchers_and_their_record_hashes(tmp_path):
    originals = installed_packages(tmp_path)
    builder.remove_installation_artifacts(tmp_path)
    assert_private_installation_data_absent(tmp_path, originals)
    builder.remove_installation_artifacts(tmp_path)
    assert_private_installation_data_absent(tmp_path, originals)


def test_build_prunes_installation_artifacts_before_binding_inventory(tmp_path, monkeypatch):
    base = tmp_path / "release-python"
    (base / "DLLs").mkdir(parents=True)
    (base / "Lib").mkdir()
    (base / "bin").mkdir()
    (base / "lib").mkdir(exist_ok=True)
    (base / "python.exe").write_bytes(b"synthetic interpreter")
    (base / "bin/python3").write_bytes(b"synthetic interpreter")
    (base / "Lib/runtime.py").write_bytes(b"standard library")
    monkeypatch.setattr(builder.sys, "base_prefix", str(base))
    monkeypatch.setattr(builder.sys, "_base_executable", str(base / "bin/python3"))
    monkeypatch.setattr(builder.sysconfig, "get_path", lambda _: str(base / "Lib"))
    monkeypatch.setattr(builder.platform, "machine", lambda: "AMD64")
    installed = {}

    def install_or_probe(command, **kwargs):
        if "--target" in command:
            packages = Path(command[command.index("--target") + 1])
            assert not packages.resolve().is_relative_to(destination.parent.resolve())
            installed["files"] = installed_packages(packages)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(builder.subprocess, "run", install_or_probe)
    monkeypatch.setattr(builder.subprocess, "check_output", lambda *args, **kwargs: "v1\n")
    destination = tmp_path / "publication" / "bundle"
    builder.build(destination)
    packages = next(destination.rglob("site-packages"))
    assert_private_installation_data_absent(packages, installed["files"])
    manifest = json.loads((destination / "bundle-manifest.json").read_text(encoding="utf-8"))
    assert any(name.endswith("sample/runtime.py") for name in manifest["files"])
    assert not any(name.endswith(("direct_url.json", "sample.exe", "sample-script.py")) for name in manifest["files"])


def test_build_refuses_private_staging_inside_source_checkout(tmp_path, monkeypatch):
    source = tmp_path / "source"
    scratch = source / "scratch"
    scratch.mkdir(parents=True)
    monkeypatch.setattr(builder, "ROOT", source)
    monkeypatch.setattr(builder.tempfile, "gettempdir", lambda: str(scratch))
    def forbidden_copy(*args, **kwargs):
        raise AssertionError("Build entered private staging inside the checkout")
    monkeypatch.setattr(builder, "copy_standard_library", forbidden_copy)
    destination = tmp_path / "bundle"
    with pytest.raises(ValueError, match="outside the source checkout"):
        builder.build(destination)
    assert not destination.exists()
    assert not list(scratch.iterdir())
