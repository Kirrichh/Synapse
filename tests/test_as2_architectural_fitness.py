"""Shared AS2 architectural fitness tests for P0.6.17."""
from __future__ import annotations

from pathlib import Path

from tests.support.as2_boundary_guards import AS2BoundaryGuard, render_violations

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AS2_BOUNDARY_SCOPE = (
    "synapse/agent_snapshot_adapter.py",
    "synapse/agent_snapshot_bridge.py",
)
AS2_BRIDGE_CALL_SCOPE = ("synapse/agent_snapshot_bridge.py",)
AS2_RUNTIME_WIRING_SCOPE = ("synapse/runtime/as2_runtime_wiring.py",)
AS2_RUNTIME_WIRING_CALL_SCOPE = AS2_RUNTIME_WIRING_SCOPE
AS2_GATE_CONTROLLER_SCOPE = ("synapse/runtime/as2_gate_controller.py",)
AS2_AUDIT_SINK_SCOPE = ("synapse/runtime/as2_audit_sink.py",)
AS2_AUDIT_OUTBOX_SCOPE = ("synapse/runtime/as2_audit_outbox.py",)
AS2_IDEMPOTENCY_STORE_SCOPE = ("synapse/runtime/as2_idempotency_store.py",)
AS2_PROJECTION_HANDOFF_SCOPE = ("synapse/runtime/as2_projection_handoff.py",)
AS2_PROVIDER_PORTS_SCOPE = ("synapse/runtime/as2_provider_ports.py",)
AS2_PROVIDER_AGGREGATOR_SCOPE = ("synapse/runtime/as2_provider_aggregator.py",)
AS2_INTEGRATION_HARNESS_SCOPE = ("synapse/runtime/as2_integration_harness.py",)
AS2_PROVIDER_TEST_SUPPORT_SCOPE = (
    "tests/support/as2_provider_fakes.py",
    "tests/support/as2_prestage_provider_harness.py",
    "tests/support/as2_provider_routing.py",
)
ALLOWED_PROJECTION_CALLERS = AS2_PROJECTION_HANDOFF_SCOPE


def test_as2_boundary_modules_do_not_import_legacy_runtime_layers() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_BOUNDARY_SCOPE,
    )
    assert not violations, render_violations(violations)


def test_as2_bridge_does_not_call_projection_or_construct_snapshot() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_BRIDGE_CALL_SCOPE,
        call_check_globs=AS2_BRIDGE_CALL_SCOPE,
    )
    assert not violations, render_violations(violations)


def test_as2_runtime_wiring_skeleton_does_not_import_legacy_runtime_layers() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_RUNTIME_WIRING_SCOPE,
    )
    assert not violations, render_violations(violations)


def test_as2_runtime_wiring_skeleton_does_not_call_projection_or_construct_snapshot() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_RUNTIME_WIRING_SCOPE,
        call_check_globs=AS2_RUNTIME_WIRING_CALL_SCOPE,
    )
    assert not violations, render_violations(violations)


def test_as2_boundary_guard_detects_direct_forbidden_calls(tmp_path: Path) -> None:
    sample = tmp_path / "sample_direct.py"
    sample.write_text(
        "def bad():\n"
        "    AgentSnapshot()\n"
        "    project_validated_as2_inputs()\n",
        encoding="utf-8",
    )
    violations = AS2BoundaryGuard().check_file(sample, check_imports=False, check_calls=True)
    symbols = {violation.symbol for violation in violations}
    assert symbols == {"AgentSnapshot", "project_validated_as2_inputs"}


def test_as2_boundary_guard_detects_attribute_forbidden_calls(tmp_path: Path) -> None:
    sample = tmp_path / "sample_attribute.py"
    sample.write_text(
        "def bad(module):\n"
        "    module.AgentSnapshot()\n"
        "    module.project_validated_as2_inputs()\n",
        encoding="utf-8",
    )
    violations = AS2BoundaryGuard().check_file(sample, check_imports=False, check_calls=True)
    symbols = {violation.symbol for violation in violations}
    assert symbols == {"AgentSnapshot", "project_validated_as2_inputs"}


def test_as2_boundary_guard_detects_forbidden_imports(tmp_path: Path) -> None:
    sample = tmp_path / "sample_imports.py"
    sample.write_text(
        "import synapse.agent_runtime\n"
        "from synapse.environment import Environment\n"
        "from synapse.interpreter import Interpreter\n"
        "import synapse.actor_runtime.mailbox\n",
        encoding="utf-8",
    )
    violations = AS2BoundaryGuard().check_file(sample, check_imports=True, check_calls=False)
    symbols = {violation.symbol for violation in violations}
    assert symbols == {
        "synapse.agent_runtime",
        "synapse.environment",
        "synapse.interpreter",
        "synapse.actor_runtime.mailbox",
    }


