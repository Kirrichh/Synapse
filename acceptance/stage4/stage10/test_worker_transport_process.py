from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

from synapse.experiments.gold.stage10.delivery_verification import (
    DeliveryFailureCode,
    DeliveryViolation,
)
from synapse.experiments.gold.stage10_composition import (
    create_stage10_production_composition,
    require_stage10_production_composition,
)
from synapse.experiments.gold.stage10.worker_transport import (
    WorkerCandidateStatus,
    WorkerDeliveryStatus,
    WorkerInvocation,
)
from synapse.worker.mini_adapter import (
    MiniAdapterConfig,
    MiniWorkerTransport,
    run_mini_worker_invocation,
)
from tests.gold_store_fence import fence_for


def _invocation(
    payload: str,
    *,
    allowed_scope: tuple[str, ...] = ("synapse/experiments/gold/stage10",),
) -> WorkerInvocation:
    payload_bytes = payload.encode("utf-8")
    return WorkerInvocation(
        invocation_id="inv_" + "1" * 64,
        attempt_id="transport-attempt",
        context_id="ctx_" + "2" * 64,
        payload_text=payload,
        payload_sha256=hashlib.sha256(payload_bytes).hexdigest(),
        payload_byte_length=len(payload_bytes),
        envelope_sha256="3" * 64,
        allowed_scope=allowed_scope,
        capabilities=("repository.edit",),
    )


def _git_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "acceptance@example.invalid"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Stage10 Acceptance"],
        cwd=path,
        check=True,
    )


def test_typed_context_crosses_adapter_transport_and_real_subprocess(
    stage10_delivery_world,
) -> None:
    world = stage10_delivery_world
    invocation = world.dispatch.invocation
    result = world.dispatch.worker_result

    assert (world.worker_worktree / ".git").is_dir()
    assert world.transport_proof_path.read_text(encoding="utf-8") == invocation.payload_sha256
    assert invocation.payload_text == world.context.delivery_envelope.prompt_text
    assert result.status is WorkerCandidateStatus.NO_PATCH
    assert result.delivery_evidence.status is WorkerDeliveryStatus.PROCESS_STARTED
    assert result.delivery_evidence.payload_sha256 == invocation.payload_sha256
    assert result.touched_files == ()
    assert result.diagnostics["tracked_files"] == ()
    assert result.diagnostics["untracked_files"] == ()
    assert result.diagnostics["scope_violations"] == ()
    assert invocation.payload_text not in repr(result.diagnostics)


def test_canonical_dispatch_refuses_non_repository_before_worker_process(
    stage10_delivery_world,
    tmp_path: Path,
) -> None:
    world = stage10_delivery_world
    marker = tmp_path / "worker-process-started"
    worker = tmp_path / "must-not-start.py"
    worker.write_text(
        "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text("
        "'started', encoding='utf-8')",
        encoding="utf-8",
    )
    store_root = tmp_path / "record-store"
    store_root.mkdir()
    composition = create_stage10_production_composition(
        record_root=store_root / "records",
        mutation_fence=fence_for(store_root),
        mini_config=MiniAdapterConfig(
            command=(sys.executable, str(worker), str(marker)),
            timeout_seconds=30,
            max_steps=1,
            cost_limit=0,
        ),
    )
    non_repository = tmp_path / "not-a-repository"
    non_repository.mkdir()
    with pytest.raises(DeliveryViolation) as raised:
        composition.worker_adapter.dispatch(
            worktree_path=non_repository,
            context=world.context,
            persistence=world.context_persistence,
            plan_persistence=world.plan_persistence,
            authorization=world.authorization,
        )

    assert raised.value.failure_code is DeliveryFailureCode.NOT_DISPATCHED
    assert not marker.exists()
    assert not tuple(non_repository.glob(".synapse-mini-*.trajectory.json"))


def test_canonical_composition_refuses_rewired_component_bindings(tmp_path: Path) -> None:
    configured = MiniAdapterConfig(command=("mini",), timeout_seconds=30, max_steps=1)

    def composition_for(case: int):
        authority_root = tmp_path / f"authority-{case}"
        authority_root.mkdir()
        return create_stage10_production_composition(
            record_root=authority_root / "records",
            mutation_fence=fence_for(authority_root),
            mini_config=configured,
        )

    rewired = composition_for(0)
    object.__setattr__(rewired.record_store, "_root", tmp_path / "other-records")
    with pytest.raises(TypeError):
        require_stage10_production_composition(rewired)

    rewired = composition_for(1)
    object.__setattr__(rewired.record_store, "_coordinator_id", "other-coordinator")
    with pytest.raises(TypeError):
        require_stage10_production_composition(rewired)

    rewired = composition_for(2)
    object.__setattr__(rewired.worker_transport, "_config", MiniAdapterConfig(command=("other",)))
    with pytest.raises(TypeError):
        require_stage10_production_composition(rewired)

    rewired = composition_for(3)
    object.__setattr__(rewired.worker_adapter, "_transport", MiniWorkerTransport(config=configured))
    with pytest.raises(TypeError):
        require_stage10_production_composition(rewired)


