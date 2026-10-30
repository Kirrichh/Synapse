"""Optional native Stage 4 kernel parity and runtime-identity contracts."""
from __future__ import annotations

import hashlib
from importlib.machinery import EXTENSION_SUFFIXES
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unicodedata

import pytest

from synapse.experiments.gold import canonicalization as canon
from synapse.experiments.gold.run_inputs import runtime_source_digest


def _python(value):
    return canon._encode_stage4_data_only_python(value)


def _actual(value):
    return canon._encode_stage4_data_only(value)


def _refusal(function, value):
    with pytest.raises(canon.CanonicalizationViolation) as exc:
        function(value)
    return exc.value.failure_code, exc.value.detail


@pytest.mark.parametrize("value", [
    None, True, False, 0, -2**53 + 1, 2**53 - 1,
    0.0, -0.0, 1.0, 1e-6, 1e-7, 1e20, 1e21,
    1.1880909976517927e-05, 5e-324, 2.2250738585072014e-308,
    "", "e\u0301", "é", "\u043f\u0430\u043c\u044f\u0442\u044c \U0001f600", "a\x00b\n\"\\",
    {"z": [1, True, None], "a": {"e\u0301": -0.0}},
])
def test_native_and_python_accepted_bytes_are_identical(value):
    assert _actual(value) == _python(value)


@pytest.mark.parametrize("value", [
    2**53, -2**53 - 1, float("nan"), float("inf"), float("-inf"),
    "\ud800", {"\udfff": 1}, {"key": "\ud800"},
    {"e\u0301": 1, "é": 2}, {1: "non-string"},
    bytearray(b"x"), {"not", "a", "list"},
])
def test_native_and_python_refusals_are_identical(value):
    assert _refusal(_actual, value) == _refusal(_python, value)


def test_native_depth_cycles_and_shared_ownership_match_python():
    cyclic = []
    cyclic.append(cyclic)
    assert _refusal(_actual, cyclic) == _refusal(_python, cyclic)

    shared = [1, 2]
    assert _actual([shared, shared]) == _python([shared, shared])

    for depth in (128, 129):
        value = 0
        for _ in range(depth):
            value = [value]
        if depth == 128:
            assert _actual(value) == _python(value)
        else:
            assert _refusal(_actual, value) == _refusal(_python, value)


def test_loaded_native_binary_is_part_of_frozen_runtime_identity(monkeypatch):
    native = pytest.importorskip("_stage4_native")
    identity = canon.native_runtime_identity()
    if unicodedata.unidata_version != "15.0.0":
        assert canon._native is None
        assert identity == {"implementation": "python"}
        assert _actual({"e\u0301": [1, True]}) == _python({"e\u0301": [1, True]})
        return
    assert identity["implementation"] == "rust-pyo3"
    imported = Path(native.__file__)
    if imported.name == "__init__.py":
        binaries = [path for path in imported.parent.iterdir()
                    if any(path.name == "_stage4_native" + suffix for suffix in EXTENSION_SUFFIXES)]
        assert len(binaries) == 1
        binary = binaries[0]
        assert identity["wrapper_sha256"] == hashlib.sha256(imported.read_bytes()).hexdigest()
    else:
        binary = imported
    assert identity["binary_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    native_digest = runtime_source_digest()
    monkeypatch.setattr(canon, "_native", None)
    assert canon.native_runtime_identity() == {"implementation": "python"}
    assert runtime_source_digest() != native_digest


def copied_native(tmp_path, *, layout="wheel"):
    """Copy the installed extension, never modify its live installation."""
    native = pytest.importorskip("_stage4_native")
    if unicodedata.unidata_version != "15.0.0":
        pytest.skip("native identity requires the compatible Unicode runtime")
    imported = Path(native.__file__)
    if imported.name == "__init__.py":
        binaries = [path for path in imported.parent.iterdir()
                    if any(path.name == "_stage4_native" + suffix for suffix in EXTENSION_SUFFIXES)]
        assert len(binaries) == 1
        original = binaries[0]
    else:
        original = imported
    root = tmp_path / "native-copy"
    root.mkdir()
    wrapper = None
    if layout == "wheel":
        if imported.name != "__init__.py":
            pytest.skip("the wheel packaging contract needs an installed wheel")
        package = root / "_stage4_native"
        package.mkdir()
        wrapper = package / "__init__.py"
        shutil.copyfile(imported, wrapper)
        binary = package / original.name
    else:
        binary = root / original.name
    shutil.copyfile(original, binary)
    return root, binary, wrapper


def native_environment(root):
    return {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None,
        (str(root), os.environ.get("PYTHONPATH")))), "PYTHONDONTWRITEBYTECODE": "1"}


def native_probe(root):
    result = subprocess.run([sys.executable, "-B", "-c", """
import json
from synapse.experiments.gold import canonicalization as canon
from synapse.experiments.gold.run_inputs import runtime_source_digest
print(json.dumps({"identity": canon.native_runtime_identity(),
                  "runtime": runtime_source_digest(),
                  "encoded": canon._native.encode({"x": 1}).decode()}))
"""], cwd=Path(__file__).resolve().parents[1], env=native_environment(root),
        capture_output=True, text=True, check=True, timeout=30)
    return json.loads(result.stdout)


@pytest.mark.parametrize("layout", ["wheel", "direct"])
def test_binary_drift_changes_identity_in_both_supported_layouts(tmp_path, layout):
    root, binary, wrapper = copied_native(tmp_path, layout=layout)
    before = native_probe(root)
    assert before["identity"]["binary_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    wrapper_bytes = None if wrapper is None else wrapper.read_bytes()
    binary.write_bytes(binary.read_bytes() + b"\0native identity acceptance\0")
    after = native_probe(root)
    assert before["encoded"] == after["encoded"] == '{"x":1}'
    assert before["identity"]["binary_sha256"] != after["identity"]["binary_sha256"]
    assert before["runtime"] != after["runtime"]
    if wrapper is not None:
        assert wrapper.read_bytes() == wrapper_bytes


def test_wheel_wrapper_drift_changes_frozen_runtime_identity(tmp_path):
    root, binary, wrapper = copied_native(tmp_path)
    before = native_probe(root)
    wrapper.write_bytes(wrapper.read_bytes() + b"\n# Native identity acceptance.\n")
    after = native_probe(root)
    assert before["identity"]["binary_sha256"] == after["identity"]["binary_sha256"]
    assert before["identity"]["wrapper_sha256"] != after["identity"]["wrapper_sha256"]
    assert before["runtime"] != after["runtime"]
    assert before["encoded"] == after["encoded"] == '{"x":1}'


def test_unidentifiable_native_callable_is_refused(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(canon, "_native", SimpleNamespace(encode=lambda value: b"null"))
    with pytest.raises(ValueError, match="loaded extension"):
        canon.native_runtime_identity()


def test_native_identity_refuses_conflicting_binary_locations(monkeypatch, tmp_path):
    pytest.importorskip("_stage4_native")
    if canon._native is None:
        pytest.skip("native identity requires the compatible Unicode runtime")
    extension = canon._native.encode.__self__
    replacement = tmp_path / "other-binary.so"
    replacement.write_bytes(b"another binary")
    monkeypatch.setattr(extension, "__file__", str(replacement))
    with pytest.raises(ValueError, match="loaded extension"):
        canon.native_runtime_identity()
