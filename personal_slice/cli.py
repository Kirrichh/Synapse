"""Compatibility CLI for the historical Personal Slice entry point."""

from __future__ import annotations

import argparse
import sys

from synapse.change import ControlledChangeRequest, ControlledChangeResult, execute_controlled_change


def display_result(result: ControlledChangeResult, *, label: str = "Personal Slice") -> None:
    print(f"{label} outcome: {result.outcome}")
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


def handle_run(args: argparse.Namespace) -> int:
    task_path = args.task or args.task_json
    if not task_path:
        raise SystemExit("personal_slice run requires --task <task-path>")
    request = ControlledChangeRequest(
        base=args.base,
        task_path=task_path,
        keep_worktree=args.keep_worktree,
        report_dir=args.report_dir,
        environment_kind=args.environment_kind,
    )
    result = execute_controlled_change(request)
    display_result(result)
    return result.exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the historical Personal Slice compatibility CLI")
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="run a controlled-change task contract")
    run_parser.add_argument("task_json", nargs="?", help="compatibility positional task path")
    run_parser.add_argument("--base", default="HEAD", help="base revision containing the committed task JSON")
    run_parser.add_argument("--task", help="repository-relative task JSON path")
    run_parser.add_argument("--keep-worktree", action="store_true", help="preserve isolated worktree for inspection")
    run_parser.add_argument("--report-dir", help="directory for the JSON report")
    run_parser.add_argument("--environment-kind", default="UNSPECIFIED", help="environment label recorded in the report")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return handle_run(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
