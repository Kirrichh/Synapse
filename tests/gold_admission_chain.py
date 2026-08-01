"""Реальная цепочка четырёх шлюзов §22 — для приёмок, чей предмет лежит ниже.

Приёмки Patch 9 говорят о воспроизведении и о записанных активностях, а не о
допуске. Но им нужно **настоящее** consumption-решение: §23 запрещает
воспроизведению потреблять то, чего шлюз §22 не допустил, и подделанное решение
сделало бы это требование непроверяемым. Поэтому цепочка прогоняется здесь один
раз через production-входы, и обе приёмки пользуются одним построением.

Ничего здесь не решает. Декларация, контроллер и четыре вызова шлюзов —
production-функции; модуль лишь поставляет конфигурацию, которую вызывающий
обязан поставить в любом случае (акторы, пробы, доверенные часы, читатель
head-ов), и делает это ровно в одном месте, чтобы две приёмки не разошлись в
представлении о том, как выглядит законная цепочка.

Проба совместимости — функция вопроса, а не модульная константа: находка теперь
называет свой субъект и замороженный контекст потребителя, так что фиксированное
значение было бы ответом об одном субъекте, переиспользованным для всех
остальных, — ровно та подстановка, против которой существует шлюз.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib

from synapse.experiments.gold import admission as A
from synapse.experiments.gold import authority_config as AC
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AttemptId,
    AuthorityIdentity,
    AuthorityRole,
    GateKind,
    RunId,
    SchemaVersion,
    create_stage4_authority_configuration,
    create_stage4_authority_handle,
)

#: Часы, под которыми объявлен оценщик. Декларация и контроллер обязаны
#: смотреть на одни и те же часы, иначе конфигурация недействительна.
NOW = datetime(2026, 7, 31, 9, 0, 0, tzinfo=timezone.utc)
POLICY = "policy-v1"
RUN_ID = RunId("stage9-run")
ATTEMPT_ID = AttemptId("stage9-attempt")
REPOSITORY_REVISION = "b" * 40
ENVIRONMENT_PROFILE_ID = "stage9-env"

REQUEST = A.RequestedEnvelope(scopes=("repo:x",), capabilities=("read",), oracles=("swebench",))
CLEAN_TAINT = A.TaintFinding(
    consumable=True, chain_complete=True, quarantined=False, blocks_publication=False
)


def ref(kind: RefKind, name: str, payload: bytes = b"p") -> HashBoundRef:
    return HashBoundRef(
        kind=kind,
        ref_id=name,
        schema_id="synapse.stage4.gold.thing/v1",
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


CONTEXT_REF = ref(RefKind.ARTIFACT, "consumer-ctx")
BOUNDARY_REF = ref(RefKind.ATOMIC_BOUNDARY, "boundary-1")
OTHER_CONTEXT_REF = ref(RefKind.ARTIFACT, "consumer-ctx-2")
OTHER_BOUNDARY_REF = ref(RefKind.ATOMIC_BOUNDARY, "boundary-2")

FROZEN_SET_REF = HashBoundRef(
    kind=RefKind.ARTIFACT,
    ref_id="frozen-set-stage9",
    schema_id=SchemaVersion.FROZEN_CANDIDATE_SET_V2.value,
    sha256=hashlib.sha256(b"frozen-set-stage9").hexdigest(),
    byte_length=len(b"frozen-set-stage9"),
    media_type="application/json",
)


def subject_key(item: HashBoundRef) -> str:
    return f"{item.kind.value}\x00{item.ref_id}\x00{item.sha256}"


def compat_finding(
    subject_ref: HashBoundRef,
    consumer_context_ref: HashBoundRef,
    **overrides: bool,
) -> A.CompatibilityFinding:
    """Ответ о совместимости, который называет, о чём он."""

    fields = {
        "compatible": True,
        "evidence_complete": True,
        "drifted": False,
        "conflicts_unresolved": False,
    } | overrides
    return A.CompatibilityFinding(
        subject_ref=subject_ref, consumer_context_ref=consumer_context_ref, **fields
    )


def authority_handle():
    configuration = create_stage4_authority_configuration(
        platform_attester_actor=ActorIdentity(value="attester"),
        builder_actor=ActorIdentity(value="builder"),
        taint_classifier_authority=AuthorityIdentity(value="taint-classifier"),
        taint_reviewer_authority=AuthorityIdentity(value="taint-reviewer"),
        supersession_reviewer_authority=AuthorityIdentity(value="supersession-reviewer"),
        revocation_reviewer_authority=AuthorityIdentity(value="revocation-reviewer"),
        lifecycle_writer_actor=ActorIdentity(value="lifecycle-writer"),
        governing_human_authority=None,
    )
    return create_stage4_authority_handle(configuration)


def gate_declaration(*, authority: str = "gate-authority", policy: str = POLICY):
    """Регистрация, из которой конфигурируется контроллер.

    Роли берутся отсюда, а не от вызывающего: иначе тот, кто конфигурирует
    контроллер, объявлял бы собственные полномочия.
    """

    return AC.create_gate_evaluator_declaration(
        authority_handle=authority_handle(),
        evaluator_identity=AuthorityIdentity(value=authority),
        evaluator_component_id="gate-evaluator",
        evaluator_component_version="synapse.stage4.gate-evaluator/v1",
        gate_roles={
            GateKind.INGESTION: AuthorityRole.INGESTION_GATE_EVALUATOR,
            GateKind.PUBLICATION: AuthorityRole.PUBLICATION_GATE_EVALUATOR,
            GateKind.RETRIEVAL: AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
            GateKind.CONSUMPTION: AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
        },
        policy_version=policy,
        trusted_clock=lambda: NOW,
    )


#: Набор акторов, над которым каждый контроллер этой цепочки устанавливает
#: независимость. Проверяющий обязан принести те же три.
SOURCE_ACTORS = (
    ActorIdentity(value="producer"),
    ActorIdentity(value="retriever"),
    ActorIdentity(value="consumer"),
)


def entitlement(*, authority: str = "gate-authority", policy: str = POLICY):
    """Собственная декларация проверяющего и его набор акторов, по шлюзам."""

    return {
        gate: (gate_declaration(authority=authority, policy=policy), SOURCE_ACTORS)
        for gate in GateKind
    }


def anchors(*, boundary_ref: HashBoundRef = BOUNDARY_REF) -> dict[str, object]:
    """Что возвращает доверенный читатель: зафиксированную границу и все head-ы."""

    return {
        "boundary_ref": boundary_ref,
        "heads": {
            name: {
                "anchor_sha256": hashlib.sha256(f"{name}-head-0".encode()).hexdigest(),
                "sequence": 1,
            }
            for name in A.AUTHORITY_HEAD_DOMAINS
        },
    }


def gate_controller(
    *,
    policy: str = POLICY,
    boundary_ref: HashBoundRef = BOUNDARY_REF,
    taint=None,
    provenance=None,
    lifecycle=None,
    compat=None,
    boundary=None,
    grant=None,
    heads=None,
) -> A.ConfiguredGateController:
    grant_envelope = A.GrantEnvelope(
        scopes=("repo:x",), capabilities=("read",), oracles=("swebench",), policy_version=policy
    )
    return A.configure_gate_controller(
        declaration=gate_declaration(policy=policy),
        policy_version=policy,
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        repository_revision=REPOSITORY_REVISION,
        environment_profile_id=ENVIRONMENT_PROFILE_ID,
        trusted_clock=lambda: NOW,
        taint_probe=taint or (lambda item: CLEAN_TAINT),
        provenance_probe=provenance or (lambda item: True),
        lifecycle_probe=lifecycle or (lambda item: True),
        compatibility_probe=compat or (lambda item, ctx: compat_finding(item, ctx)),
        # Проба границы отвечает о конкретной границе, а не «да» на любую:
        # шлюз спрашивает, является ли она в точности зафиксированной властью.
        boundary_probe=boundary or (
            lambda item: subject_key(item) == subject_key(boundary_ref)
        ),
        grant_probe=grant or (lambda: grant_envelope),
        head_reader=heads or (lambda: anchors(boundary_ref=boundary_ref)),
        producer_actor=ActorIdentity(value="producer"),
        retriever_actor=ActorIdentity(value="retriever"),
        consumer_actor=ActorIdentity(value="consumer"),
    )


def consumption_decision(
    subject_refs: tuple[HashBoundRef, ...],
    *,
    consumer_context_ref: HashBoundRef = CONTEXT_REF,
    boundary_ref: HashBoundRef = BOUNDARY_REF,
    policy: str = POLICY,
    frozen_candidate_set_ref: HashBoundRef = FROZEN_SET_REF,
) -> A.GateDecision:
    """Прогнать полную цепочку четырёх шлюзов и вернуть её consumption-вердикт."""

    control = gate_controller(policy=policy, boundary_ref=boundary_ref)
    ingestion = A.evaluate_ingestion_gate(control, subject_refs=subject_refs)
    publication = A.evaluate_publication_gate(
        control, subject_refs=subject_refs, requested=REQUEST, predecessor=ingestion
    )
    retrieval = A.evaluate_retrieval_gate(
        control,
        subject_refs=subject_refs,
        consumer_context_ref=consumer_context_ref,
        boundary_ref=boundary_ref,
        frozen_candidate_set_ref=frozen_candidate_set_ref,
        requested=REQUEST,
        predecessor=publication,
    )
    return A.evaluate_consumption_gate(
        control,
        subject_refs=subject_refs,
        consumer_context_ref=consumer_context_ref,
        boundary_ref=boundary_ref,
        requested=REQUEST,
        predecessor=retrieval,
    )