def test_p0624_bridge_and_skeleton_do_not_import_projected_artifact_symbols() -> None:
    guard = AS2BoundaryGuard()
    violations = []
    for relative in (
        Path("synapse/agent_snapshot_bridge.py"),
        Path("synapse/runtime/as2_runtime_wiring.py"),
    ):
        violations.extend(
            guard.check_forbidden_imported_symbols(
                PROJECT_ROOT / relative,
                forbidden_symbols=frozenset({"AgentSnapshot", "AdapterDerivationRecordSkeleton"}),
            )
        )
    assert not violations, render_violations(violations)


def test_p0624_projection_call_is_allowed_only_from_tests_not_bridge_or_skeleton() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_BRIDGE_CALL_SCOPE + AS2_RUNTIME_WIRING_SCOPE,
        call_check_globs=AS2_BRIDGE_CALL_SCOPE + AS2_RUNTIME_WIRING_SCOPE,
    )
    assert not violations, render_violations(violations)


def test_p0625_gate_controller_and_audit_sink_do_not_import_legacy_runtime_layers() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_GATE_CONTROLLER_SCOPE + AS2_AUDIT_SINK_SCOPE,
    )
    assert not violations, render_violations(violations)


def test_p0625_gate_controller_does_not_call_projection_or_construct_snapshot() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_GATE_CONTROLLER_SCOPE,
        call_check_globs=AS2_GATE_CONTROLLER_SCOPE,
    )
    assert not violations, render_violations(violations)


def test_p0625_audit_sink_does_not_call_projection_or_construct_snapshot() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_AUDIT_SINK_SCOPE,
        call_check_globs=AS2_AUDIT_SINK_SCOPE,
    )
    assert not violations, render_violations(violations)


def test_p0625_gate_controller_and_audit_sink_do_not_import_projected_artifact_symbols() -> None:
    guard = AS2BoundaryGuard()
    violations = []
    for relative in (
        Path("synapse/runtime/as2_gate_controller.py"),
        Path("synapse/runtime/as2_audit_sink.py"),
    ):
        violations.extend(
            guard.check_forbidden_imported_symbols(
                PROJECT_ROOT / relative,
                forbidden_symbols=frozenset({"AgentSnapshot", "AdapterDerivationRecordSkeleton"}),
            )
        )
    assert not violations, render_violations(violations)


def test_p0625_import_direction_audit_sink_does_not_import_gate_controller() -> None:
    source = (PROJECT_ROOT / "synapse/runtime/as2_audit_sink.py").read_text(encoding="utf-8")
    assert "as2_gate_controller" not in source


def test_p0625_new_control_plane_modules_do_not_import_storage_or_io_drivers() -> None:
    guard = AS2BoundaryGuard(
        forbidden_imports=frozenset({
            "sqlite3",
            "redis",
            "boto3",
            "pymongo",
            "socket",
            "requests",
            "urllib",
            "pathlib",
        })
    )
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_GATE_CONTROLLER_SCOPE + AS2_AUDIT_SINK_SCOPE,
        call_check_globs=(),
    )
    assert not violations, render_violations(violations)


def test_p0625_no_direct_datetime_calls_in_audit_hash_construction() -> None:
    source = (PROJECT_ROOT / "synapse/runtime/as2_audit_sink.py").read_text(encoding="utf-8")
    assert "datetime.utcnow" not in source
    assert "datetime.now" not in source


def test_p0626_projection_call_is_allowed_only_from_handoff_module() -> None:
    guard = AS2BoundaryGuard()
    production_paths = [path for path in (PROJECT_ROOT / "synapse").rglob("*.py")]
    disallowed_paths = [
        path
        for path in production_paths
        if path.relative_to(PROJECT_ROOT).as_posix() not in set(ALLOWED_PROJECTION_CALLERS)
    ]
    violations = guard.check_files(disallowed_paths, check_imports=False, check_calls=True)
    projection_violations = [
        violation
        for violation in violations
        if violation.symbol == "project_validated_as2_inputs"
    ]
    assert not projection_violations, render_violations(projection_violations)


def test_p0626_projection_handoff_does_not_import_legacy_runtime_or_io_layers() -> None:
    guard = AS2BoundaryGuard(
        forbidden_imports=frozenset({
            "synapse.agent_runtime",
            "synapse.environment",
            "synapse.interpreter",
            "synapse.actor_runtime",
            "tests",
            "sqlite3",
            "redis",
            "boto3",
            "pymongo",
            "socket",
            "requests",
            "urllib",
            "pathlib",
        })
    )
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_PROJECTION_HANDOFF_SCOPE,
        call_check_globs=(),
    )
    assert not violations, render_violations(violations)


