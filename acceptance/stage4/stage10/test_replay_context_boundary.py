from __future__ import annotations

import pytest

from synapse.experiments.gold.replay import ReplayObservation, validate_replay_observation


def test_worker_prose_cannot_substitute_for_a_typed_replay_observation() -> None:
    worker_claim = {
        "transcript_matched": True,
        "summary": "I replayed it and everything passed",
    }

    with pytest.raises(ValueError):
        validate_replay_observation(worker_claim)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ReplayObservation()
