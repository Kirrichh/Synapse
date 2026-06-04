"""P0.6.20 permanent guard against AS2 legacy selector reintroduction."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REMOVED_LEGACY_ALIAS = "legacy_agent_runtime_to_dict"
REMOVED_BRIDGE_ALIAS = "model_selector"
REMOVED_ALIASES = frozenset({REMOVED_LEGACY_ALIAS, REMOVED_BRIDGE_ALIAS})
ALLOWED_PATHS = {
    Path("tests/test_as2_legacy_reintroduction_guard.py"),
}


@dataclass(frozen=True)
class RemovedAliasUsage:
    path: Path
    lineno: int
    symbol_kind: str
    alias: str
    owner: str

    def render(self) -> str:
        return f"{self.path}:{self.lineno}: {self.alias} as {self.symbol_kind} in {self.owner}"


class _RemovedAliasVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.scope_stack: list[str] = []
        self.usages: list[RemovedAliasUsage] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope_stack.append(node.name)
        self._check_args(node.args, node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope_stack.append(node.name)
        self._check_args(node.args, node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_arg(self, node: ast.arg) -> None:
        if node.arg in REMOVED_ALIASES:
            self._record(node.lineno, "arg", node.arg)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in REMOVED_ALIASES:
            self._record(node.lineno, "name", node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in REMOVED_ALIASES:
            self._record(node.lineno, "attribute", node.attr)
        self.generic_visit(node)

    def visit_keyword(self, node: ast.keyword) -> None:
        if node.arg in REMOVED_ALIASES:
            self._record(node.lineno, "keyword", node.arg)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            for alias in REMOVED_ALIASES:
                if alias in node.value:
                    self._record(node.lineno, "string", alias)

    def _check_args(self, args: ast.arguments, owner: str) -> None:
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if arg.arg in REMOVED_ALIASES:
                self.usages.append(RemovedAliasUsage(self.path, arg.lineno, "arg", arg.arg, owner))

    def _record(self, lineno: int, symbol_kind: str, alias: str) -> None:
        owner = self.scope_stack[-1] if self.scope_stack else "<module>"
        self.usages.append(RemovedAliasUsage(self.path, lineno, symbol_kind, alias, owner))


def _relative(path: Path) -> Path:
    return path.relative_to(PROJECT_ROOT)


def _removed_alias_usages(path: Path) -> list[RemovedAliasUsage]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    visitor = _RemovedAliasVisitor(_relative(path))
    visitor.visit(tree)
    return visitor.usages


def test_removed_as2_legacy_aliases_cannot_reappear_in_code_or_primary_tests() -> None:
    """Permanent guard: P0.6.20 Contract removed legacy selector aliases."""

    violations: list[RemovedAliasUsage] = []

    for root in (PROJECT_ROOT / "synapse", PROJECT_ROOT / "tests"):
        for path in sorted(root.rglob("*.py")):
            rel = _relative(path)
            if rel in ALLOWED_PATHS:
                continue
            violations.extend(_removed_alias_usages(path))

    assert not violations, "Removed AS2 legacy selector alias found:\n" + "\n".join(
        usage.render() for usage in violations
    )
