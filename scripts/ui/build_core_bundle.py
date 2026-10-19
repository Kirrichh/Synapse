"""Build a self-contained CPython resource that preserves python -m synapse.

Run with the release Python for the target OS. This is build tooling, not another
runtime entry point. The application starts the copied interpreter's canonical
module entry and does not depend on a user-installed Python.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = {"test", "tests", "__pycache__", "site-packages", "ensurepip", "idlelib", "turtledemo"}
# CPython's layout also classifies these non-_test-prefixed extensions as tests.
TEST_EXTENSIONS = {"_ctypes_test", "xxlimited", "xxlimited_35"}


def copy_standard_library(source: Path, destination: Path) -> None:
    def ignore(_directory, names):
        return [name for name in names if name in EXCLUDED or name.startswith(("_test", "test_"))
                or name.split(".", 1)[0].removesuffix("_d") in TEST_EXTENSIONS]
    shutil.copytree(source, destination, ignore=ignore, symlinks=False)


def remove_installation_artifacts(packages: Path) -> None:
    """Exclude installer provenance and launchers bound to the build interpreter."""
    for directory in (packages / "bin", packages / "Scripts"):
        if directory.exists():
            shutil.rmtree(directory)
    for metadata in packages.glob("*.dist-info"):
        provenance = metadata / "direct_url.json"
        provenance.unlink(missing_ok=True)
        record = metadata / "RECORD"
        if not record.exists():
            continue
        with record.open(encoding="utf-8", newline="") as source:
            rows = list(csv.reader(source))
        removed_metadata = f"{metadata.name}/direct_url.json"
        with record.open("w", encoding="utf-8", newline="") as destination:
            writer = csv.writer(destination)
            for row in rows:
                if row and (row[0] == removed_metadata or row[0].startswith(
                    ("../../bin/", "../../Scripts/", "bin/", "Scripts/")
                )):
                    continue
                writer.writerow(row)


def inventory(root: Path, python: str, runtime_version: str) -> dict:
    target_os = {"Linux": "linux", "Windows": "windows"}.get(platform.system())
    architecture = {"x86_64": "x86_64", "AMD64": "x86_64", "aarch64": "aarch64", "ARM64": "aarch64"}.get(platform.machine())
    if target_os is None or architecture is None:
        raise ValueError("Unsupported desktop bundle target")
    return {"format": 1, "target_os": target_os, "architecture": architecture,
        "runtime_version": runtime_version, "python": python,
        "files": {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in sorted(root.rglob("*")) if path.is_file()}}


def build(destination: Path) -> None:
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("Destination already exists; choose a new staging directory")
    if Path(tempfile.gettempdir()).resolve().is_relative_to(ROOT):
        raise ValueError("Build temporary directory must be outside the source checkout")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="synapse-core-") as temporary:
        staging = Path(temporary) / "core"
        staging.mkdir()
        base = Path(sys.base_prefix)
        if sys.platform == "win32":
            executable = "python.exe"
            shutil.copy2(base / executable, staging / executable)
            for library in base.glob("*.dll"):
                shutil.copy2(library, staging / library.name)
            copy_standard_library(base / "DLLs", staging / "DLLs")
            standard = staging / "Lib"
            copy_standard_library(base / "Lib", standard)
            packages = standard / "site-packages"
        elif sys.platform.startswith("linux"):
            executable = "bin/python3"
            (staging / "bin").mkdir()
            shutil.copy2(Path(sys._base_executable).resolve(), staging / executable)
            standard = staging / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"
            copy_standard_library(Path(sysconfig.get_path("stdlib")), standard)
            for library in (base / "lib").glob("libpython*.so*"):
                if library.is_file():
                    shutil.copy2(library, staging / "lib" / library.name, follow_symlinks=True)
            packages = standard / "site-packages"
        else:
            raise ValueError("Only Windows and Linux desktop bundles are supported")
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-compile", "--target", str(packages), str(ROOT)], check=True)
        remove_installation_artifacts(packages)
        # Distribution wheels can contain their own acceptance suites. They are
        # unnecessary runtime resources and are excluded before hashing.
        for path in sorted(staging.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_dir() and path.name in {"tests", "test", "__pycache__"}:
                shutil.rmtree(path)
        for name in ("LICENSE",):
            shutil.copy2(ROOT / name, staging / f"SYNAPSE-{name}")
        for name in ("LICENSE.txt", "LICENSE"):
            if (base / name).is_file():
                shutil.copy2(base / name, staging / "PYTHON-LICENSE.txt")
                break
        # Run the exact staged interpreter with isolation; no source-tree path
        # or active virtual environment can supply the package being checked.
        version = subprocess.check_output([str(staging / executable), "-I", "-B", "-c",
            "from synapse.version import RUNTIME_VERSION; print(RUNTIME_VERSION)"], cwd=staging, text=True).strip()
        subprocess.run([str(staging / executable), "-I", "-B", "-m", "synapse", "machine", "--help"],
                       cwd=staging, check=True, stdout=subprocess.DEVNULL)
        manifest = inventory(staging, executable, version)
        (staging / "bundle-manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        # Only sanitized resources may enter the checkout. A sibling staging
        # directory keeps final publication atomic even across temporary volumes.
        with tempfile.TemporaryDirectory(prefix="synapse-publish-", dir=destination.parent) as publication:
            ready = Path(publication) / "core"
            shutil.copytree(staging, ready)
            ready.rename(destination)
    print(f"Bundled canonical core: {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output)
