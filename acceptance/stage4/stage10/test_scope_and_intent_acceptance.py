from __future__ import annotations

import pytest

from synapse.experiments.gold.stage10.intent import IntentFailureCode, IntentViolation
from synapse.experiments.gold.stage10.intent_transport import (
    decode_intent_candidate,
    encode_intent_candidate,
)
from synapse.experiments.gold.stage10.repository_scope import (
    ScopeViolation,
    create_repository_scope,
)

from acceptance.stage4.stage10._builders import plan_world


def test_scope_uses_path_boundaries_and_rejects_escape() -> None:
    scope = create_repository_scope(("synapse/runtime", "docs/models"))

    assert scope.covers("synapse/runtime/engine.py")
    assert not scope.covers("synapse/runtimex/engine.py")
    with pytest.raises(ScopeViolation):
        scope.covers("synapse/runtime/../outside.py")


def test_intent_transport_round_trips_and_old_identity_rejects_tamper() -> None:
    intent, *_ = plan_world()
    encoded = encode_intent_candidate(intent)

    assert decode_intent_candidate(encoded) == intent
    tampered = encoded.replace(b"accepted Stage 10", b"altered Stage 10 ")
    with pytest.raises(IntentViolation) as raised:
        decode_intent_candidate(tampered)
    assert raised.value.failure_code is IntentFailureCode.IDENTITY_MISMATCH
