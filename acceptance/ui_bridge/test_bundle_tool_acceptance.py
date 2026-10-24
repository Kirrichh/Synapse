"""Exercise staging exclusions and inventory identity without product fixtures."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("core_bundle_builder", ROOT / "scripts/ui/build_core_bundle.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_copy_preserves_runtime_source_without_distributing_acceptance_or_user_packages(tmp_path):
    source = tmp_path / "source"
    for module in ["runtime.py", "tests/probe.py", "test/probe.py", "site-packages/injection.py",
                   "__pycache__/runtime.pyc", "_testcapi.so", "test_runtime.py", "pkg/useful.py", "pkg/tests/probe.py",
                   "_ctypes_test.pyd", "_ctypes_test_d.pyd", "_ctypes_test.cpython-312-x86_64-linux-gnu.so",
                   "xxlimited.pyd", "xxlimited_35_d.pyd"]:
        path = source / module
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"runtime or untrusted code")
    destination = tmp_path / "staged"
    builder.copy_standard_library(source, destination)
    assert (destination / "runtime.py").read_bytes() == b"runtime or untrusted code"
    assert (destination / "pkg/useful.py").read_bytes() == b"runtime or untrusted code"
    # The runtime inventory must not authorize excluded executable bytes.
    record = builder.inventory(destination, "runtime.py", "v1")
    assert set(record["files"]) == {"runtime.py", "pkg/useful.py"}
    assert record["files"]["runtime.py"] == hashlib.sha256(b"runtime or untrusted code").hexdigest()
    (destination / "runtime.py").write_bytes(b"modified runtime")
    assert builder.inventory(destination, "runtime.py", "v1")["files"]["runtime.py"] != record["files"]["runtime.py"]


def test_build_never_overwrites_an_existing_bundle(tmp_path):
    marker = tmp_path / "existing.py"
    marker.write_text("owner data")
    with pytest.raises(ValueError, match="already exists"):
        builder.build(tmp_path)
    assert marker.read_text() == "owner data"
