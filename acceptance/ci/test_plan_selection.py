"""Observe planned execution, rather than repository metadata conventions."""
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


def test_native_mutations_follow_affected_owners_and_shared_contracts():
    seeds = [{'id': 'transport', 'target': 'synapse/ui/src-tauri/src/transport.rs', 'case': 'transport'},
             {'id': 'bundle', 'target': 'synapse/ui/src-tauri/src/bundle.rs', 'case': 'bundle'}]
    plan = {'native': True, 'changed': ['synapse/ui/src-tauri/src/transport.rs']}
    assert selected_seeds('native', seeds, plan) == ['transport']
    plan['changed'] = ['synapse/machine/release_profile.json']
    assert selected_seeds('native', seeds, plan) == ['transport', 'bundle']
