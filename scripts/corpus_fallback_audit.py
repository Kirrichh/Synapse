#!/usr/bin/env python3
"""Static corpus fallback audit for the Synapse CVM routing surface.

The audit intentionally does not execute guest programs.  It parses every
``.syn`` file in the selected corpus roots, traverses the AST, classifies each
AST node through ``classify_ast_node_v22()``, and emits a deterministic JSON
report.  The report is used for data-driven prioritization of future CVM work
without changing runtime semantics.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import json
from collections import Counter
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
from synapse.version import LANGUAGE_VERSION as _LANGUAGE_VERSION

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from synapse.ast import Node, Program
from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.runtime.vm_routing import classify_ast_node_v22, fallback_reason_for

DEFAULT_ROOTS: Tuple[str, ...] = ("examples", "tests")
DEFAULT_OUTPUT = "reports/corpus_fallback_alpha3e.json"

# Nodes that still appear as AST-level fallbacks in the static routing table
# but have a compiler lowering path to CVM bytecode for the supported alpha3e
# syntax subset.  This prevents AST parser coverage from being confused with
# runtime-only fallback surface.
LOWERABLE_TO_CVM_NODE_TYPES = {
    "GovernedMemoryWrite": {
        "lowering_status": "lowerable_to_cvm",
        "note": (
            "Handled by Track B.1 inline guarded memory-write lowering; "
            "counted as AST fallback before compiler lowering."
        ),
    },
}



def _repo_root() -> Path:
    return REPO_ROOT


def source_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def discover_syn_files(roots: Sequence[str], base_dir: Optional[Path] = None) -> List[Path]:
    base = base_dir or _repo_root()
    files: List[Path] = []
    for root in roots:
        root_path = (base / root).resolve()
        if not root_path.exists():
            continue
        if root_path.is_file() and root_path.suffix == ".syn":
            files.append(root_path)
            continue
        for path in root_path.rglob("*.syn"):
            parts = set(path.parts)
            if (
                ".git" in parts
                or "__pycache__" in parts
                or ".pytest_cache" in parts
                or "golden_replays_alpha3e" in parts
                or "golden_sources_alpha3e" in parts
            ):
                continue
            files.append(path.resolve())
    return sorted(set(files), key=lambda p: str(p.relative_to(base)))


def parse_source(source: str) -> Program:
    return Parser(Lexer(source).scan_tokens()).parse()


def iter_ast_nodes(root: Any, *, include_program: bool = False) -> Iterator[Node]:
    """Cycle-safe traversal over dataclass AST nodes.

    The traversal includes both statements and expressions.  This makes the
    report a corpus-wide static coverage map rather than a runtime/taken-path
    metric.  Runtime coverage remains available through ``metrics_snapshot()``.
    """
    seen: Set[int] = set()

    def walk(value: Any) -> Iterator[Node]:
        if value is None:
            return
        if isinstance(value, Node):
            obj_id = id(value)
            if obj_id in seen:
                return
            seen.add(obj_id)
            if include_program or not isinstance(value, Program):
                yield value
            if is_dataclass(value):
                for f in fields(value):
                    child = getattr(value, f.name, None)
                    yield from walk(child)
            return
        if isinstance(value, dict):
            for k, v in value.items():
                yield from walk(k)
                yield from walk(v)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                yield from walk(item)
            return

    yield from walk(root)


def audit_file(path: Path, base_dir: Optional[Path] = None) -> Dict[str, Any]:
    base = base_dir or _repo_root()
    rel = str(path.relative_to(base)) if path.is_absolute() else str(path)
    source = path.read_text(encoding="utf-8")
    digest = source_sha256(source)

    try:
        program = parse_source(source)
    except Exception as exc:  # parse errors should be visible, not fatal to the whole corpus audit
        return {
            "path": rel,
            "source_sha256": digest,
            "parse_ok": False,
            "parse_error": f"{type(exc).__name__}: {exc}",
            "total_nodes": 0,
            "cvm_compilable": 0,
            "fallback": 0,
            "coverage_ratio": 0.0,
            "fallback_by_node_type": {},
            "fallback_by_reason": {},
        }

    total = 0
    cvm = 0
    fallback = 0
    fallback_by_node: Counter[str] = Counter()
    fallback_by_reason: Counter[str] = Counter()
    cvm_by_node: Counter[str] = Counter()
    lowerable_by_node: Counter[str] = Counter()
    runtime_only_by_node: Counter[str] = Counter()

    for node in iter_ast_nodes(program):
        node_type = type(node).__name__
        decision = classify_ast_node_v22(node_type)
        total += 1
        if decision.route == "CVM":
            cvm += 1
            cvm_by_node[node_type] += 1
        else:
            fallback += 1
            fallback_by_node[node_type] += 1
            reason = fallback_reason_for(node_type)
            fallback_by_reason[reason.get("code", "UNKNOWN")] += 1
            if node_type in LOWERABLE_TO_CVM_NODE_TYPES:
                lowerable_by_node[node_type] += 1
            else:
                runtime_only_by_node[node_type] += 1

    coverage = round(cvm / total, 6) if total else 0.0
    return {
        "path": rel,
        "source_sha256": digest,
        "parse_ok": True,
        "total_nodes": total,
        "cvm_compilable": cvm,
        "fallback": fallback,
        "coverage_ratio": coverage,
        "fallback_by_node_type": dict(sorted(fallback_by_node.items(), key=lambda kv: (-kv[1], kv[0]))),
        "fallback_by_reason": dict(sorted(fallback_by_reason.items(), key=lambda kv: (-kv[1], kv[0]))),
        "lowerable_to_cvm_by_node_type": dict(sorted(lowerable_by_node.items(), key=lambda kv: (-kv[1], kv[0]))),
        "runtime_only_fallback_by_node_type": dict(sorted(runtime_only_by_node.items(), key=lambda kv: (-kv[1], kv[0]))),
        "runtime_only_fallbacks": sum(runtime_only_by_node.values()),
        "cvm_by_node_type": dict(sorted(cvm_by_node.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def build_report(roots: Sequence[str] = DEFAULT_ROOTS, base_dir: Optional[Path] = None) -> Dict[str, Any]:
    base = base_dir or _repo_root()
    files = discover_syn_files(roots, base)
    file_reports = [audit_file(path, base) for path in files]

    total_nodes = sum(item["total_nodes"] for item in file_reports)
    total_cvm = sum(item["cvm_compilable"] for item in file_reports)
    total_fallback = sum(item["fallback"] for item in file_reports)
    parse_errors = [
        {"path": item["path"], "error": item.get("parse_error", "")}
        for item in file_reports
        if not item.get("parse_ok")
    ]

    fallback_by_node: Counter[str] = Counter()
    fallback_by_reason: Counter[str] = Counter()
    cvm_by_node: Counter[str] = Counter()
    lowerable_by_node: Counter[str] = Counter()
    runtime_only_by_node: Counter[str] = Counter()
    for item in file_reports:
        fallback_by_node.update(item.get("fallback_by_node_type", {}))
        fallback_by_reason.update(item.get("fallback_by_reason", {}))
        cvm_by_node.update(item.get("cvm_by_node_type", {}))
        lowerable_by_node.update(item.get("lowerable_to_cvm_by_node_type", {}))
        runtime_only_by_node.update(item.get("runtime_only_fallback_by_node_type", {}))

    coverage = round(total_cvm / total_nodes, 6) if total_nodes else 0.0
    return {
        "schema_version": "2",
        "version": _LANGUAGE_VERSION,
        "routing_model": "static_ast_plus_lowering_status_v22",
        "description": (
            "Static corpus fallback audit using classify_ast_node_v22(), with an "
            "additional lowering-status layer for AST nodes that compile to CVM "
            "after source-level lowering. This is not runtime/taken-path coverage."
        ),
        "roots": list(roots),
        "files_scanned": len(file_reports),
        "files_parse_ok": sum(1 for item in file_reports if item.get("parse_ok")),
        "files_parse_failed": len(parse_errors),
        "parse_errors": parse_errors,
        "total_ast_nodes": total_nodes,
        "total_cvm_compilable": total_cvm,
        "total_fallback": total_fallback,
        "corpus_coverage_ratio": coverage,
        "lowering_status_by_node_type": LOWERABLE_TO_CVM_NODE_TYPES,
        "corpus_lowerable_to_cvm_by_node_type": dict(sorted(lowerable_by_node.items(), key=lambda kv: (-kv[1], kv[0]))),
        "runtime_only_fallbacks": sum(runtime_only_by_node.values()),
        "corpus_runtime_only_fallback_by_node_type": dict(sorted(runtime_only_by_node.items(), key=lambda kv: (-kv[1], kv[0]))),
        "corpus_fallback_by_node_type": dict(sorted(fallback_by_node.items(), key=lambda kv: (-kv[1], kv[0]))),
        "corpus_fallback_by_reason": dict(sorted(fallback_by_reason.items(), key=lambda kv: (-kv[1], kv[0]))),
        "corpus_cvm_by_node_type": dict(sorted(cvm_by_node.items(), key=lambda kv: (-kv[1], kv[0]))),
        "files": file_reports,
    }


def write_report(report: Dict[str, Any], output: str, base_dir: Optional[Path] = None) -> Path:
    base = base_dir or _repo_root()
    out = (base / output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit Synapse .syn corpus fallback distribution.")
    parser.add_argument("--roots", nargs="+", default=list(DEFAULT_ROOTS), help="Corpus roots to scan")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="JSON report output path")
    parser.add_argument("--no-write", action="store_true", help="Print report to stdout without writing")
    args = parser.parse_args(argv)

    report = build_report(args.roots)
    if args.no_write:
        print(json.dumps(report, indent=2))
    else:
        out = write_report(report, args.output)
        print(f"Wrote corpus fallback audit: {out}")
        print(
            f"files={report['files_scanned']} parse_failed={report['files_parse_failed']} "
            f"coverage={report['corpus_coverage_ratio']} fallback={report['total_fallback']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
