"""Reusable AS2 architectural boundary guards for P0.6.17-P0.6.19.

The guard is intentionally AST-based and standard-library-only. It enforces
structural AS2 boundary invariants without importing the modules under test,
which keeps the checks side-effect free and suitable for future runtime-wiring
patches.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class BoundaryViolation:
    """A single static AS2 boundary violation."""

    path: Path
    lineno: int
    kind: str
    symbol: str

    def render(self) -> str:
        return f"{self.path}:{self.lineno}: forbidden {self.kind}: {self.symbol}"


@dataclass(frozen=True)
class AS2BoundaryGuard:
    """AST guard for AS2 adapter/bridge architectural invariants.

    P0.6.17 limits the default production scan to the AS2 boundary modules that
    must remain isolated from legacy runtime layers and test-support dependencies.
    Call checks are intended
    for bridge/controller-like modules; the standalone adapter may define the
    projection function and construct AgentSnapshot in its approved synthetic
    projection path, so production call checks are applied to the bridge only.
    """

    forbidden_imports: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "synapse.agent_runtime",
                "synapse.environment",
                "synapse.interpreter",
                "synapse.actor_runtime",
                "tests",
            }
        )
    )
    forbidden_calls: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "project_validated_as2_inputs",
                "AgentSnapshot",
            }
        )
    )

    def check_file(
        self,
        path: Path,
        *,
        check_imports: bool = True,
        check_calls: bool = True,
    ) -> list[BoundaryViolation]:
        """Return all boundary violations found in ``path``.

        The function parses source only; it never imports the target module.
        Forbidden calls are detected for both direct calls such as
        ``AgentSnapshot(...)`` and attribute calls such as
        ``module.AgentSnapshot(...)``.
        """

        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        violations: list[BoundaryViolation] = []
        for node in ast.walk(tree):
            if check_imports and isinstance(node, ast.Import):
                violations.extend(self._check_import_node(path, node))
            elif check_imports and isinstance(node, ast.ImportFrom):
                violations.extend(self._check_import_from_node(path, node))
            elif check_calls and isinstance(node, ast.Call):
                violation = self._check_call_node(path, node)
                if violation is not None:
                    violations.append(violation)
        return violations

    def check_package(
        self,
        root: Path,
        *,
        include_globs: Sequence[str],
        call_check_globs: Sequence[str] = (),
    ) -> list[BoundaryViolation]:
        """Check selected Python files under ``root``.

        ``include_globs`` defines the AS2 boundary scope. ``call_check_globs``
        narrows forbidden-call checks to modules where those calls are illegal.
        This avoids false positives in the standalone adapter projection path
        while still protecting the bridge boundary.
        """

        files = self._iter_files(root, include_globs)
        call_checked = set(self._iter_files(root, call_check_globs)) if call_check_globs else set()
        violations: list[BoundaryViolation] = []
        for path in files:
            violations.extend(
                self.check_file(
                    path,
                    check_imports=True,
                    check_calls=path in call_checked,
                )
            )
        return violations


    def check_forbidden_imported_symbols(
        self,
        path: Path,
        *,
        forbidden_symbols: frozenset[str],
    ) -> list[BoundaryViolation]:
        """Return forbidden symbol imports from ``from ... import Symbol`` forms.

        This helper is intentionally opt-in so the standalone adapter may keep
        its approved projection-core imports while bridge/skeleton modules can
        be checked for stricter no-retention/no-projection boundaries.
        """

        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        violations: list[BoundaryViolation] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                if alias.name in forbidden_symbols:
                    violations.append(BoundaryViolation(path, node.lineno, "symbol import", alias.name))
        return violations

    def _iter_files(self, root: Path, globs: Sequence[str]) -> list[Path]:
        paths: list[Path] = []
        for pattern in globs:
            paths.extend(path for path in root.glob(pattern) if path.is_file())
        return sorted(set(paths))

    def check_files(
        self,
        paths: Sequence[Path],
        *,
        check_imports: bool = True,
        check_calls: bool = True,
    ) -> list[BoundaryViolation]:
        """Check an explicit sequence of Python files."""

        violations: list[BoundaryViolation] = []
        for path in paths:
            violations.extend(
                self.check_file(
                    path,
                    check_imports=check_imports,
                    check_calls=check_calls,
                )
            )
        return violations

    def _check_import_node(self, path: Path, node: ast.Import) -> list[BoundaryViolation]:
        violations: list[BoundaryViolation] = []
        for alias in node.names:
            if self._is_forbidden_import(alias.name):
                violations.append(BoundaryViolation(path, node.lineno, "import", alias.name))
        return violations

    def _check_import_from_node(self, path: Path, node: ast.ImportFrom) -> list[BoundaryViolation]:
        module = node.module or ""
        if self._is_forbidden_import(module):
            return [BoundaryViolation(path, node.lineno, "import", module)]
        return []

    def _check_call_node(self, path: Path, node: ast.Call) -> BoundaryViolation | None:
        call_name = self._call_symbol(node.func)
        if call_name is None:
            return None
        if call_name in self.forbidden_calls:
            return BoundaryViolation(path, node.lineno, "call", call_name)
        return None

    def _is_forbidden_import(self, module_name: str) -> bool:
        return any(
            module_name == forbidden or module_name.startswith(f"{forbidden}.")
            for forbidden in self.forbidden_imports
        )

    def _call_symbol(self, func: ast.expr) -> str | None:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None


def render_violations(violations: Iterable[BoundaryViolation]) -> str:
    """Render violations for pytest assertion messages."""

    return "\n".join(violation.render() for violation in violations)
