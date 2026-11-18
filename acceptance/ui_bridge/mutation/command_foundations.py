"""Isolated command/JCS mutations using installed Python, Node and native Rust.

The shared executor owns mutation application and result classification. This
adapter copies only required build inputs and uses Node's native type stripping,
so the campaign requires neither symlink privileges nor a desktop resource tree.
"""
from __future__ import annotations

import argparse
from argparse import Namespace
import os
from pathlib import Path
import shutil
import subprocess
import sys

from acceptance.ui_bridge.mutation import run


def copy_subject(destination: Path) -> None:
    shutil.copytree(run.ROOT / "synapse", destination / "synapse",
                    ignore=shutil.ignore_patterns("ui", "__pycache__", "*.pyc"))
    ui = Path("synapse/ui")
    for directory in (ui / "src", ui / "contracts", ui / "tooling", ui / "src-tauri/src",
                      Path("acceptance/ui_bridge"), Path("acceptance/agents")):
        shutil.copytree(run.ROOT / directory, destination / directory,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "target"))
    for file in ("pyproject.toml", "LICENSE", "synapse/ui/package.json", "synapse/ui/src-tauri/Cargo.toml",
                 "synapse/ui/src-tauri/Cargo.lock", "synapse/ui/src-tauri/build.rs"):
        shutil.copy2(run.ROOT / file, destination / file)
    (destination / "scripts/ui").mkdir(parents=True)
    shutil.copy2(run.ROOT / "scripts/ui/build_core_bundle.py", destination / "scripts/ui/build_core_bundle.py")
    shutil.copytree(run.ROOT / ui / "node_modules/lossless-json",
                    destination / ui / "node_modules/lossless-json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cargo", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--only", action="append")
    parser.add_argument("--seeds", type=Path, default=Path(__file__).with_name("command-foundations.json"))
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if workspace.is_relative_to(run.ROOT) or args.output.resolve().is_relative_to(run.ROOT):
        parser.error("raw campaign output and working copies must remain outside the checkout")
    workspace.mkdir(parents=True, exist_ok=True)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["PYTEST_ADDOPTS"] = f'--basetemp="{workspace / "pytest"}"'
    os.environ["TMP"] = os.environ["TEMP"] = str(workspace)
    original_execute, original_selected = run.execute, run.selected_cases

    def execute(seed, root, python, cargo, timeout):
        if seed["kind"] != "typescript":
            return original_execute(seed, root, python, cargo, timeout)
        if seed.get("generate"):
            shutil.copytree(run.ROOT / "synapse/ui/node_modules", root / "synapse/ui/node_modules", dirs_exist_ok=True)
            generated = subprocess.run([args.node, "tooling/generate-contracts.mjs"], cwd=root / "synapse/ui",
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
            if generated.returncode:
                return "INVALID", generated.stdout
        cases = seed.get("cases", ["acceptance/ui_bridge/typescript/jcs.test.ts"])
        loader = (["--import", (run.ROOT / "synapse/ui/node_modules/tsx/dist/loader.mjs").as_uri()]
                  if seed.get("tsx") else ["--experimental-strip-types"])
        result = subprocess.run([args.node, *loader, "--test", *cases], cwd=root,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, timeout=timeout)
        if result.returncode == 0:
            return "SURVIVED", result.stdout
        return ("KILLED" if "AssertionError" in result.stdout else "INVALID"), result.stdout

    run.copy_subject = copy_subject
    run.execute = execute
    run.selected_cases = lambda seed: (seed.get("cases", ["acceptance/ui_bridge/typescript/jcs.test.ts"])
        if seed["kind"] == "typescript" else original_selected(seed))
    return run.main(Namespace(seeds=[args.seeds],
        only=args.only, workspace=workspace, output=args.output, python=sys.executable,
        cargo=args.cargo, timeout=120))


if __name__ == "__main__":
    raise SystemExit(main())
