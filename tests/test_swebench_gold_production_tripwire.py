from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("synapse", "personal_slice", "scripts", "examples")
GOLD_WRITER_ALLOWLIST = (
    Path("synapse/experiments/swebench/gold_attempt_writer.py"),
)
FITNESS_MESSAGE = (
    "Production GOLD construction detected outside GoldAttemptWriter. "
    "Executable GOLD success must be written through the evidence-validating writer."
)
BYPASS_MARKERS = ("disable", "skip", "bypass", "unsafe")


@dataclass(frozen=True)
class GoldProductionViolation:
    path: Path
    lineno: int
    kind: str

    def render(self) -> str:
        return f"{self.path}:{self.lineno}: forbidden {self.kind}"


def _relative_project_path(path: Path) -> Path:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve())
    except (OSError, RuntimeError, ValueError):
        return path


def _is_allowlisted_writer(path: Path) -> bool:
    return Path(_relative_project_path(path).as_posix()) in GOLD_WRITER_ALLOWLIST


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


def _scan_import_from(path: Path, node: ast.ImportFrom) -> GoldProductionViolation | None:
    for alias in node.names:
        if alias.name == "ExperimentArm" and alias.asname is not None:
            return GoldProductionViolation(path, node.lineno, "aliased ExperimentArm import")
    return None


def _scan_attribute(path: Path, node: ast.Attribute) -> GoldProductionViolation | None:
    if node.attr == "GOLD" and _is_experiment_arm_expr(node.value):
        return GoldProductionViolation(
            path,
            node.lineno,
            _node_source(node, "ExperimentArm.GOLD"),
        )
    return None