def test_p0626_projection_handoff_is_the_only_runtime_module_allowed_to_import_projection_symbols() -> None:
    guard = AS2BoundaryGuard()
    checked = (
        Path("synapse/agent_snapshot_bridge.py"),
        Path("synapse/runtime/as2_runtime_wiring.py"),
        Path("synapse/runtime/as2_gate_controller.py"),
    )
    violations = []
    for relative in checked:
        violations.extend(
            guard.check_forbidden_imported_symbols(
                PROJECT_ROOT / relative,
                forbidden_symbols=frozenset({"AgentSnapshot", "AdapterDerivationRecordSkeleton"}),
            )
        )
    assert not violations, render_violations(violations)


def test_p0627_runtime_wiring_may_import_handoff_but_not_projection_symbols() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_forbidden_imported_symbols(
        PROJECT_ROOT / "synapse/runtime/as2_runtime_wiring.py",
        forbidden_symbols=frozenset({
            "project_validated_as2_inputs",
            "AgentSnapshot",
            "AdapterDerivationRecordSkeleton",
        }),
    )
    assert not violations, render_violations(violations)


def test_p0627_projection_call_remains_allowed_only_from_handoff_module() -> None:
    guard = AS2BoundaryGuard()
    production_paths = [path for path in (PROJECT_ROOT / "synapse").rglob("*.py")]
    disallowed_paths = [
        path
        for path in production_paths
        if path.relative_to(PROJECT_ROOT).as_posix() not in set(ALLOWED_PROJECTION_CALLERS)
    ]
    violations = guard.check_files(disallowed_paths, check_imports=False, check_calls=True)
    projection_violations = [
        violation
        for violation in violations
        if violation.symbol == "project_validated_as2_inputs"
    ]
    assert not projection_violations, render_violations(projection_violations)


def test_p0628_provider_ports_do_not_import_legacy_runtime_or_io_layers() -> None:
    guard = AS2BoundaryGuard(
        forbidden_imports=frozenset({
            "synapse.agent_runtime",
            "synapse.environment",
            "synapse.interpreter",
            "synapse.actor_runtime",
            "tests",
            "sqlite3",
            "redis",
            "boto3",
            "pymongo",
            "socket",
            "requests",
            "urllib",
            "pathlib",
        })
    )
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_PROVIDER_PORTS_SCOPE,
        call_check_globs=(),
    )
    assert not violations, render_violations(violations)


def test_p0628_provider_ports_do_not_call_projection_or_construct_snapshot() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_PROVIDER_PORTS_SCOPE,
        call_check_globs=AS2_PROVIDER_PORTS_SCOPE,
    )
    assert not violations, render_violations(violations)


def test_p0628_provider_ports_do_not_import_projected_artifact_symbols() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_forbidden_imported_symbols(
        PROJECT_ROOT / "synapse/runtime/as2_provider_ports.py",
        forbidden_symbols=frozenset({
            "project_validated_as2_inputs",
            "AgentSnapshot",
            "AdapterDerivationRecordSkeleton",
        }),
    )
    assert not violations, render_violations(violations)


def test_p0628_provider_ports_are_contracts_not_concrete_adapters() -> None:
    source = (PROJECT_ROOT / "synapse/runtime/as2_provider_ports.py").read_text(encoding="utf-8")
    forbidden_terms = {
        "requests.",
        "urllib.",
        "socket.",
        "sqlite3.",
        "boto3.",
        "redis.",
        "open(",
    }
    found = {term for term in forbidden_terms if term in source}
    assert not found


def test_p0629_provider_test_support_fakes_and_harness_do_not_import_legacy_runtime_or_io_layers() -> None:
    guard = AS2BoundaryGuard(
        forbidden_imports=frozenset({
            "synapse.agent_runtime",
            "synapse.environment",
            "synapse.interpreter",
            "synapse.actor_runtime",
            "sqlite3",
            "redis",
            "boto3",
            "pymongo",
            "socket",
            "requests",
            "urllib",
        })
    )
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_PROVIDER_TEST_SUPPORT_SCOPE,
        call_check_globs=(),
    )
    assert not violations, render_violations(violations)


def test_p0629_provider_test_support_fakes_and_harness_do_not_call_projection_or_construct_snapshot() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_PROVIDER_TEST_SUPPORT_SCOPE,
        call_check_globs=AS2_PROVIDER_TEST_SUPPORT_SCOPE,
    )
    assert not violations, render_violations(violations)


