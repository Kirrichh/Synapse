from __future__ import annotations

from dataclasses import replace

import pytest

from synapse.experiments.gold.stage10.intent_transport import decode_intent_candidate
from synapse.experiments.gold.stage10.plan_authority import validate_accepted_operation_plan

from acceptance.stage4.stage10._builders import plan_world


def test_identity_checks_reject_mutations_across_every_accepted_plan_layer() -> None:
    intent, _plan, _policy, _authority, _decision, accepted = plan_world()
    mutations = (
        replace(accepted.candidate, repository_revision_sha256="b" * 40),
        replace(accepted.candidate, capability_profile=("repository.read",)),
        replace(accepted.candidate, execution_order=("hidden-operation",)),
    )

    for candidate in mutations:
        with pytest.raises(ValueError):
            validate_accepted_operation_plan(replace(accepted, candidate=candidate))

    raw = intent.to_dict()
    raw["payload"]["required_capabilities"] = ["*"]
    from synapse.experiments.gold.stage10.context_codec import encode_canonical

    with pytest.raises(ValueError):
        decode_intent_candidate(encode_canonical(raw))
