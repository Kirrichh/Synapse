"""Build a synthetic cross-service migration frame through the product builder."""
import base64
import hashlib

from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.project_model import build_active_memory_frame, build_project_model
from synapse.experiments.gold.source_verification import SOURCE_FILE_V1, canonical, source_ref
from synapse.experiments.gold.stage10.intent import AcceptanceCriterion, AcceptanceKind, EffectConstraint, EffectDisposition, EffectKind
from synapse.experiments.gold.stage10.repository_scope import RepositoryScope
from synapse.experiments.gold.stage10.task_contract import GoverningTaskContract, TASK_CONTRACT_SCHEMA_V3

PRIVATE = "INTERNAL_FRAME_PROOF_NOT_WORKER_INFORMATION"
REVISION = "e" * 40


def _task(paths):
    condition = source_ref(b"migration-acceptance", "acceptance.migration/v1", RefKind.CONTRACT_CONDITION)
    return GoverningTaskContract("migration", "Migrate 1536 partitions across 24 nodes while preserving rollback obligations.",
        REVISION, RepositoryScope(tuple(paths)), ("source:patch",), (),
        tuple(EffectConstraint(f"effect-{index}", EffectDisposition.EXPECTED, EffectKind.PATH_MODIFIED, path, condition)
              for index, path in enumerate(paths)),
        tuple(AcceptanceCriterion(f"criterion-{index}", AcceptanceKind.CONTRACT_CONDITION, condition) for index in range(8)),
        schema_version=TASK_CONTRACT_SCHEMA_V3)


def _sources(paths, symbols):
    selected, bindings = [], []
    for index, path in enumerate(paths):
        raw = "\n".join(f"def route_{number}(partition):\n    return (partition + {number}) % 1536\n"
                        for number in range(symbols)).encode()
        reference = source_ref(raw, SOURCE_FILE_V1).to_dict()
        own = [{"path": path, "qualname": f"route_{number}", "symbol_kind": "FUNCTION", "module": f"services.s{index}"}
               for number in range(symbols)]
        bindings.extend(own)
        experience = {"bindings": own, "sources": [{"path": path, "ref": reference}],
            "retained": [{"ref": reference, "content_base64url": base64.urlsafe_b64encode(raw).decode().rstrip("=")}],
            "claim": {"operation_id": f"source-study-{index}"}, "status": "VERIFIED", "verification": "VERIFIED",
            "execution": "EXITED_ZERO"}
        selected.append({"match": {"paths": [path]}, "experience": experience})
    return {"schema_version": "acceptance.source-snapshot/v1", "recall": {"profile": "acceptance", "selected": selected}}, bindings


def _history(paths, count):
    episodes = []
    for index in range(count):
        episodes.append({"run_id": f"rollout-{index}", "paths": [paths[(index + offset) % len(paths)]
            for offset in range(min(8, len(paths)))], "repository_revision": hashlib.sha256(str(index).encode()).hexdigest(),
            "status": "PARTIAL" if index % 5 == 0 else "FULL", "final_status": "PARTIAL" if index % 5 == 0 else "FULL",
            "result_ref": source_ref(canonical({"run": index}), "acceptance.rollout/v1").to_dict(),
            "outcome": {"epoch": index, "partition_count": 1536, "replication_factor": 3, "nodes": 24,
                "moves": [{"from": (index + offset) % 24, "to": (index + offset + 7) % 24,
                           "count": (index + offset) % 31, "observed_epoch": index} for offset in range(8)],
                "obligations": {"anti_affinity": "verified", "capacity": "verified", "rollback": "pending"}}})
    return episodes


def migration_frame(*, version=3, paths=4, symbols=2, episodes=8):
    paths = [f"services/s{index:02d}/routing.py" for index in range(paths)]
    snapshot, bindings = _sources(paths, symbols)
    resolution = {"elements": [{"binding": item} for item in bindings], "effect_paths": paths,
                  "repository_revision": REVISION}
    model = build_project_model(project_identity="cross-service-migration", resolution=resolution, source_snapshot=snapshot)
    frame = build_active_memory_frame(model=model, task=_task(paths), source_snapshot=snapshot, prior_episodes=_history(paths, episodes))
    frame["schema_version"] = f"synapse.stage4.gold.active-memory-frame/v{version}"
    if version >= 2:
        frame["layers"] = {"learned": [{"claim": {"patch_sha256": "a" * 64}, "status": "CONFIRMED", "proof": PRIVATE}]}
    if version == 3:
        frame["court"] = {"decision": {"proof": PRIVATE}, "automatic_patches": ["a" * 64],
            "judged_episodes": episodes + 2, "omitted_episodes": 2, "pending": [{"proof": PRIVATE}], "subjects": []}
        for item in frame["elements"]:
            item["memory"]["Evidence"]["private_proof"] = PRIVATE
            for episode in item["memory"]["Episodic"]["task_outcomes"]:
                episode["observation"] = {"requirement": {"outcome": "UNCERTAIN"}, "proof": PRIVATE}
    return frame