def test_p0629_provider_test_support_fakes_and_harness_do_not_import_projected_artifact_symbols() -> None:
    guard = AS2BoundaryGuard()
    violations = []
    for relative in AS2_PROVIDER_TEST_SUPPORT_SCOPE:
        violations.extend(
            guard.check_forbidden_imported_symbols(
                PROJECT_ROOT / relative,
                forbidden_symbols=frozenset({
                    "project_validated_as2_inputs",
                    "AgentSnapshot",
                    "AdapterDerivationRecordSkeleton",
                }),
            )
        )
    assert not violations, render_violations(violations)

def test_p0635_audit_outbox_does_not_import_legacy_runtime_or_io_layers() -> None:
    guard = AS2BoundaryGuard(
        forbidden_imports=frozenset({
            "synapse.agent_runtime",
            "synapse.environment",
            "synapse.interpreter",
            "synapse.actor_runtime",
            "tests",
            "sqlite3",
            "psycopg2",
            "redis",
            "boto3",
            "pymongo",
            "kafka",
            "socket",
            "requests",
            "urllib",
            "pathlib",
        })
    )
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_AUDIT_OUTBOX_SCOPE,
        call_check_globs=(),
    )
    assert not violations, render_violations(violations)


def test_p0635_audit_outbox_does_not_call_projection_or_construct_snapshot() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_AUDIT_OUTBOX_SCOPE,
        call_check_globs=AS2_AUDIT_OUTBOX_SCOPE,
    )
    assert not violations, render_violations(violations)


def test_p0635_audit_outbox_does_not_import_projected_artifact_or_provider_symbols() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_forbidden_imported_symbols(
        PROJECT_ROOT / "synapse/runtime/as2_audit_outbox.py",
        forbidden_symbols=frozenset({
            "AgentSnapshot",
            "AdapterDerivationRecordSkeleton",
            "project_validated_as2_inputs",
            "HostIdentityProviderPort",
            "HostDefinitionProviderPort",
            "StaticModelRegistryProviderPort",
            "MemoryReferenceProviderPort",
            "CapabilityGrantProviderPort",
            "ModelSelectionProviderPort",
        }),
    )
    assert not violations, render_violations(violations)



def test_p0636_idempotency_store_does_not_import_legacy_runtime_or_io_layers() -> None:
    guard = AS2BoundaryGuard(
        forbidden_imports=frozenset({
            "synapse.agent_runtime",
            "synapse.environment",
            "synapse.interpreter",
            "synapse.actor_runtime",
            "tests",
            "sqlite3",
            "psycopg2",
            "redis",
            "boto3",
            "pymongo",
            "kafka",
            "socket",
            "requests",
            "urllib",
            "pathlib",
        })
    )
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_IDEMPOTENCY_STORE_SCOPE,
        call_check_globs=(),
    )
    assert not violations, render_violations(violations)


def test_p0636_idempotency_store_does_not_call_projection_or_construct_snapshot() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_IDEMPOTENCY_STORE_SCOPE,
        call_check_globs=AS2_IDEMPOTENCY_STORE_SCOPE,
    )
    assert not violations, render_violations(violations)


def test_p0636_idempotency_store_does_not_import_projected_artifact_or_provider_symbols() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_forbidden_imported_symbols(
        PROJECT_ROOT / "synapse/runtime/as2_idempotency_store.py",
        forbidden_symbols=frozenset({
            "AgentSnapshot",
            "AdapterDerivationRecordSkeleton",
            "project_validated_as2_inputs",
            "HostIdentityProviderPort",
            "HostDefinitionProviderPort",
            "StaticModelRegistryProviderPort",
            "MemoryReferenceProviderPort",
            "CapabilityGrantProviderPort",
            "ModelSelectionProviderPort",
        }),
    )
    assert not violations, render_violations(violations)


def test_p0636_idempotency_store_uses_injected_clock_not_direct_time_calls() -> None:
    source = (PROJECT_ROOT / "synapse/runtime/as2_idempotency_store.py").read_text(encoding="utf-8")
    assert "time.time(" not in source
    assert "from time import time as _default_clock" in source