def _scan_call(path: Path, node: ast.Call) -> GoldProductionViolation | None:
    if _is_experiment_arm_expr(node.func) and any(_is_gold_constant(arg) for arg in node.args):
        return GoldProductionViolation(
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
        return GoldProductionViolation(
            path,
            node.lineno,
            _node_source(node, 'getattr(ExperimentArm, "GOLD")'),
        )
    return None


def _scan_subscript(path: Path, node: ast.Subscript) -> GoldProductionViolation | None:
    if _is_experiment_arm_expr(node.value) and _is_gold_constant(node.slice):
        return GoldProductionViolation(
            path,
            node.lineno,
            _node_source(node, 'ExperimentArm["GOLD"]'),
        )
    return None


def _scan_raw_gold_dict(path: Path, node: ast.Dict) -> GoldProductionViolation | None:
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and key.value == "arm" and _is_gold_constant(value):
            return GoldProductionViolation(path, node.lineno, '{"arm": "GOLD"}')
    return None


def _scan_raw_gold_keyword(path: Path, node: ast.Call) -> GoldProductionViolation | None:
    for keyword in node.keywords:
        if keyword.arg == "arm" and _is_gold_constant(keyword.value):
            return GoldProductionViolation(path, keyword.value.lineno, 'arm="GOLD"')
    return None


def _scan_gold_production_forms(path: Path, tree: ast.AST) -> list[GoldProductionViolation]:
    if _is_allowlisted_writer(path):
        return []
    violations: list[GoldProductionViolation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            violation = _scan_import_from(path, node)
        elif isinstance(node, ast.Attribute):
            violation = _scan_attribute(path, node)
        elif isinstance(node, ast.Call):
            violation = _scan_call(path, node) or _scan_raw_gold_keyword(path, node)
        elif isinstance(node, ast.Subscript):
            violation = _scan_subscript(path, node)
        elif isinstance(node, ast.Dict):
            violation = _scan_raw_gold_dict(path, node)
        else:
            violation = None
        if violation is not None:
            violations.append(violation)
    return violations


def _contains_validate_gold_evidence_call(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "validate_gold_evidence":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "validate_gold_evidence":
            return True
    return False


def _references_gold_evidence(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "GoldEvidence":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "GoldEvidence":
            return True
    return False


def _writer_bypass_flag_violations(path: Path, tree: ast.AST) -> list[GoldProductionViolation]:
    violations: list[GoldProductionViolation] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
            if node.args.vararg is not None:
                arguments.append(node.args.vararg)
            if node.args.kwarg is not None:
                arguments.append(node.args.kwarg)
            for arg in arguments:
                if any(marker in arg.arg for marker in BYPASS_MARKERS):
                    violations.append(
                        GoldProductionViolation(path, arg.lineno, f"validation bypass flag {arg.arg}")
                    )
        elif isinstance(node, ast.Name) and any(marker in node.id for marker in BYPASS_MARKERS):
            violations.append(
                GoldProductionViolation(path, node.lineno, f"validation bypass name {node.id}")
            )
    return violations


def _scan_writer_contract(path: Path, tree: ast.AST) -> list[GoldProductionViolation]:
    if not _is_allowlisted_writer(path):
        return []
    violations: list[GoldProductionViolation] = []
    if not _contains_validate_gold_evidence_call(tree):
        violations.append(
            GoldProductionViolation(path, 1, "GoldAttemptWriter must call validate_gold_evidence")
        )
    if not _references_gold_evidence(tree):
        violations.append(
            GoldProductionViolation(path, 1, "GoldAttemptWriter must reference GoldEvidence")
        )
    violations.extend(_writer_bypass_flag_violations(path, tree))
    return violations


def _scan_source(
    path: Path,
    source: str,
    *,
    enforce_writer_contract: bool = False,
) -> list[GoldProductionViolation]:
    tree = ast.parse(source, filename=str(path))
    violations = _scan_gold_production_forms(path, tree)
    if enforce_writer_contract:
        violations.extend(_scan_writer_contract(path, tree))
    return violations


def _scan_file(path: Path) -> list[GoldProductionViolation]:
    source = path.read_text(encoding="utf-8", errors="replace")
    return _scan_source(path, source, enforce_writer_contract=True)


def _iter_scanned_python_files() -> Iterable[Path]:
    for root_name in SCAN_ROOTS:
        root = PROJECT_ROOT / root_name
        if not root.exists():
            continue
        yield from sorted(path for path in root.rglob("*.py") if path.is_file())


def _render_failure(violations: list[GoldProductionViolation]) -> str:
    rendered = "\n".join(violation.render() for violation in violations)
    return f"{FITNESS_MESSAGE}\n{rendered}"


def test_gold_fitness_detector_catches_static_forms(tmp_path: Path) -> None:
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


def test_gold_fitness_detector_allows_enum_definition_and_dynamic_construction(tmp_path: Path) -> None:
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


def test_gold_fitness_allowlist_accepts_only_gold_attempt_writer() -> None:
    source = (
        "from synapse.experiments.swebench.contract import ExperimentArm as EA\n"
        "from synapse.experiments.swebench.gold_evidence import GoldEvidence, validate_gold_evidence\n"
        "\n"
        "def write(gold_evidence: GoldEvidence):\n"
        "    ExperimentArm.GOLD\n"
        "    return validate_gold_evidence(gold_evidence, repo_root='.')\n"
    )

    assert _scan_source(Path("synapse/experiments/swebench/gold_attempt_writer.py"), source) == []
    violations = _scan_source(Path("synapse/experiments/swebench/not_allowed.py"), source)
    assert {violation.kind for violation in violations} >= {
        "aliased ExperimentArm import",
        "ExperimentArm.GOLD",
    }


def test_gold_fitness_requires_writer_to_call_validate_gold_evidence() -> None:
    source = (
        "from synapse.experiments.swebench.contract import ExperimentArm\n"
        "from synapse.experiments.swebench.gold_evidence import GoldEvidence, validate_gold_evidence\n"
        "\n"
        "def write(gold_evidence: GoldEvidence):\n"
        "    return ExperimentArm.GOLD.value\n"
    )

    violations = _scan_source(
        Path("synapse/experiments/swebench/gold_attempt_writer.py"),
        source,
        enforce_writer_contract=True,
    )

    assert "GoldAttemptWriter must call validate_gold_evidence" in {
        violation.kind for violation in violations
    }


def test_gold_fitness_forbids_writer_validation_bypass_flags() -> None:
    source = (
        "from synapse.experiments.swebench.contract import ExperimentArm\n"
        "from synapse.experiments.swebench.gold_evidence import GoldEvidence, validate_gold_evidence\n"
        "\n"
        "def write(gold_evidence: GoldEvidence, skip_validation=False):\n"
        "    return validate_gold_evidence(gold_evidence, repo_root='.')\n"
    )

    violations = _scan_source(
        Path("synapse/experiments/swebench/gold_attempt_writer.py"),
        source,
        enforce_writer_contract=True,
    )

    assert "validation bypass flag skip_validation" in {
        violation.kind for violation in violations
    }


def test_gold_fitness_detects_raw_arm_gold_writer_bypass() -> None:
    source = 'record = {"arm": "GOLD", "status": "GOLD_APPLIED_WITH_EVIDENCE"}\n'

    violations = _scan_source(Path("synapse/experiments/swebench/raw_writer.py"), source)

    assert {violation.kind for violation in violations} == {'{"arm": "GOLD"}'}
    assert _scan_source(Path("synapse/experiments/swebench/gold_attempt_writer.py"), source) == []


def test_gold_fitness_scan_roots_are_preserved() -> None:
    assert SCAN_ROOTS == ("synapse", "personal_slice", "scripts", "examples")


def test_gold_fitness_v2_production_surface() -> None:
    assert GOLD_WRITER_ALLOWLIST == (
        Path("synapse/experiments/swebench/gold_attempt_writer.py"),
    )
    violations: list[GoldProductionViolation] = []
    for path in _iter_scanned_python_files():
        violations.extend(_scan_file(path))

    assert not violations, _render_failure(violations)
