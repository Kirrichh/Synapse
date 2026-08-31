"""Mutation acceptance: a delivery checkpoint cannot swap terminal kind."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse.experiments.gold.admission import AdmissionFailureCode
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.vocabulary import (
    FallbackPolicy,
    GoldRunFailureCode,
    GoldRunViolation,
)
from synapse.experiments.gold.stage10.context_codec import (
    decode_base64url,
    decode_canonical,
    encode_canonical,
)

from acceptance.stage4.stage11._builders import run_world
from acceptance.stage4.stage11._record_mutations import (
    clone_run_records,
    replace_progress_payload,
)


def test_refusal_checkpoint_cannot_claim_unavailable_payload_kind(tmp_path: Path) -> None:
    world = run_world(
        tmp_path / "source",
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        refusal_attempts={1},
        run_id="mutated-delivery-kind",
    )
    world.execute()

    def mutate(kind: str, key: str, stored: dict[str, object]):
        if kind != RecordKind.ATTEMPT_PROGRESS or not key.endswith("delivery_refused"):
            return stored
        progress = stored["payload"]
        raw = decode_canonical(decode_base64url(progress["payload_base64url"]))
        raw["terminal_kind"] = "DELIVERY_UNAVAILABLE"
        raw["admission_failure_code"] = AdmissionFailureCode.DEPENDENCY_UNAVAILABLE.value
        return replace_progress_payload(stored, raw=encode_canonical(raw))

    forged = clone_run_records(
        world.composition.record_store,
        tmp_path / "forged",
        mutate=mutate,
    )
    with pytest.raises(GoldRunViolation) as caught:
        load_run_state(forged)
    assert caught.value.failure_code is GoldRunFailureCode.AUTHORITY_MISMATCH
