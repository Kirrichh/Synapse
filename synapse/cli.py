"""Synapse CLI for alpha3e tooling."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Optional, TextIO

from .lexer import Lexer
from .parser import Parser
from .interpreter import Interpreter
from .golden_replay import (
    record_source,
    replay_mock_artifact,
    DeterministicReplayError,
    ReplayArtifactError,
)
from .debugger_core import (
    EventInjectionValidator,
    GoldenArtifactTraceAdapter,
    ForkDisposedError,
    ForkLifecycleError,
    ForkRegistry,
    ForkResourceLimitError,
    find_trace_divergence,
    GovernanceViolationError,
    REPLAY_DETERMINISTIC,
    REPLAY_EXPLORATORY_LIVE,
)


class CLIArgError(ValueError):
    """Raised for transport-level CLI argument errors."""


class CLICompareIntegrityError(RuntimeError):
    """Raised when debug compare cannot trust artifact/replay integrity."""


class SynapseDebugParser(argparse.ArgumentParser):
    """Debug CLI parser with Synapse-stable transport error semantics.

    argparse defaults to SystemExit(2) for syntax errors. The debugger CLI
    contract reserves exit 1 for transport-level CLI argument/JSON errors, so
    parse errors are converted into CLIArgError and mapped by run_debug_cli.
    Help remains the normal argparse success path via SystemExit(0).
    """

    def error(self, message: str) -> None:  # pragma: no cover - exercised through run_debug_cli
        raise CLIArgError(message)


_DEBUG_REGISTRY = ForkRegistry()


def compile_source(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


def _json_dump(data: Any) -> str:
    return json.dumps(data, sort_keys=True)


def _parse_json_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CLIArgError(f"malformed json: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise CLIArgError("payload must be a JSON object")
    return payload


def _error_exit(exc: Exception, stderr: TextIO) -> int:
    if isinstance(exc, CLIArgError):
        print(f"cli: invalid argument / malformed json — {exc}", file=stderr)
        return 1
    if isinstance(exc, CLICompareIntegrityError):
        print(f"core: replay artifact integrity error — {exc}", file=stderr)
        return 8
    if isinstance(exc, GovernanceViolationError):
        print(f"core: governance violation — {exc}", file=stderr)
        return 2
    if isinstance(exc, DeterministicReplayError):
        print(f"core: deterministic replay constraint violated — {exc}", file=stderr)
        return 3
    if isinstance(exc, ForkDisposedError):
        print(f"core: {exc}", file=stderr)
        return 4
    if isinstance(exc, ForkLifecycleError):
        print(f"core: invalid fork lifecycle transition — {exc}", file=stderr)
        return 5
    if isinstance(exc, ForkResourceLimitError):
        print(f"core: fork resource limit exceeded — {exc}", file=stderr)
        return 6
    if isinstance(exc, (KeyError, ValueError)):
        print(f"cli: invalid argument / malformed json — {exc}", file=stderr)
        return 1
    print(f"cli: invalid argument / malformed json — {exc}", file=stderr)
    return 1


def _record_view(record: Any) -> dict[str, Any]:
    return {
        "fork_id": record.fork_id,
        "parent_history_hash": record.parent_history_hash,
        "parent_fork_id": record.parent_fork_id,
        "mode": record.mode,
        "status": record.status,
        "metadata": dict(record.metadata),
    }


def _fork_resource_deltas(registry: ForkRegistry, fork_id: str) -> list[dict[str, Any]]:
    """Return read-only state deltas for attached debugger resources.

    Patch 3 has no session persistence and no long-lived daemon. This helper is
    intentionally read-only and only supports process-scoped tests/diagnostics.
    """
    deltas: list[dict[str, Any]] = []
    for resource in getattr(registry, "_resources", {}).get(fork_id, []):
        if hasattr(resource, "state_delta"):
            deltas.append(resource.state_delta())
    return deltas


def _debug_fork(args: argparse.Namespace, registry: ForkRegistry, stdout: TextIO) -> int:
    record = registry.create_fork(parent_history_hash=args.parent_hash, mode=args.mode)
    print(_json_dump({"created": _record_view(record)}), file=stdout)
    return 0


def _debug_dispose(args: argparse.Namespace, registry: ForkRegistry, stdout: TextIO) -> int:
    record = registry.dispose(args.fork_id)
    print(_json_dump({"disposed": _record_view(record)}), file=stdout)
    return 0


def _debug_status(args: argparse.Namespace, registry: ForkRegistry, stdout: TextIO) -> int:
    record = registry.get(args.fork_id)
    print(_json_dump({"status": _record_view(record)}), file=stdout)
    return 0


def _debug_inject_event(args: argparse.Namespace, registry: ForkRegistry, stdout: TextIO) -> int:
    payload = _parse_json_payload(args.payload)
    validator = EventInjectionValidator(registry)
    result = validator.validate_injection(
        fork_id=args.fork_id,
        event_type=args.event_type,
        payload=payload,
    )
    print(_json_dump({
        "validated": {
            "allowed": result.allowed,
            "event_type": result.event_type,
            "fork_id": result.fork_id,
            "payload": result.sanitized_payload,
            "reason": result.reason,
        }
    }), file=stdout)
    return 0


def _debug_compare(args: argparse.Namespace, registry: ForkRegistry, stdout: TextIO) -> int:
    """Compare two artifact-backed traces via the core divergence engine.

    P7 intentionally compares artifact directories, not fork ids. ForkRecord is
    identity/lifecycle metadata and does not yet carry execution_history. The
    CLI does not compute hashes; it loads read-only trace adapters and delegates
    forensic comparison to find_trace_divergence().
    """
    left = Path(args.left_artifact)
    right = Path(args.right_artifact)
    if not left.exists() or not left.is_dir():
        raise CLIArgError(f"left artifact directory not found: {left}")
    if not right.exists() or not right.is_dir():
        raise CLIArgError(f"right artifact directory not found: {right}")
    try:
        result = find_trace_divergence(
            GoldenArtifactTraceAdapter(left),
            GoldenArtifactTraceAdapter(right),
        )
    except (ReplayArtifactError, DeterministicReplayError) as exc:
        raise CLICompareIntegrityError(str(exc)) from exc
    print(_json_dump(result.to_dict()), file=stdout)
    return 0 if result.equal else 7


def _build_debug_parser() -> argparse.ArgumentParser:
    parser = SynapseDebugParser(prog="synapse debug")
    sub = parser.add_subparsers(dest="debug_cmd", required=True, parser_class=SynapseDebugParser)

    fork = sub.add_parser("fork")
    fork.add_argument("--from", dest="parent_hash", required=True)
    fork.add_argument(
        "--mode",
        choices=[REPLAY_DETERMINISTIC, REPLAY_EXPLORATORY_LIVE],
        default=REPLAY_DETERMINISTIC,
    )

    dispose = sub.add_parser("dispose")
    dispose.add_argument("fork_id")

    inject = sub.add_parser("inject-event")
    inject.add_argument("--fork-id", required=True)
    inject.add_argument("--type", dest="event_type", required=True)
    inject.add_argument("--payload", required=True)

    compare = sub.add_parser("compare")
    compare.add_argument("left_artifact")
    compare.add_argument("right_artifact")

    status = sub.add_parser("status")
    status.add_argument("fork_id")
    return parser


def run_debug_cli(
    argv: Optional[Iterable[str]] = None,
    *,
    registry: Optional[ForkRegistry] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> int:
    """Run the process-scoped debugger CLI surface.

    This is a thin transport layer. It parses command-line arguments and JSON,
    then delegates structural/governance decisions to debugger_core.
    """
    registry = registry or _DEBUG_REGISTRY
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = _build_debug_parser()
    try:
        args = parser.parse_args(list(argv or []))
        if args.debug_cmd == "fork":
            return _debug_fork(args, registry, stdout)
        if args.debug_cmd == "dispose":
            return _debug_dispose(args, registry, stdout)
        if args.debug_cmd == "inject-event":
            return _debug_inject_event(args, registry, stdout)
        if args.debug_cmd == "compare":
            return _debug_compare(args, registry, stdout)
        if args.debug_cmd == "status":
            return _debug_status(args, registry, stdout)
        raise CLIArgError(f"unknown debug command: {args.debug_cmd}")
    except SystemExit as exc:  # argparse transport error
        if exc.code == 0:
            return 0
        print("cli: invalid argument / malformed json", file=stderr)
        return 1
    except Exception as exc:  # mapped core/transport errors
        return _error_exit(exc, stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="synapse")
    sub = ap.add_subparsers(dest="cmd")

    run = sub.add_parser("run")
    run.add_argument("file")
    run.add_argument("--record", action="store_true", help="record a golden replay artifact")
    run.add_argument("--output", help="artifact output directory for --record")
    run.add_argument("--layer", choices=["strict", "smoke"], default="strict")

    replay = sub.add_parser("replay")
    replay.add_argument("--mock", required=True, help="golden replay artifact directory")

    debug = sub.add_parser("debug")
    debug.add_argument("debug_args", nargs=argparse.REMAINDER)

    sub.add_parser("metrics")
    args = ap.parse_args(argv)

    if args.cmd == "run":
        path = Path(args.file)
        source = path.read_text(encoding="utf-8")
        if args.record:
            if not args.output:
                raise SystemExit("synapse run --record requires --output <artifact_dir>")
            manifest = record_source(source, args.output, source_path=str(path), layer=args.layer)
            print(json.dumps({
                "recorded": args.output,
                "program_hash": manifest["metadata"]["program_hash"],
                "final_history_hash": manifest["final"]["final_history_hash"],
                "history_length": manifest["final"]["history_length"],
            }, sort_keys=True))
            return
        interp = Interpreter(); interp.source_code = source
        interp.interpret(compile_source(source))
        print(interp.get_output())
    elif args.cmd == "replay":
        try:
            result = replay_mock_artifact(args.mock)
        except DeterministicReplayError as exc:
            raise SystemExit(f"DeterministicReplayError: {exc}") from exc
        print(json.dumps(result.to_dict(), sort_keys=True))
    elif args.cmd == "debug":
        code = run_debug_cli(args.debug_args)
        if code:
            raise SystemExit(code)
    elif args.cmd == "metrics":
        print(Interpreter().metrics_text())
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
