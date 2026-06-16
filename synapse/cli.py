"""Synapse CLI for alpha3e tooling."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Optional, TextIO

from .application import (
    DurableRunRequest,
    DurableRunResult,
    FileExecutionRequest,
    ReplRequest,
    SourceExecutionRequest,
    RuntimeExecutionResult,
    durable_error_result,
    execute_durable_run,
    execute_file,
    execute_source as execute_runtime_source,
    metrics_text,
    run_repl as run_runtime_repl,
)
from .golden_replay import (
    replay_mock_artifact,
    DeterministicReplayError,
    ReplayArtifactError,
)
from .change import ControlledChangeRequest, ControlledChangeResult, execute_controlled_change
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


ARGUMENT_PARSER = argparse.ArgumentParser

class CLIArgError(ValueError):
    """Raised for transport-level CLI argument errors."""


class CLICompareIntegrityError(RuntimeError):
    """Raised when debug compare cannot trust artifact/replay integrity."""


class SynapseDebugParser (argparse.ArgumentParser):
    """Debug CLI parser with Synapse-stable transport error semantics.

    argparse defaults to SystemExit(2) for syntax errors. The debugger CLI
    contract reserves exit 1 for transport-level CLI argument/JSON errors, so
    parse errors are converted into CLIArgError and mapped by run_debug_cli.
    Help remains the normal argparse success path via SystemExit(0).
    """

    def error(self, message: str) -> None:  # pragma: no cover - exercised through run_debug_cli
        raise CLIArgError(message)


DEBUG_PARSER_CLASS = SynapseDebugParser


_DEBUG_REGISTRY = ForkRegistry()



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
    parser = DEBUG_PARSER_CLASS(prog="synapse debug")
    sub = parser.add_subparsers(dest="debug_cmd", required=True, parser_class=DEBUG_PARSER_CLASS)

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


def _display_change_result(result: ControlledChangeResult) -> None:
    print(f"Controlled Change outcome: {result.outcome}")
    if result.report_path:
        print(f"Report: {result.report_path}")
    if result.verified_commit:
        print(f"Verified commit: {result.verified_commit}")
    if result.verified_tree:
        print(f"Verified tree: {result.verified_tree}")
    if result.evidence_ref:
        print(f"Evidence ref: {result.evidence_ref}")
    if result.application:
        print(f"Application status: {result.application.status}")
    print(f"Cleanup status: {result.cleanup_status}")
    if result.worktree_path:
        print(f"Worktree: {result.worktree_path}")


def handle_change_apply(args: argparse.Namespace) -> int:
    request = ControlledChangeRequest(
        base=args.base,
        task_path=args.task,
        keep_worktree=args.keep_worktree,
        report_dir=args.report_dir,
        environment_kind=args.environment_kind,
    )
    result = execute_controlled_change(request)
    _display_change_result(result)
    return result.exit_code


def _render_runtime_result(result: RuntimeExecutionResult) -> None:
    if result.artifact:
        print(json.dumps(result.artifact.to_json(), sort_keys=True))
        return
    if result.output:
        print(result.output)
    elif result.exit_code == 0:
        print("")
    for diagnostic in result.diagnostics:
        print(f"Error: {diagnostic}", file=sys.stderr)


def _render_durable_result(result: DurableRunResult) -> None:
    if result.public_payload:
        print(json.dumps(result.public_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    for diagnostic in result.diagnostics:
        print(diagnostic, file=sys.stderr)


def _handle_run(args: argparse.Namespace) -> int:
    if args.durable:
        input_from_stdin = args.input_file == "-"
        result = execute_durable_run(
            DurableRunRequest(
                source_path=Path(args.file),
                state_dir=Path(args.state_dir),
                run_id=args.run_id,
                correlation_id=args.correlation_id,
                input_file=None if input_from_stdin or args.input_file is None else Path(args.input_file),
                input_from_stdin=input_from_stdin,
            ),
            stdin=sys.stdin,
        )
        _render_durable_result(result)
        return result.exit_code
    if args.source is not None:
        result = execute_runtime_source(SourceExecutionRequest(args.source))
    else:
        result = execute_file(
            FileExecutionRequest(
                path=Path(args.file),
                record=args.record,
                output_dir=Path(args.output) if args.output else None,
                layer=args.layer or "strict",
            )
        )
    _render_runtime_result(result)
    return result.exit_code


def _durable_invalid_input() -> DurableRunResult:
    return durable_error_result(2, "INVALID_CLI_INPUT", "Invalid durable input")


def main(argv=None) -> int:
    ap = ARGUMENT_PARSER(prog="synapse")
    sub = ap.add_subparsers(dest="cmd")

    run = sub.add_parser("run")
    run.add_argument("file", nargs="?")
    run.add_argument("-c", "--source", help="execute a Synapse source string")
    run.add_argument("--record", action="store_true", help="record a golden replay artifact")
    run.add_argument("--output", help="artifact output directory for --record")
    run.add_argument("--layer", choices=["strict", "smoke"], default=None)
    run.add_argument("--durable", action="store_true", help="execute initial durable run")
    run.add_argument("--state-dir", help="existing durable state directory")
    run.add_argument("--run-id", help="durable run id")
    run.add_argument("--correlation-id", help="durable correlation id")
    run.add_argument("--input-file", help="strict JSON object file for initial bindings, or - for stdin")

    sub.add_parser("repl", help="start the Synapse REPL")

    replay = sub.add_parser("replay")
    replay.add_argument("--mock", required=True, help="golden replay artifact directory")

    debug = sub.add_parser("debug")
    debug.add_argument("debug_args", nargs=argparse.REMAINDER)

    sub.add_parser("metrics")

    change = sub.add_parser("change")
    change_sub = change.add_subparsers(dest="change_cmd")
    change_apply = change_sub.add_parser("apply")
    change_apply.add_argument("--base", required=True, help="base revision containing the committed task JSON")
    change_apply.add_argument("--task", required=True, help="repository-relative task JSON path")
    change_apply.add_argument("--keep-worktree", action="store_true", help="preserve isolated worktree for inspection")
    change_apply.add_argument("--report-dir", help="directory for the JSON report")
    change_apply.add_argument("--environment-kind", default="UNSPECIFIED", help="environment label recorded in the report")

    args = ap.parse_args(argv)

    if args.cmd == "run":
        has_file = args.file is not None
        has_source = args.source is not None
        durable_conditional = {
            "--state-dir": args.state_dir is not None,
            "--run-id": args.run_id is not None,
            "--correlation-id": args.correlation_id is not None,
            "--input-file": args.input_file is not None,
        }
        if not args.durable:
            forbidden = [flag for flag, present in durable_conditional.items() if present]
            if forbidden:
                ap.error(f"{', '.join(forbidden)} require --durable")
        if args.durable:
            if not has_file or has_source:
                result = _durable_invalid_input()
                _render_durable_result(result)
                return result.exit_code
            if args.state_dir is None:
                result = _durable_invalid_input()
                _render_durable_result(result)
                return result.exit_code
            if args.record:
                result = _durable_invalid_input()
                _render_durable_result(result)
                return result.exit_code
            if args.output:
                result = _durable_invalid_input()
                _render_durable_result(result)
                return result.exit_code
            if args.layer is not None:
                result = _durable_invalid_input()
                _render_durable_result(result)
                return result.exit_code
            return _handle_run(args)
        if has_file == has_source:
            ap.error("synapse run requires exactly one source origin: <file> or -c/--source")
        if has_source and args.record:
            ap.error("synapse run -c/--source cannot be combined with --record")
        if has_source and args.output:
            ap.error("synapse run -c/--source cannot be combined with --output")
        if has_source and args.layer is not None:
            ap.error("synapse run -c/--source cannot be combined with --layer")
        if has_file and args.record and not args.output:
            ap.error("synapse run --record requires --output <artifact_dir>")
        if has_file and not args.record and args.output:
            ap.error("synapse run --output requires --record")
        if has_file and not args.record and args.layer is not None:
            ap.error("synapse run --layer applies only with --record")
        return _handle_run(args)
    if args.cmd == "repl":
        return run_runtime_repl(ReplRequest(), stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr).exit_code
    if args.cmd == "replay":
        try:
            result = replay_mock_artifact(args.mock)
        except DeterministicReplayError as exc:
            print(f"DeterministicReplayError: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result.to_dict(), sort_keys=True))
        return 0
    if args.cmd == "debug":
        return run_debug_cli(args.debug_args)
    if args.cmd == "metrics":
        print(metrics_text())
        return 0
    if args.cmd == "change":
        if args.change_cmd == "apply":
            return handle_change_apply(args)
        change.print_help()
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
