"""New worker contexts require scoped source delivery; recorded envelopes stay readable."""

import pytest

from acceptance.stage4.stage10._builders import hash_ref
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.stage10.context import (
    build_worker_context, ContextViolation, inspect_recorded_worker_context,
)
from synapse.experiments.gold.stage10.context_codec import (
    ContextCodecViolation, SOURCE_DELIVERY_PROFILE_V2,
    create_worker_delivery_envelope, decode_canonical, decode_worker_delivery_envelope,
    encode_base64url, encode_canonical,
)


def _source():
    reference = hash_ref(RefKind.SOURCE_EVIDENCE, "retained-source-cut",
                         schema="synapse.stage4.gold.source-experience-snapshot/v1")
    return {"snapshot_ref": reference.to_dict(), "projection_profile": SOURCE_DELIVERY_PROFILE_V2,
            "information": {"schema_version": "synapse.worker.local-information-input/v1", "items": [
                {"role": "REFERENCE", "media_type": "text/plain",
                 "content_base64url": encode_base64url(b"retained routing observation")}]}}


def _build(context, source):
    return build_worker_context(intent=context.intent, accepted_plan=context.accepted_plan,
        attempt_id=context.attempt_id, admitted_knowledge=context.admitted_knowledge,
        knowledge_selection=context.knowledge_selection, knowledge_items=context.knowledge_items,
        replay_observations=context.replay_observations, excluded_refs=context.excluded_refs,
        source_experience_bytes=encode_canonical(source))


@pytest.mark.parametrize("profile", [None, "legacy/v1", "future/v3"])
def test_new_context_refuses_unversioned_or_unknown_source_projection(stage10_delivery_world, profile):
    source = _source()
    if profile is None:
        source.pop("projection_profile")
    else:
        source["projection_profile"] = profile
    with pytest.raises(ContextCodecViolation, match="current projection profile"):
        _build(stage10_delivery_world.context, source)


def test_recorded_legacy_envelope_reopens_without_rewriting_its_bytes(stage10_delivery_world, tmp_path):
    current = _build(stage10_delivery_world.context, _source())
    body = decode_canonical(current.delivery_envelope.body_bytes)
    body["source_experience"].pop("projection_profile")
    legacy = create_worker_delivery_envelope(context_id=current.context_id, body_bytes=encode_canonical(body))
    path = tmp_path / "recorded-worker-envelope.json"
    original = legacy.canonical_bytes()
    path.write_bytes(original)
    reopened = decode_worker_delivery_envelope(path.read_bytes())
    assert reopened.canonical_bytes() == original == path.read_bytes()
    assert reopened.information_text == current.delivery_envelope.information_text
    assert reopened.prompt_text == current.delivery_envelope.prompt_text
    assert reopened.envelope_sha256 != current.delivery_envelope.envelope_sha256


def test_source_profile_is_bound_to_the_recorded_audit(stage10_delivery_world):
    current = _build(stage10_delivery_world.context, _source())
    assert inspect_recorded_worker_context(current.canonical_bytes(), current.delivery_envelope.canonical_bytes())
    body = decode_canonical(current.delivery_envelope.body_bytes)
    body["source_experience"].pop("projection_profile")
    downgraded = create_worker_delivery_envelope(context_id=current.context_id, body_bytes=encode_canonical(body))
    with pytest.raises(ContextViolation):
        inspect_recorded_worker_context(current.canonical_bytes(), downgraded.canonical_bytes())


def test_recorded_envelope_rejects_an_unknown_projection_profile(stage10_delivery_world):
    current = _build(stage10_delivery_world.context, _source())
    body = decode_canonical(current.delivery_envelope.body_bytes)
    body["source_experience"]["projection_profile"] = "future/v3"
    with pytest.raises(ContextCodecViolation, match="current projection profile"):
        create_worker_delivery_envelope(context_id=current.context_id, body_bytes=encode_canonical(body))