def test_p0637_provider_aggregator_does_not_import_legacy_runtime_or_io_layers() -> None:
    guard = AS2BoundaryGuard(
        forbidden_imports=frozenset({
            "synapse.agent_runtime",
            "synapse.environment",
            "synapse.interpreter",
            "synapse.actor_runtime",
            "tests",
            "sqlite3",
            "psycopg2",
            "psycopg",
            "redis",
            "boto3",
            "pymongo",
            "kafka",
            "nats",
            "socket",
            "requests",
            "httpx",
            "urllib",
            "pathlib",
            "time",
        })
    )
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_PROVIDER_AGGREGATOR_SCOPE,
        call_check_globs=(),
    )
    assert not violations, render_violations(violations)


def test_p0637_provider_aggregator_does_not_call_projection_or_construct_snapshot() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_PROVIDER_AGGREGATOR_SCOPE,
        call_check_globs=AS2_PROVIDER_AGGREGATOR_SCOPE,
    )
    assert not violations, render_violations(violations)


def test_p0637_provider_aggregator_does_not_import_projected_artifact_or_storage_symbols() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_forbidden_imported_symbols(
        PROJECT_ROOT / "synapse/runtime/as2_provider_aggregator.py",
        forbidden_symbols=frozenset({
            "AgentSnapshot",
            "AdapterDerivationRecordSkeleton",
            "project_validated_as2_inputs",
            "prepare_as2_inputs_from_host_prestage",
            "InMemoryIdempotencyStore",
            "IdempotencyKey",
            "InMemoryOutboxAuditSink",
        }),
    )
    assert not violations, render_violations(violations)


def test_p0637_provider_aggregator_has_no_direct_storage_projection_or_time_terms() -> None:
    source = (PROJECT_ROOT / "synapse/runtime/as2_provider_aggregator.py").read_text(encoding="utf-8")
    forbidden_terms = {
        "as2_idempotency_store",
        "as2_projection_handoff",
        "as2_runtime_wiring",
        "project_validated_as2_inputs",
        "prepare_as2_inputs_from_host_prestage",
        "InMemoryIdempotencyStore",
        "AgentSnapshot",
        "open(",
        "Path(",
        "time.time(",
    }
    found = {term for term in forbidden_terms if term in source}
    assert not found



def test_p0638_integration_harness_does_not_import_legacy_runtime_or_io_layers() -> None:
    guard = AS2BoundaryGuard(
        forbidden_imports=frozenset({
            "synapse.agent_runtime",
            "synapse.environment",
            "synapse.interpreter",
            "synapse.actor_runtime",
            "tests",
            "sqlite3",
            "psycopg2",
            "psycopg",
            "redis",
            "boto3",
            "pymongo",
            "kafka",
            "nats",
            "socket",
            "requests",
            "httpx",
            "urllib",
            "pathlib",
            "time",
            "threading",
            "asyncio",
            "concurrent",
            "multiprocessing",
        })
    )
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_INTEGRATION_HARNESS_SCOPE,
        call_check_globs=(),
    )
    assert not violations, render_violations(violations)


def test_p0638_integration_harness_does_not_call_projection_core_or_construct_snapshot() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_package(
        PROJECT_ROOT,
        include_globs=AS2_INTEGRATION_HARNESS_SCOPE,
        call_check_globs=AS2_INTEGRATION_HARNESS_SCOPE,
    )
    assert not violations, render_violations(violations)


def test_p0638_integration_harness_uses_only_approved_integration_import_symbols() -> None:
    guard = AS2BoundaryGuard()
    violations = guard.check_forbidden_imported_symbols(
        PROJECT_ROOT / "synapse/runtime/as2_integration_harness.py",
        forbidden_symbols=frozenset({
            "AgentSnapshot",
            "AdapterDerivationRecordSkeleton",
            "project_validated_as2_inputs",
            "AS2GateControllerSkeleton",
            "AS2GateController",
            "InMemoryOutboxAuditSink",
            "AS2AuditSink",
            "NoOpAuditSink",
        }),
    )
    assert not violations, render_violations(violations)


def test_p0638_integration_harness_has_no_direct_runtime_wiring_time_or_real_io_terms() -> None:
    source = (PROJECT_ROOT / "synapse/runtime/as2_integration_harness.py").read_text(encoding="utf-8")
    forbidden_terms = {
        "as2_runtime_wiring",
        "project_validated_as2_inputs",
        "AgentSnapshot",
        "AgentRuntime",
        "Environment",
        "interpreter",
        "actor_runtime",
        "sqlite3",
        "redis",
        "boto3",
        "requests",
        "httpx",
        "socket",
        "Path(",
        "open(",
        "time.time",
        "threading",
        "asyncio",
        "concurrent.futures",
        "multiprocessing",
        "gate_controller",
        "handle_wiring_outcome",
        "handle_provider_failure",
    }
    found = {term for term in forbidden_terms if term in source}
    assert not found
