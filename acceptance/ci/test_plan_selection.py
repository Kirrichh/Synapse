"""Observe planned execution, rather than repository metadata conventions."""
import json
from select_tests import build_plan, matrices, ui_selection
from ui_mutations import selected_seeds


def test_existing_matrix_schedules_only_the_affected_file():
    workflow = {'jobs': {'memory': {'strategy': {'matrix': {'include': [
        {'file': 'acceptance/memory/test_read.py'}, {'file': 'acceptance/memory/test_write.py'}]}},
        'steps': [{'run': 'python -m pytest "${{ matrix.file }}"'}]}}}
    groups, _ = matrices(workflow, {'acceptance/memory/test_write.py'}, set())
    assert groups['memory']['enabled']
    assert groups['memory']['matrix']['include'] == [{'file': 'acceptance/memory/test_write.py'}]


def test_existing_fixed_group_is_skipped_without_an_affected_consumer():
    workflow = {'jobs': {'core': {'steps': [{'run': 'python -m pytest tests/test_core*.py'}]}}}
    groups, _ = matrices(workflow, {'tests/test_ui.py'}, set())
    assert not groups['core']['enabled']


def test_new_acceptance_is_scheduled_without_editing_an_existing_catalog(tmp_path):
    path = tmp_path / 'acceptance/feature/test_new.py'
    path.parent.mkdir(parents=True)
    path.write_text('def test_behavior(): assert True\n')
    name = path.relative_to(tmp_path).as_posix()
    plan = build_plan(tmp_path, {name}, {name}, {'jobs': {}})
    assert plan['additional'] == [name]


def test_renderer_changes_do_not_start_python_or_native_suites():
    plan = ui_selection({'synapse/ui/src/App.tsx'}, set())
    assert plan['renderer'] and not plan['native'] and not plan['python']


def test_shared_control_profile_keeps_both_ui_consumers():
    plan = ui_selection({'synapse/machine/release_profile.json'}, set())
    assert plan['renderer'] and plan['native']


def test_documentation_and_retained_evidence_start_no_ui_suites():
    plan = ui_selection({'docs/ui/P1_VERIFICATION.md', 'acceptance/ui_bridge/evidence/result.json'}, set())
    assert not any(plan[key] for key in ('python', 'native', 'renderer', 'reporting'))


def test_recovery_acceptance_selects_renderer_and_its_browser_mutations():
    plan = ui_selection({'acceptance/ui_bridge/browser_recovery.py'}, set())
    assert plan['renderer'] and not plan['native'] and not plan['python']
    seeds = [{'id': 'channel', 'target': 'synapse/ui/src/connection/desktop.ts', 'kind': 'browser'}]
    assert selected_seeds('renderer', seeds, plan) == ['channel']


def test_dependency_environment_change_reaches_all_python_consumers_except_manual_providers(tmp_path):
    names = {'tests/test_old.py', 'acceptance/feature/test_new.py', 'acceptance/stage4/stage16/test_live_gemini_worker.py'}
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('')
    plan = build_plan(tmp_path, names, {'pyproject.toml'}, {'jobs': {}}, environment_changed=True)
    assert plan['tests'] == ['acceptance/feature/test_new.py', 'tests/test_old.py']


def test_python_mutations_follow_selected_behavior_cases():
    seeds = [{'id': 'selected', 'cases': ['acceptance/ui_bridge/test_codec.py::test_refusal']},
             {'id': 'unrelated', 'cases': ['acceptance/ui_bridge/test_reporting.py']}]
    assert selected_seeds('python', seeds, {'files': ['acceptance/ui_bridge/test_codec.py']}) == ['selected']


def test_mutations_follow_existing_acceptance_without_duplicate_ui_execution(tmp_path):
    case = 'tests/test_entry.py'
    catalog = 'acceptance/ui_bridge/mutation/python.json'
    seeds = [{'id':'entry', 'cases':[case + '::test_delegation']}]
    for name, text in {case:'', catalog:json.dumps(seeds)}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    plan = build_plan(tmp_path, {case, catalog}, {case}, {'jobs':{}})
    assert plan['additional'] == [case]
    assert plan['ui']['files'] == [] and not plan['ui']['python']
    assert plan['ui']['python_mutations']
    assert selected_seeds('python', seeds, plan['ui']) == ['entry']


