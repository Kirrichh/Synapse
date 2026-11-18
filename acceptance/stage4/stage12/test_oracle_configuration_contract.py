"""Configuration comparisons only; fabricated diagnostics grant no C1 proof."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from synapse.experiments.gold.runner.c1_boundary import C1EvidenceContext, matches_retained_oracle_configuration
from synapse.experiments.gold.stage10.context_codec import encode_canonical
from synapse.experiments.applied_verification.change_oracle import (
    ChangeOracleConfig, build_oracle_config_fingerprint_payload, compute_oracle_config_fingerprint,
    build_oracle_environment_fingerprint_payload, compute_oracle_environment_fingerprint,
)
from tests.test_applied_verification_gold_runner import policy


def comparison():
    config = ChangeOracleConfig(work_dir=Path('/observed/oracle'), fail_to_pass=(('/observed/python', 'check.py'),),
        command_timeout_seconds=10)
    boundary = C1EvidenceContext(Path('/observed/repo'), policy(), 'observed-environment', config)
    observed = build_oracle_config_fingerprint_payload(config)
    environment = build_oracle_environment_fingerprint_payload(config)
    return boundary, {'oracle_diagnostics': {
        'oracle_config_fingerprint_payload': observed,
        'oracle_config_fingerprint': compute_oracle_config_fingerprint(observed),
        'oracle_environment_fingerprint_payload': environment,
        'oracle_environment_fingerprint': compute_oracle_environment_fingerprint(environment),
        'cwd': str(config.work_dir)}}


def test_exact_captured_configuration_matches_without_executing_oracle():
    boundary, payload = comparison()
    assert matches_retained_oracle_configuration(boundary, encode_canonical(payload))


@pytest.mark.parametrize('changes', [
    {'fail_to_pass': (('/another/python', 'check.py'),)},
    {'pass_to_pass': (('/observed/python', 'regressions.py'),)},
    {'work_dir': Path('/another/oracle')},
    {'command_timeout_seconds': 11},
    {'evaluator_patch': Path('/observed/hidden.patch'), 'evaluator_patch_sha256': 'a' * 64},
])
def test_other_check_directory_or_evaluator_cannot_inherit_old_outcome(changes):
    boundary, payload = comparison()
    changed = replace(boundary, oracle_config=replace(boundary.oracle_config, **changes))
    assert not matches_retained_oracle_configuration(changed, encode_canonical(payload))


@pytest.mark.parametrize('mutate', [
    lambda info: info.pop('oracle_environment_fingerprint_payload'),
    lambda info: info.update(oracle_environment_fingerprint='0' * 64),
    lambda info: info['oracle_environment_fingerprint_payload'].update(adapter='another-oracle'),
    lambda info: info['oracle_config_fingerprint_payload'].update(fail_to_pass=[['/another/python', 'check.py']]),
    lambda info: info.update(cwd='/another/oracle'),
])
def test_retained_configuration_needs_matching_environment_bytes_and_execution_location(mutate):
    boundary, payload = comparison()
    changed = deepcopy(payload)
    mutate(changed['oracle_diagnostics'])
    assert not matches_retained_oracle_configuration(boundary, encode_canonical(changed))


def test_historical_platform_is_checked_as_retained_data_without_using_todays_platform():
    boundary, payload = comparison()
    environment = payload['oracle_diagnostics']['oracle_environment_fingerprint_payload']
    environment.update(platform='historical-platform', python_version='historical-version')
    payload['oracle_diagnostics']['oracle_environment_fingerprint'] = compute_oracle_environment_fingerprint(environment)
    assert matches_retained_oracle_configuration(boundary, encode_canonical(payload))
