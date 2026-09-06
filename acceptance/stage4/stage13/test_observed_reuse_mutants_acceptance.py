"""External mutation oracles exercise actual consumption and promotion proof."""

from copy import deepcopy
from dataclasses import replace
import inspect
import textwrap
from types import SimpleNamespace

import pytest

from acceptance.stage4.stage13._observed_reuse import observed_reuse_case
from synapse.experiments.gold.run_inputs import reopen_frozen_inputs
from synapse.experiments.gold.runner_composition import compose_frozen_gold_run
from synapse.experiments.gold.runner.completed_delivery_codec import restore_completed_worker_delivery
from synapse.experiments.gold.runner.run_progress import load_attempt_progress, require_progress_payload, AttemptProgressPhase
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.stage10.context_codec import decode_canonical
from synapse.experiments.gold.stage12.verification import verify_attempt
from synapse.experiments.gold.stage12.outcome import evaluate_attempt_outcome
from synapse.experiments.gold.stage13 import reuse, promotion
from synapse.experiments.gold.stage13.publication import PublicationViolation
from synapse.experiments.swebench.gold_oracle_binding import GoldSWEbenchOracleBinding


@pytest.fixture(scope="module")
def evidence(tmp_path_factory):
    root = tmp_path_factory.mktemp("actual-reuse-mutations")
    _, consumer, _, _ = observed_reuse_case(root)
    code, pending = consumer.start()
    assert code == 3, pending
    code, result = consumer.approve(pending)
    assert code == 0, result
    assert len(result["result"]["structured_outcome"]["payload"]["observed_reuse"]) == 1
    composition = compose_frozen_gold_run(reopen_frozen_inputs(consumer.run_root))
    materializer = composition._attempt_materializer
    context = load_run_state(composition.record_store).attempts[0].context
    progress = load_attempt_progress(composition.record_store, manifest=composition.manifest, context=context)
    raw, ref = require_progress_payload(progress.get(AttemptProgressPhase.WORKER_COMPLETED))
    completed = restore_completed_worker_delivery(raw, expected_ref=ref)
    raw, _ = require_progress_payload(progress.latest)
    mechanism = decode_canonical(raw)
    arguments = dict(publisher=materializer._publisher, stage10_store=composition.stage10_composition.record_store,
        run_store=composition.record_store, run_root=consumer.run_root, manifest=composition.manifest,
        context=context, completed=completed, profile=materializer._verification_profile, boundary=composition._c1_boundary)
    verification_arguments = dict(manifest=composition.manifest, context=context, run_store=composition.record_store,
        boundary=composition._c1_boundary, record_store=composition.stage10_composition.record_store,
        profile=materializer._verification_profile, run_root=consumer.run_root,
        reusable_authority=materializer._reusable_authority, publication_store=materializer._publisher)
    return SimpleNamespace(consumer=consumer, mechanism=mechanism, arguments=arguments,
                           verification_arguments=verification_arguments)


def _mutate(monkeypatch, module, name, before, after):
    original = getattr(module, name)
    source = textwrap.dedent(inspect.getsource(original))
    assert source.count(before) == 1
    namespace = dict(original.__globals__)
    exec(compile(source.replace(before, after), original.__code__.co_filename, "exec"), namespace)
    monkeypatch.setattr(module, name, namespace[name])


@pytest.mark.parametrize("mutation", ["oracle_configuration", "observed_fingerprint", "promotion_binding"])
def test_controlled_product_mutations_change_the_acceptance_verdict(evidence, monkeypatch, mutation):
    actual = reuse.verify_mechanism_use(evidence.mechanism, **evidence.arguments)
    assert actual["repository_unchanged"] is True
    arguments = dict(evidence.arguments)
    mechanism = deepcopy(evidence.mechanism)
    if mutation == "oracle_configuration":
        boundary = arguments["boundary"]
        arguments["boundary"] = replace(boundary, oracle=GoldSWEbenchOracleBinding(
            replace(boundary.oracle.config, dataset_name="different-acceptance-dataset")))
        exercise = lambda: reuse.verify_mechanism_use(mechanism, **arguments)
    elif mutation == "observed_fingerprint":
        mechanism["domain_fingerprint"] = "0" * 64
        exercise = lambda: reuse.verify_mechanism_use(mechanism, **arguments)
    else:
        facts = verify_attempt(**evidence.verification_arguments).payload()
        mechanism["confidence"] = "UNVERIFIED"
        exercise = lambda: promotion.read_reuse_promotions(publisher=arguments["publisher"], store=arguments["run_store"],
            manifest=arguments["manifest"], context=arguments["context"], facts=facts, mechanism_record=mechanism)
    with pytest.raises(PublicationViolation):
        exercise()
    with monkeypatch.context() as patch:
        if mutation == "oracle_configuration":
            patch.setattr(reuse, "_oracle_configuration_matches", lambda *args: True)
        elif mutation == "observed_fingerprint":
            _mutate(patch, reuse, "verify_mechanism_use", 'value["domain_fingerprint"] != digest or ', "")
        else:
            _mutate(patch, promotion, "_source_proof",
                'or reference(mechanism_record, MECHANISM_USE_SCHEMA_V1).to_dict() != use["record_ref"]', "")
        # The same acceptance assertion must fail for each altered product.
        with pytest.raises(pytest.fail.Exception):
            with pytest.raises(PublicationViolation):
                exercise()


def test_retained_snapshot_is_required_on_independent_reverification(evidence):
    ref = evidence.mechanism["snapshot_ref"]
    path = evidence.consumer.run_root / "replay" / "records" / "snapshots" / ref["sha256"][:2] / ref["sha256"]
    raw = path.read_bytes()
    try:
        path.unlink()
        verified = verify_attempt(**evidence.verification_arguments)
        assert "MECHANISM_USE_INVALID" in verified.payload()["failure_codes"]
        assert verified.payload()["reuse_promotions"] == []
        assert evaluate_attempt_outcome(verified).payload()["status"] == "INVALID_CONTRACT"
    finally:
        path.write_bytes(raw)
    assert len(verify_attempt(**evidence.verification_arguments).payload()["reuse_promotions"]) == 1
