"""Read method applicability from physically retained admitted replay.

This adapter joins the existing source-publication and replay contracts with
Stage 10's neutral planning basis. It cannot publish, execute a command, mint
admission, or substitute observations supplied by a worker.
"""

from ..behavior import behavior_unit_from_dict
from ..bindings import binding_to_ref
from ..canonicalization import HashBoundRef
from ..replay_store import FileReplayStore
from ..replay_vm_adapter import read_replayed_return_value
from ..source_procedures import SOURCE_COVERAGE_PROFILE_V1, source_coverage_inputs, validate_source_coverage
from ..stage10.planning_basis import create_planning_basis
from ..stage13.publication_store import PublicationResult


def read_procedural_observations(*, catalog, target_records, selected_subject_refs):
    from ..stage14.sources import read_input_graph, read_source_publications, _reopen_location

    read_input_graph(catalog)
    path, fence = _reopen_location(catalog["replay"])
    store = FileReplayStore(path.parent, mutation_fence=fence, read_only=True)
    replay = store.require_result(HashBoundRef.from_dict(catalog["replay_ref"]))
    observations = {item.behavior_content_key: item for item in replay.observations}
    targets = {binding_to_ref(item): item.path for item in target_records}
    alternatives = []
    for source in read_source_publications(catalog):
        origin = source["origin"]
        subject = HashBoundRef.from_dict(origin["subject_ref"])
        if subject not in selected_subject_refs:
            continue
        unit = behavior_unit_from_dict(source["request"]["unit"])
        if unit.core.verification_contract.profile_id != SOURCE_COVERAGE_PROFILE_V1:
            continue
        # The ordinary publication reader verifies the complete transaction and
        # its independent source proof; a retained request alone is insufficient.
        from pathlib import Path
        PublicationResult(Path(origin["publication_root"]), origin["transaction_id"]).payload()
        observation = observations.get(unit.content_key.value)
        if observation is None:
            continue
        returned = read_replayed_return_value(observation, store.open_snapshot(observation.terminal_snapshot_ref))
        validate_source_coverage(returned, inputs=source_coverage_inputs(unit, tuple(targets)))
        paths = sorted({targets[ref] for ref in unit.core.binding_refs if ref in targets})
        alternatives.append({
            "subject_ref": subject.to_dict(), "observation_id": observation.observation_id.to_dict(),
            "terminal_snapshot_ref": observation.terminal_snapshot_ref.to_dict(),
            "coverage": returned, "covered_paths": paths,
        })
    return create_planning_basis(target_paths=tuple(sorted(set(targets.values()))), alternatives=alternatives)
