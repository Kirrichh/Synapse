#!/usr/bin/env python3
"""
Legacy Synapse CLI compatibility entry point.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from synapse.application import (  # noqa: E402
    FileExecutionRequest,
    ReplRequest,
    SourceExecutionRequest,
    RuntimeExecutionResult,
    execute_file,
    execute_source as execute_runtime_source,
    run_repl as run_runtime_repl,
)


def _render_result(result: RuntimeExecutionResult) -> None:
    if result.artifact:
        return
    if result.output:
        print(result.output)
    elif result.exit_code == 0:
        print("")
    for diagnostic in result.diagnostics:
        if diagnostic.startswith("File not found: "):
            print(diagnostic)
        else:
            print(f"Error: {diagnostic}")


def execute_source(source: str) -> int:
    """Compatibility helper used by legacy callers and tests."""

    result = execute_runtime_source(SourceExecutionRequest(source))
    _render_result(result)
    return result.exit_code


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 1:
        print("Usage: python main.py <file.syn>")
        print("   or: python main.py -c 'code'")
        print("   or: python main.py --repl")
        return 1

    arg = args[0]

    if arg == "--repl":
        return run_runtime_repl(ReplRequest(), stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr).exit_code
    if arg == "-c":
        if len(args) > 1:
            code = args[1]
        else:
            sys.stdout.write("Synapse> ")
            sys.stdout.flush()
            code = sys.stdin.readline().rstrip("\n")
        return execute_source(code)

    result = execute_file(FileExecutionRequest(path=Path(arg)))
    _render_result(result)
    return result.exit_code


def run_repl() -> int:
    """Compatibility helper for historical imports."""

    return run_runtime_repl(ReplRequest(), stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr).exit_code


if __name__ == "__main__":
    raise SystemExit(main())
