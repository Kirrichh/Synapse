from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("synapse", "personal_slice", "scripts", "examples")
TRIPWIRE_MESSAGE = (
    "The same PR that introduces production GOLD execution must also add "
    "controlled-change evidence validation and remove or replace this tripwire."
)


@dataclass(frozen=True)
class GoldConstructionViolation:
    path: Path
    lineno: int
    kind: str

    def render(self) -> str:
        return f"{self.path}:{self.lineno}: forbidden {self.kind}"


def _is_experiment_arm_expr(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Name)
        and node.id == "ExperimentArm"
    ) or (
        isinstance(node, ast.Attribute)
        and node.attr == "ExperimentArm"
    )


def _is_gold_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == "GOLD"


def _node_source(node: ast.AST, fallback: str) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return fallback


def _scan_import_from(path: Path, node: ast.ImportFrom) -> list[GoldConstructionViolation]:
    violations: list[GoldConstructionViolation] = []
    for alias in node.names:
        if alias.name == "ExperimentArm" and alias.asname is not None:
            violations.append(
                GoldConstructionViolation(path, node.lineno, "aliased ExperimentArm import")
            )
    return violations


def _scan_attribute(path: Path, node: ast.Attribute) -> GoldConstructionViolation | None:
    if node.attr == "GOLD" and _is_experiment_arm_expr(node.value):
        return GoldConstructionViolation(
            path,
            node.lineno,
            _node_source(node, "ExperimentArm.GOLD"),
        )
    return None


def _scan_call(path: Path, node: ast.Call) -> GoldConstructionViolation | None:
    if _is_experiment_arm_expr(node.func) and any(_is_gold_constant(arg) for arg in node.args):
        return GoldConstructionViolation(
            path,
            node.lineno,
            _node_source(node, 'ExperimentArm("GOLD")'),
        )
    if (
        isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and _is_experiment_arm_expr(node.args[0])
        and _is_gold_constant(node.args[1])
    ):
        return GoldConstructionViolation(
            path,
            node.lineno,
            _node_source(node, 'getattr(ExperimentArm, "GOLD")'),
        )
    return None


def _scan_subscript(path: Path, node: ast.Subscript) -> GoldConstructionViolation | None:
    if _is_experiment_arm_expr(node.value) and _is_gold_constant(node.slice):
        return GoldConstructionViolation(
            path,
            node.lineno,
            _node_source(node, 'ExperimentArm["GOLD"]'),
        )
    return None


def _scan_file(path: Path) -> list[GoldConstructionViolation]:
    source = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source, filename=str(path))
    violations: list[GoldConstructionViolation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            violations.extend(_scan_import_from(path, node))
        elif isinstance(node, ast.Attribute):
            violation = _scan_attribute(path, node)
            if violation is not None:
                violations.append(violation)
        elif isinstance(node, ast.Call):
            violation = _scan_call(path, node)
            if violation is not None:
                violations.append(violation)
        elif isinstance(node, ast.Subscript):
            violation = _scan_subscript(path, node)
            if violation is not None:
                violations.append(violation)
    return violations


def _iter_scanned_python_files() -> Iterable[Path]:
    for root_name in SCAN_ROOTS:
        root = PROJECT_ROOT / root_name
        if not root.exists():
            continue
        yield from sorted(path for path in root.rglob("*.py") if path.is_file())


def _render_failure(violations: list[GoldConstructionViolation]) -> str:
    rendered = "\n".join(violation.render() for violation in violations)
    return (
        "Production GOLD construction detected.\n"
        f"{rendered}\n"
        f"{TRIPWIRE_MESSAGE}"
    )


def test_gold_tripwire_detector_catches_static_forms(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from synapse.experiments.swebench.contract import ExperimentArm as EA\n"
        "\n"
        "def bad(contract):\n"
        "    ExperimentArm.GOLD\n"
        "    ExperimentArm('GOLD')\n"
        "    getattr(ExperimentArm, 'GOLD')\n"
        "    ExperimentArm['GOLD']\n"
        "    contract.ExperimentArm.GOLD\n"
        "    contract.ExperimentArm('GOLD')\n"
        "    getattr(contract.ExperimentArm, 'GOLD')\n"
        "    contract.ExperimentArm['GOLD']\n",
        encoding="utf-8",
    )

    kinds = {violation.kind for violation in _scan_file(sample)}

    assert kinds == {
        "aliased ExperimentArm import",
        "ExperimentArm.GOLD",
        "ExperimentArm('GOLD')",
        "getattr(ExperimentArm, 'GOLD')",
        "ExperimentArm['GOLD']",
        "contract.ExperimentArm.GOLD",
        "contract.ExperimentArm('GOLD')",
        "getattr(contract.ExperimentArm, 'GOLD')",
        "contract.ExperimentArm['GOLD']",
    }


def test_gold_tripwire_detector_allows_enum_definition_and_dynamic_construction(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "class ExperimentArm:\n"
        "    GOLD = 'GOLD'\n"
        "\n"
        "def allowed(contract, value_from_manifest):\n"
        "    ExperimentArm(value_from_manifest)\n"
        "    contract.ExperimentArm(value_from_manifest)\n",
        encoding="utf-8",
    )

    assert _scan_file(sample) == []


def test_no_production_gold_construction_before_evidence_validation_rule() -> None:
    violations: list[GoldConstructionViolation] = []
    for path in _iter_scanned_python_files():
        violations.extend(_scan_file(path.relative_to(PROJECT_ROOT)))

    assert not violations, _render_failure(violations)
