"""Настоящий граф полномочий точки использования — для приёмок Stage 9.

Patch 9 доставляет знание в воспроизведение, поэтому §22 требует, чтобы путь
пересекал consumption-барьер *в момент использования*. Барьер — это
``admit_for_use_now``, а он отказывает всему, кроме реального
``ProductionAuthorityBinding``: те же файловые хранилища, тот же координатор,
настоящие Stage 3 записи по каждому субъекту. Подделать его нечем — и это цель,
а не неудобство.

Отсюда три факта, определяющие форму этого модуля.

**Мир строится один раз на процесс.** Публикация поведения, три истории
полномочий, снимок и зафиксированная граница стоят около девяти секунд. Мир
неизменяем в той части, которую приёмки читают, а всё, что они дописывают, —
append-only, поэтому разделяемый мир не делает тесты зависимыми друг от друга.

**Одна попытка точки использования допускает ровно один раз.** Запись Stage 3
ревалидации детерминирована, а история совместимости append-only: второй
``admit_for_use_now`` на том же binding упирается в RECORD_DUPLICATE. Поэтому
``admission_request`` каждый раз собирает *новую* попытку — свежие Stage 3
доказательства под сдвинутыми часами — и это единственный способ получить второе
допущение.

**Одно допущение запечатывает сколько угодно журналов активностей.**
``seal_activity_ledger`` не допускает сам, он принимает уже отчеканенное
``CurrentAdmittedKnowledge``. Приёмке активностей поэтому хватает одного
допущения на весь модуль.

Ничего здесь не решает и ничего не подменяет: все объекты — production, а модуль
лишь собирает их в том порядке, в каком их собрал бы вызывающий.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import tempfile

from synapse.experiments.gold import gate_findings as GF
from synapse.experiments.gold import point_of_use as P
from synapse.experiments.gold.compatibility import (
    configure_compatibility_evaluator,
    create_compatibility_context,
    evaluate_compatibility,
    evaluate_conflicts,
    revalidate_before_loading,
)
from synapse.experiments.gold.compatibility_store import (
    CompatibilityStoreFailureCode,
    CompatibilityStoreViolation,
)

_WORLDS: dict[str, object] = {}
_ATTEMPTS: dict[str, int] = {}


def _core_key(core) -> str:
    import hashlib
    import json

    if core is None:
        return "default"
    return hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def world(core=None):
    """Граф полномочий точки использования для одного опубликованного поведения.

    Кэшируется по ядру поведения: §22 допускает *опубликованный* субъект, а
    поведение с другим replay-контрактом — другой субъект, у которого нет ни
    дескриптора, ни аттестации в чужом мире. Приёмка, которой нужно несколько
    поведений, получает по миру на каждое, и каждый строится один раз.
    """

    key = _core_key(core)
    if key not in _WORLDS:
        from tests.test_stage4_gold_consumption_evidence import production_point_of_use_case

        root = Path(tempfile.mkdtemp(prefix="stage9-point-of-use-"))
        _WORLDS[key] = production_point_of_use_case(root / "case", behavior_core=core)
    return _WORLDS[key]


def subject_ref(core=None):
    """Ссылка библиотечного субъекта, которую допустили четыре шлюза."""

    return world(core).subject


def consumer_context_ref(core=None):
    return world(core).context_ref


def boundary_ref(core=None):
    return world(core).boundary_ref


def behavior_unit(core=None):
    """Опубликованное поведение, которое эта ссылка называет."""

    from tests.test_stage4_gold_compatibility import _behavior

    unit, _blob, _manifest = _behavior(core_payload=core)
    return unit


def _next_binding(case, moment):
    """Свежие Stage 3 доказательства и новый production binding под ними.

    Записи-предпосылки (контекст, решение, скан, stage-2) у второй попытки могут
    совпасть с первой байт в байт — тогда append-only история справедливо
    отказывает в дубликате, и это не ошибка: запись уже зафиксирована и именно
    она нужна. Отказывает история, а не тест: дубликат распознаётся по её
    собственному коду отказа, а не по тексту.
    """

    ambient = case.world
    binding = case.binding
    evaluator = configure_compatibility_evaluator(
        authority_handle=ambient.handle,
        declaration=ambient.declaration,
        evaluator_component_id=ambient.declaration.evaluator_component_id,
        evaluator_component_version=ambient.declaration.evaluator_component_version,
        trusted_clock=lambda: moment,
        platform_observation_provider=ambient.observation_provider,
        library=ambient.library,
        lifecycle_store=binding.lifecycle_store,
        attestation_store=binding.attestation_store,
        taint_store=binding.taint_store,
        evidence_resolver=lambda descriptor: ambient.catalog[descriptor.descriptor_id.value],
        binding_repo_root=ambient.root,
        conflict_assessor=ambient.evaluator._conflict_assessor,
        retriever_actor=ambient.evaluator.retriever_actor,
        consumer_actor=ambient.evaluator.consumer_actor,
        score_provider_actor=ambient.evaluator.score_provider_actor,
    )
    context = create_compatibility_context(
        evaluator=evaluator,
        authority_handle=ambient.handle,
        observation=ambient.observation,
        library_snapshot=ambient.library.current_snapshot().snapshot,
        lifecycle_snapshot=binding.lifecycle_store.snapshot(),
        consumer_actor=evaluator.consumer_actor,
    )
    decision = evaluate_compatibility(
        evaluator=evaluator,
        context=context,
        descriptor=ambient.descriptor,
        index_entry=ambient.entry,
    )
    scan = evaluate_conflicts(
        evaluator=evaluator,
        context=context,
        decisions=(decision,),
        descriptors=(ambient.descriptor,),
        considered_index_entries=(ambient.entry,),
        proposals=(),
    )
    stage2 = revalidate_before_loading(
        evaluator=evaluator,
        context=context,
        descriptor=ambient.descriptor,
        original_decision=decision,
    )
    history = binding.compatibility_history
    for record in (context, decision.evidence, decision, scan, stage2):
        try:
            history.append_record(record, expected_parent_anchor=history.current_anchor())
        except CompatibilityStoreViolation as exc:
            if exc.failure_code is not CompatibilityStoreFailureCode.RECORD_DUPLICATE:
                raise
    evidence_binding = GF.bind_consumption_evidence(
        descriptor=ambient.descriptor,
        original_decision=decision,
        before_loading=stage2,
        conflict_scan=scan,
    )
    probe = GF.configured_durable_revalidation_probe(
        evaluator=evaluator,
        context=context,
        bindings=(evidence_binding,),
        compatibility_history=history,
    )
    return P.create_production_authority_binding(
        controller=binding.controller,
        lifecycle_store=binding.lifecycle_store,
        attestation_store=binding.attestation_store,
        taint_store=binding.taint_store,
        admission_journal=binding.admission_journal,
        admission_causal_history=binding.admission_causal_history,
        compatibility_history=history,
        compatibility_probe=probe,
        knowledge_store=binding.knowledge_store,
        snapshot_attempt_id=binding.snapshot_attempt_id,
        snapshot_evaluator_declaration=binding.snapshot_evaluator_declaration,
        snapshot_actor_set=binding.snapshot_actor_set,
        snapshot_independence_proof=binding.snapshot_independence_proof,
    )


def admission_request(core=None) -> P.PointOfUseAdmissionRequest:
    """Новая попытка точки использования, годная ровно для одного допущения."""

    key = _core_key(core)
    case = world(core)
    _ATTEMPTS[key] = _ATTEMPTS.get(key, 0) + 1
    moment = case.now[0] + timedelta(seconds=_ATTEMPTS[key])
    binding = _next_binding(case, moment)
    case.now[0] = moment
    return P.create_point_of_use_admission_request(
        handle=case.handle,
        binding=binding,
        chain=case.chain,
        evidence=case.evidence,
        entitlements=case.entitlements,
        requested=case.requested,
    )


_ADMITTED: dict[str, object] = {}


def admit(request: P.PointOfUseAdmissionRequest):
    return P.admit_for_use_now(
        request.handle,
        binding=request.binding,
        chain=request.chain,
        evidence=request.evidence,
        entitlements=request.entitlements,
        requested=request.requested,
    )


def admitted_knowledge(core=None):
    """Одно допущение на поведение, разделяемое всеми запечатываниями журнала."""

    key = _core_key(core)
    if key not in _ADMITTED:
        _ADMITTED[key] = admit(admission_request(core))
    return _ADMITTED[key]
