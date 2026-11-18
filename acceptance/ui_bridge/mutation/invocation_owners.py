"""Serial invocation-owner mutations, without desktop resources or symlink rights.

Uses the shared mutation executor and its nonsecret result format. Every subject
and pytest runtime directory is outside the source checkout. No product file in
the working tree is mutated. Only the Python invocation owner family is selected.
"""
from __future__ import annotations

import argparse
from argparse import Namespace
import os
from pathlib import Path
import shutil
import sys

from acceptance.ui_bridge.mutation import run


def copy_subject(destination: Path) -> None:
    shutil.copytree(run.ROOT / "synapse", destination / "synapse",
                    ignore=shutil.ignore_patterns("ui", "__pycache__", "*.pyc"))
    for directory in ("acceptance/agents", "acceptance/ui_bridge"):
        shutil.copytree(run.ROOT / directory, destination / directory,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "target"))
    shutil.copy2(run.ROOT / "pyproject.toml", destination / "pyproject.toml")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", action="append")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if workspace.is_relative_to(run.ROOT):
        parser.error("mutation workspace must remain outside the source checkout")
    workspace.mkdir(parents=True, exist_ok=True)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["PYTEST_ADDOPTS"] = f'--basetemp="{workspace / "pytest"}"'
    os.environ["TMP"] = os.environ["TEMP"] = str(workspace)
    run.copy_subject = copy_subject
    return run.main(Namespace(seeds=[Path(__file__).with_name("invocation-owners.json")],
        only=args.only, workspace=workspace, output=args.output, python=sys.executable,
        cargo="cargo", timeout=45))


if __name__ == "__main__":
    raise SystemExit(main())