def test_transport_refuses_unrepresentable_payload_before_process_start(
    tmp_path: Path,
) -> None:
    called = False

    def must_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("oversized payload reached subprocess runner")

    invocation = _invocation("x" * 40_000)
    result = run_mini_worker_invocation(
        tmp_path,
        invocation,
        config=MiniAdapterConfig(command=("mini",), timeout_seconds=30, max_steps=1),
        runner=must_not_run,
    )

    assert called is False
    assert result.status is WorkerCandidateStatus.ERROR
    assert result.delivery_evidence.status is WorkerDeliveryStatus.NOT_DISPATCHED
    assert result.report.failure_reason == "worker_payload_exceeds_transport_limit"
    assert not tuple(tmp_path.glob(".synapse-mini-*.trajectory.json"))


def test_repository_scope_entry_covers_descendant_worker_output(tmp_path: Path) -> None:
    _git_repository(tmp_path)
    worker = tmp_path / "worker-probe.py"
    worker.write_text(
        "import pathlib\n"
        "target = pathlib.Path('synapse/experiments/gold/stage10/generated.py')\n"
        "target.parent.mkdir(parents=True, exist_ok=True)\n"
        "target.write_text('candidate = True\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "worker-probe.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "probe"], cwd=tmp_path, check=True)

    result = run_mini_worker_invocation(
        tmp_path,
        _invocation("typed context"),
        config=MiniAdapterConfig(
            command=(sys.executable, str(worker)),
            timeout_seconds=30,
            max_steps=1,
            cost_limit=0,
        ),
    )

    assert result.delivery_evidence.status is WorkerDeliveryStatus.PROCESS_STARTED
    assert result.touched_files == ("synapse/experiments/gold/stage10/generated.py",)
    assert result.diagnostics["scope_violations"] == ()


def test_typed_invocation_reports_out_of_scope_subprocess_output(
    tmp_path: Path,
) -> None:
    _git_repository(tmp_path)
    worker = tmp_path / "worker-probe.py"
    worker.write_text(
        "import pathlib; "
        "target = pathlib.Path('synapse/experiments/gold/stage100/generated.py'); "
        "target.parent.mkdir(parents=True, exist_ok=True); "
        "target.write_text('candidate = True', encoding='utf-8')",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "worker-probe.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "probe"], cwd=tmp_path, check=True)

    result = run_mini_worker_invocation(
        tmp_path,
        _invocation("typed context"),
        config=MiniAdapterConfig(
            command=(sys.executable, str(worker)),
            timeout_seconds=30,
            max_steps=1,
            cost_limit=0,
        ),
    )

    outside = "synapse/experiments/gold/stage100/generated.py"
    assert result.delivery_evidence.status is WorkerDeliveryStatus.PROCESS_STARTED
    assert result.status is WorkerCandidateStatus.PROPOSED_PATCH
    assert result.touched_files == (outside,)
    assert result.diagnostics["scope_violations"] == (outside,)


def test_repository_observation_failure_is_not_reported_as_no_patch(
    tmp_path: Path,
) -> None:
    _git_repository(tmp_path)
    worker = tmp_path / "worker-probe.py"
    worker.write_text(
        "import pathlib; pathlib.Path('.git').rename('.git-hidden')",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "worker-probe.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "probe"], cwd=tmp_path, check=True)

    result = run_mini_worker_invocation(
        tmp_path,
        _invocation("typed context"),
        config=MiniAdapterConfig(
            command=(sys.executable, str(worker)),
            timeout_seconds=30,
            max_steps=1,
            cost_limit=0,
        ),
    )

    assert result.status is WorkerCandidateStatus.ERROR
    assert result.delivery_evidence.status is WorkerDeliveryStatus.PROCESS_STARTED
    assert result.report.failure_reason == "worker_repository_observation_failed"
    assert result.diagnostics["repository_observation"] == "FAILED"
    assert result.touched_files == ()
