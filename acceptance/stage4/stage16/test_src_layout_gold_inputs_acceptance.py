"""A rooted project freezes its Gold run inputs with task targets bound under its import roots.

A governing task v3 resolves its targets when its Gold inputs are frozen. In a project that declares the
import root ``src``, the project's knowledge binds ``src/calc.py`` as the module ``calc``; the frozen target
resolution records the roots (``task-target-resolution/v2``) and binds the same module by the same name, and
reopening the frozen inputs reproduces it.
"""
from acceptance.stage4.stage16._source_inputs import consumer_case
from synapse.experiments.gold.bindings import BINDING_CONTRACT_VERSION_V2
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.run_inputs import freeze_gold_inputs
from synapse.experiments.gold.task_targets import TASK_TARGET_RESOLUTION_V2
from synapse.memory_consolidation.project_port import ProjectMemoryCourt


def test_a_rooted_project_freezes_its_task_targets_under_its_import_roots(tmp_path):
    case, publication = consumer_case(tmp_path, automatic_targets=True, python_roots=("src",))
    knowledge, = publication["knowledge"]["candidates"][0]["bindings"]
    assert (knowledge["module"], knowledge["contract_version"]) == ("calc", BINDING_CONTRACT_VERSION_V2)

    frozen = freeze_gold_inputs(declaration_path=case.input_path, project=open_gold_project(tmp_path / "state"),
                                run_root=case.run_root, court=ProjectMemoryCourt())

    resolution = frozen.data["target_resolution"]
    assert (resolution["schema_version"], resolution["python_roots"]) == (TASK_TARGET_RESOLUTION_V2, ["src"])
    targets = frozen.resolve_targets()
    assert targets and {(item.module, item.contract_version) for item in targets} == {
        ("calc", BINDING_CONTRACT_VERSION_V2)}
