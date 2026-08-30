"""Stage 4 scope, snapshot, and environment admission acceptance shard."""

from __future__ import annotations

from tests.stage4_gold_replay_support import *  # noqa: F403


def test_a_subject_the_admission_does_not_name_never_reaches_a_request() -> None:
    """Compiling B while A was admitted is refused before anything is compiled."""

    unit, _binding = pure_behavior()
    stranger = unit_with(contract_for(("a-transition-nobody-admitted",)))
    prepared = prepare_for(unit)
    prepared.subjects = (
        R.ReplaySubject(subject_ref=admitted_subject(unit), unit=stranger),
    )
    with pytest.raises(R.ReplayViolation) as excinfo:
        prepared.request()
    assert excinfo.value.failure_code is R.ReplayFailureCode.IDENTITY_MISMATCH

def test_a_well_named_subject_the_admission_never_covered_is_refused() -> None:
    """Ссылка, честно называющая своё поведение, но не из этого допущения.

    Предыдущий случай подставляет чужое поведение под допущенную ссылку, и его
    ловит сверка «ссылка называет этот юнит». Здесь ссылка называет свой юнит
    честно — она просто принадлежит другому допущению, и отказ обязан прийти
    от сверки допущенного набора, под другим кодом и от другого владельца.
    Разводить эти две проверки нужно явно: они совпадают, пока ссылка и
    допущение приходят из одного мира, а совпадающие проверки нельзя показать
    работающими по отдельности.
    """

    unit, _binding = pure_behavior()
    stranger = unit_with(contract_for(("a-transition-of-another-subject",)))
    stranger_ref = admitted_subject(stranger)
    assert stranger_ref.ref_id == stranger.content_key.digest_sha256
    assert stranger_ref != admitted_subject(unit)

    prepared = prepare_for(unit)
    prepared.subjects = (R.replay_subject(subject_ref=stranger_ref, unit=stranger),)
    with pytest.raises(Exception) as excinfo:
        prepared.request()
    from synapse.experiments.gold import admission as A

    assert isinstance(excinfo.value, A.AdmissionViolation)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.SUBJECT_MISMATCH

def test_a_reference_of_another_kind_cannot_stand_in_for_a_library_subject() -> None:
    """Мутант A8: снята проверка схемы ссылки субъекта.

    Сверка ``ref_id`` ловит чужое поведение, но не чужой *вид* объекта: запись
    активности, артефакт или граница могут нести тот же идентификатор и совсем
    другое значение. Схема — отдельное утверждение, и убрать её было незаметно,
    пока ни один случай не приносил ссылку правильного идентификатора и
    неправильного рода.
    """

    unit, _binding = pure_behavior()
    genuine = admitted_subject(unit)
    impostor = HashBoundRef(
        kind=genuine.kind,
        ref_id=genuine.ref_id,
        schema_id=SchemaVersion.RECORDED_ACTIVITY_V1.value,
        sha256=genuine.sha256,
        byte_length=genuine.byte_length,
        media_type=genuine.media_type,
    )
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.replay_subject(subject_ref=impostor, unit=unit)
    assert excinfo.value.failure_code is R.ReplayFailureCode.TYPE_MISMATCH

def test_a_consistently_forged_record_still_fails_the_snapshot_agreement() -> None:
    """Мутант A10: снята сверка снимка с зафиксированной границей.

    Первая попытка этой приёмки просто переписывала поле и требовала отказа —
    и проходила даже без проверки, потому что переписанный payload расходится
    с ``replay_id`` и отвергается по идентичности под тем же кодом. Тест ничего
    не доказывал.

    Здесь подделка *согласованная*: идентичность пересчитана под переписанный
    payload, как её пересчитает любой путь восстановления записи из внешнего
    представления. Такую запись сверка идентичности пропускает, и остаётся ровно
    одно, что её отвергает, — требование, чтобы снимок был той самой границей,
    против которой запись допущена.
    """

    from synapse.experiments.gold.contracts import IdentityDomain, compute_record_id

    prepared = pure_prepared()
    request = prepared.request()
    R.validate_replay_request(request)
    original_snapshot = request.knowledge_snapshot_id
    original_id = request.replay_id
    object.__setattr__(request, "knowledge_snapshot_id", "snapshot-someone-preferred")
    object.__setattr__(
        request,
        "replay_id",
        compute_record_id(
            domain=IdentityDomain.BEHAVIOR_REPLAY_REQUEST,
            canonical_bytes=R._canonical(R._request_payload(request)),
        ),
    )
    try:
        with pytest.raises(R.ReplayViolation) as excinfo:
            R.validate_replay_request(request)
        assert excinfo.value.failure_code is R.ReplayFailureCode.IDENTITY_MISMATCH
        assert "manifest" in str(excinfo.value)
    finally:
        object.__setattr__(request, "knowledge_snapshot_id", original_snapshot)
        object.__setattr__(request, "replay_id", original_id)

def test_a_rewritten_knowledge_set_detaches_the_ledger() -> None:
    """Мутант A11: журнал перестал сверяться с допущенным набором знания.

    Внутри фабрики журнал запечатывается тем же допущением, поэтому расхождение
    возможно только у переписанной записи — и именно её валидатор обязан
    отвергнуть, иначе журнал одного прогона молча описывает другой.
    """

    prepared = pure_prepared()
    request = prepared.request()
    original = request.knowledge_subject_refs
    object.__setattr__(
        request, "knowledge_subject_refs", (ref(RefKind.ARTIFACT, "another-subject"),)
    )
    try:
        with pytest.raises(ACT.ActivityViolation) as excinfo:
            R.validate_replay_request(request)
        assert excinfo.value.failure_code is ACT.ActivityFailureCode.LEDGER_NOT_BOUND
    finally:
        object.__setattr__(request, "knowledge_subject_refs", original)

def test_live_environment_drift_after_preparation_is_refused_before_replay() -> None:
    """§22: the Consumption Gate reads the world at the moment of use.

    Everything durable is left exactly where it was — lifecycle, provenance,
    taint, the admission journal and the committed boundary all keep their
    anchors — and only the *live* platform observation changes, to another
    environment profile version. Nothing a head comparison can see has moved.

    Before the repair this ran to ``REPLAY_IDENTICAL``. The gate had been
    crossed when the request was built, and execution re-checked only the
    coordinator epoch, the authority heads and the boundary; the compatibility
    that had just stopped holding was never re-evaluated, because the evaluation
    that reads environment, tool and policy observation had already happened.

    Four things are asserted together, because three of them pass without the
    fourth: the durable heads did not move, the observation provider really was
    consulted again, the refusal is typed and fail-closed, and the machine was
    never attached to a channel and never took a step.
    """

    unit, _binding = pure_behavior()
    core = published_core(unit)
    prepared = prepare_for(unit)
    port = ScriptedPort(program=prepared.program_hash, opcodes=["ADD"])

    before = WORLD.durable_head_anchors(core)
    provider = WORLD.platform_observation_provider(core)
    with WORLD.drifted_environment(core, environment_version="synapse.stage4.environment/v999"):
        assert WORLD.durable_head_anchors(core) == before, (
            "changing the live observation must not move a durable authority head"
        )
        calls_before = provider.calls
        with pytest.raises(Exception) as excinfo:
            prepared.run()
        assert provider.calls > calls_before, (
            "the point-of-use evaluation did not read the live observation again"
        )

    assert getattr(excinfo.value, "failure_code", None) is not None, (
        "environment drift must be refused with a typed failure, not a bare error"
    )
    assert port.channel is None, "a refused replay attached the activity channel"
    assert port._index == 0, "a refused replay took a machine step"