def test_native_mutations_follow_affected_owners_and_shared_contracts():
    seeds = [{'id': 'transport', 'target': 'synapse/ui/src-tauri/src/transport.rs', 'case': 'transport'},
             {'id': 'bundle', 'target': 'synapse/ui/src-tauri/src/bundle.rs', 'case': 'bundle'}]
    plan = {'native': True, 'changed': ['synapse/ui/src-tauri/src/transport.rs']}
    assert selected_seeds('native', seeds, plan) == ['transport']
    plan['changed'] = ['synapse/machine/release_profile.json']
    assert selected_seeds('native', seeds, plan) == ['transport', 'bundle']


def test_report_and_benchmark_changes_select_their_protective_cases(tmp_path):
    from check_results import check, required_jobs

    evidence = 'acceptance/verification/test_mutation_evidence_integrity.py'
    isolation = 'acceptance/verification/test_benchmark_isolation.py'
    behavior = 'acceptance/verification/test_benchmark_plugin_behavior.py'
    report = 'reports/pr112_targeted_mutations.json'
    source = 'synapse/example.py'
    case = 'tests/test_example.py'
    harness = 'acceptance/benchmarks/pr112/benchmark_plugin.py'
    snapshot_only = 'synapse/other_evidence_source.py'
    files = {evidence, isolation, behavior, report, source, case, harness, snapshot_only}
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'manual_mutants': [{'source': source, 'detecting_test': case + '::test_case'}],
                                   'selector_campaign': {'snapshot_files': {snapshot_only: 'hash'}}})
                        if name == report else '')
    workflow = {'jobs': {}}
    plan = build_plan(tmp_path, files, {report}, workflow)
    assert evidence in plan['additional']
    assert 'additional-tests' in required_jobs(plan)
    for result in ({}, {'result': 'failure'}, {'result': 'cancelled'}, {'result': 'skipped'}):
        statuses = {'select': {'result': 'success'}, 'additional-tests': result}
        assert not check(statuses, required_jobs(plan))
    assert check({'select': {'result': 'success'}, 'additional-tests': {'result': 'success'}}, required_jobs(plan))
    # The record is read from its pinned commit; later work on what it
    # describes must neither invalidate it nor be gated by it.
    for changed in (source, case, snapshot_only):
        plan = build_plan(tmp_path, files, {changed}, workflow)
        assert evidence not in plan['tests']
    for changed in (harness, 'pytest.ini'):
        plan = build_plan(tmp_path, files | {'pytest.ini'}, {changed}, workflow)
        assert {isolation, behavior}.issubset(plan['additional'])
    plan = build_plan(tmp_path, files, {'docs/unrelated.md'}, workflow)
    assert not {evidence, isolation, behavior}.intersection(plan['tests'])


def test_native_kernel_does_not_select_unrelated_python_fallback_consumers(tmp_path):
    native = 'native/stage4_canonical/src/lib.rs'
    codec = 'synapse/experiments/gold/canonicalization.py'
    direct = 'tests/test_stage4_native_direct.py'
    unrelated = 'tests/test_unrelated_python_codec_user.py'
    files = {native, codec, direct, unrelated}
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('from synapse.experiments.gold import canonicalization\n' if name in (direct, unrelated) else '')
    workflow = {'jobs': {'stage4-native': {'steps': [{'run': 'pytest tests/test_stage4_native_direct.py'}]}}}
    plan = build_plan(tmp_path, files, {native}, workflow)
    assert plan['groups']['stage4-native']['enabled']
    assert direct in plan['tests']
    assert unrelated not in plan['tests']


def test_baseline_vectors_and_input_corpus_select_native_parity(tmp_path):
    data = 'tests/data/stage4_baseline_1e226e52.json'
    corpus = 'acceptance/stage4/canonical_baseline_cases.py'
    parity = 'tests/test_stage4_canonical_differential.py'
    files = {data, corpus, parity}
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('from acceptance.stage4 import canonical_baseline_cases\n' if name == parity else '')
    workflow = {'jobs': {'stage4-native': {'steps': [{'run': 'pytest tests/test_stage4_canonical_differential.py'}]}}}
    for changed in (data, corpus):
        plan = build_plan(tmp_path, files, {changed}, workflow)
        assert parity in plan['tests']
        assert plan['groups']['stage4-native']['enabled']
