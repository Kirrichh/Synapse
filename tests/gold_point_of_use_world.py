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


def _core_key(core, extra=()) -> str:
    import hashlib
    import json

    if core is None and not extra:
        return "default"
    return hashlib.sha256(
        json.dumps([core, list(extra)], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def world(core=None, extra=()):
    """Граф полномочий точки использования для опубликованных поведений.

    Кэшируется по набору ядер: §22 допускает *опубликованный* субъект, а
    поведение с другим ядром — другой субъект, у которого нет ни дескриптора,
    ни аттестации в чужом мире.

    ``extra`` публикует дополнительные поведения в тот же мир: одна библиотека,
    одна зафиксированная граница, одна цепочка шлюзов над всем набором. Это то,
    чего не хватало упорядоченному набору §23 и отказу resume по программе —
    оба случая требуют двух субъектов *под одной границей*, а не двух миров.
    """

    key = _core_key(core, extra)
    if key not in _WORLDS:
        from tests.test_stage4_gold_consumption_evidence import production_point_of_use_case

        root = Path(tempfile.mkdtemp(prefix="stage9-point-of-use-"))
        _WORLDS[key] = production_point_of_use_case(
            root / "case", behavior_core=core, extra_behavior_cores=tuple(extra)
        )
    return _WORLDS[key]


def subject_ref(core=None, extra=()):
    """Ссылка библиотечного субъекта, которую допустили четыре шлюза."""

    return world(core, extra).subject


def subject_ref_for(unit, core=None, extra=()):
    """Ссылка того субъекта в мире, который называет именно это поведение."""

    digest = unit.content_key.digest_sha256
    for reference in world(core, extra).subjects:
        if reference.ref_id == digest:
            return reference
    raise AssertionError("этот мир не публиковал названное поведение")


def consumer_context_ref(core=None, extra=()):
    return world(core, extra).context_ref


def boundary_ref(core=None, extra=()):
    return world(core, extra).boundary_ref


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
    # Каждый допущенный субъект получает свои Stage 3 записи. Проба покрывает
    # ровно тот набор, который admit_for_use_now будет допускать: если хотя бы
    # у одного субъекта записи нет, допущение справедливо откажет — и это не то,
    # что здесь проверяется.
    supported = case.supported
    decisions = tuple(
        evaluate_compatibility(
            evaluator=evaluator, context=context, descriptor=item[1], index_entry=item[2]
        )
        for item in supported
    )
    scan = evaluate_conflicts(
        evaluator=evaluator,
        context=context,
        decisions=decisions,
        descriptors=tuple(item[1] for item in supported),
        considered_index_entries=tuple(item[2] for item in supported),
        proposals=(),
    )
    stage2_records = tuple(
        revalidate_before_loading(
            evaluator=evaluator,
            context=context,
            descriptor=item[1],
            original_decision=decisions[index],
        )
        for index, item in enumerate(supported)
    )
    history = binding.compatibility_history
    records = [context]
    for item in decisions:
        records.extend((item.evidence, item))
    records.append(scan)
    records.extend(stage2_records)
    for record in records:
        try:
            history.append_record(record, expected_parent_anchor=history.current_anchor())
        except CompatibilityStoreViolation as exc:
            if exc.failure_code is not CompatibilityStoreFailureCode.RECORD_DUPLICATE:
                raise
    evidence_bindings = tuple(
        GF.bind_consumption_evidence(
            descriptor=item[1],
            original_decision=decisions[index],
            before_loading=stage2_records[index],
            conflict_scan=scan,
        )
        for index, item in enumerate(supported)
    )
    probe = GF.configured_durable_revalidation_probe(
        evaluator=evaluator,
        context=context,
        bindings=evidence_bindings,
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


def admission_request(core=None, extra=()) -> P.PointOfUseAdmissionRequest:
    """Новая попытка точки использования, годная ровно для одного допущения."""

    key = _core_key(core, extra)
    case = world(core, extra)
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


def admitted_knowledge(core=None, extra=()):
    """Одно допущение на поведение, разделяемое всеми запечатываниями журнала."""

    key = _core_key(core, extra)
    if key not in _ADMITTED:
        _ADMITTED[key] = admit(admission_request(core, extra))
    return _ADMITTED[key]
