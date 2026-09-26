"""Consolidate verified episode observations without creating a new authority.

Learning retains exact published assertions and their physical origins. It does
not infer independence from service names, retries or the number of runs. An
exact verified candidate is not a generalized habit: its next use still needs
the ordinary publication, admission, replay, freshness and C1 owners.
"""
from pathlib import Path

from .persistence import read_committed_snapshot_transaction
from .runner.records import RecordKind
from .source_verification import canonical, source_ref
from .stage10.context_codec import decode_canonical
from .stage10.task_contract import GoverningTaskContract
from .stage13.publication import REQUEST_SCHEMA_V3, REQUEST_SCHEMA_V4, reference
from .stage13.publication_store import PublicationResult, PUBLICATION_RESULT_V3
from .stage13.rejected_patch_profile import (
    VERIFIED_PATCH_GUARD_V1, REJECTED_PATCH_GUARD_V3,
    REJECTED_PATCH_GUARD_V4, REJECTED_PATCH_GUARD_V5,
)
from .stage14.read_traversal import publication_read_scope

EPISODE_LEARNING_V1 = "synapse.stage4.gold.episode-learning/v1"
MEMORY_LAYERS_V1 = "synapse.stage4.gold.memory-layers/v1"
MAX_LEARNED_ASSERTIONS = 128


@publication_read_scope()
def consolidate_episode(*, frozen, state, records, outcome_ref):
    """Read publications attached to this completed run, never a worker label."""
    if state.final_result is None:
        raise ValueError("learning requires a completed durable run")
    task = GoverningTaskContract.from_dict(frozen["declaration"]["task_contract"])
    root = Path(frozen["project_state_root"]) / "publications"
    assertions, unresolved = {}, []
    for attempt in state.attempts:
        if attempt.result is None:
            raise ValueError("a completed learning episode contains an unfinished attempt")
        record = records.get(kind=RecordKind.PUBLICATION_RESULT, key=str(attempt.attempt_index))
        if record is None or record.payload["state"] != "COMMITTED":
            unresolved.append({"attempt_index": attempt.attempt_index, "status": "PROVISIONAL",
                               "reason": "NO_COMMITTED_VERIFIED_ASSERTION"})
            continue
        publication = PublicationResult(root, record.payload["transaction_id"])
        result = publication.payload()  # Existing reader reopens the physical proof and lineage.
        if reference(result, PUBLICATION_RESULT_V3).to_dict() != record.payload["result_ref"]:
            raise ValueError("learning publication differs from its run attachment")
        _, members = read_committed_snapshot_transaction(root / "prepared", transaction_id=publication.transaction_id)
        request = decode_canonical(members["request.json"])
        domain, identity = request["domain"], request["identity"]
        if (request["schema_version"] not in {REQUEST_SCHEMA_V3, REQUEST_SCHEMA_V4}
                or identity["manifest_sha256"] != state.manifest.manifest_sha256
                or identity["context_sha256"] != attempt.context.context_sha256
                or domain["task_contract_ref"] != task.reference.to_dict()
                or domain["base_revision"] != task.repository_revision_sha256):
            raise ValueError("learning publication belongs to another episode or task")
        profile = request["unit"]["core"]["verification_contract"]["profile_id"]
        positive = profile == VERIFIED_PATCH_GUARD_V1
        if not positive and profile not in {REJECTED_PATCH_GUARD_V3, REJECTED_PATCH_GUARD_V4, REJECTED_PATCH_GUARD_V5}:
            raise ValueError("learning has no interpretation for this publication profile")
        facts = request["verification"]["payload"]
        c1 = facts["c1"]
        if (facts["failure_codes"] or facts["interrupted"] or facts["refused"] or c1 is None
                or c1["infra_error"] or c1["oracle_resolved"] is not positive
                or request["schema_version"] != (REQUEST_SCHEMA_V4 if positive else REQUEST_SCHEMA_V3)):
            raise ValueError("learning assertion lacks an independently verified outcome")
        claim = {key: value for key, value in domain.items() if key != "schema_version"}
        claim["repository_revision"] = claim.pop("base_revision")
        key = source_ref(canonical(claim), "synapse.stage4.gold.patch-assertion/v1").sha256
        origin = {"transaction_id": publication.transaction_id, "result_ref": record.payload["result_ref"],
                  "verification_ref": request["verification"]["verification_ref"],
                  "oracle_result_ref": c1["oracle_result_ref"], "report_ref": c1["report_ref"]}
        assertion = {"assertion_id": key, "claim": claim,
                     "status": "CONFIRMED" if positive else "REFUTED", "origin": origin,
                     "generalization": "NOT_ESTABLISHED"}
        assertions[canonical(origin)] = assertion
    if len(assertions) > MAX_LEARNED_ASSERTIONS:
        raise ValueError("episode learning exceeds its assertion budget")
    return {"schema_version": EPISODE_LEARNING_V1, "outcome_ref": outcome_ref,
            "assertions": sorted(assertions.values(), key=canonical), "unresolved": unresolved}


def build_memory_layers(*, task, source_snapshot, episodes, learning, run_memory_selection="ALL"):
    """Four layers supplement the eight element views; no confidence vote."""
    from .project_memory_selection import require_run_memory_selection
    require_run_memory_selection(run_memory_selection)
    known = {}
    for episode in learning:
        if episode["schema_version"] != EPISODE_LEARNING_V1:
            raise ValueError("memory layer has an unknown consolidation profile")
        for item in episode["assertions"]:
            if item["status"] not in {"CONFIRMED", "REFUTED"} or item["generalization"] != "NOT_ESTABLISHED":
                raise ValueError("learned assertion has an unsupported status or generalization")
            selected = "SUCCESS_ONLY" if item["status"] == "CONFIRMED" else "FAILURE_ONLY"
            if run_memory_selection not in {"ALL", selected}:
                continue
            claim = item["claim"]
            if (claim["repository_revision"] != task.repository_revision_sha256
                    or claim["task_contract_ref"] != task.reference.to_dict()):
                continue
            entry = known.setdefault(item["assertion_id"], {"assertion_id": item["assertion_id"],
                "claim": claim, "observations": {}, "generalization": "NOT_ESTABLISHED"})
            # A copied publication or resumed run contributes the same origin,
            # never another witness, confidence increment or generalized birth.
            observation = {"status": item["status"], "origin": item["origin"]}
            origin_key = canonical(item["origin"])
            if entry["claim"] != claim or (origin_key in entry["observations"] and entry["observations"][origin_key] != observation):
                raise ValueError("one learning identity cannot assert incompatible facts")
            entry["observations"][origin_key] = observation
    learned = []
    for entry in sorted(known.values(), key=lambda value: value["assertion_id"]):
        observations = sorted(entry["observations"].values(), key=canonical)
        states = {item["status"] for item in observations}
        learned.append({**entry, "observations": observations,
                        "status": next(iter(states)) if len(states) == 1 else "CONFLICTED"})
    if len(learned) > MAX_LEARNED_ASSERTIONS:
        raise ValueError("active learning exceeds its assertion budget")
    declared = [{"operation_id": item["experience"]["claim"]["operation_id"],
                 "recipe": item["experience"]["claim"]["recipe"], "status": "DECLARED"}
                for item in source_snapshot["recall"]["selected"] if item["experience"]["claim"]["recipe"] is not None]
    return {"schema_version": MEMORY_LAYERS_V1, "declared": declared, "learned": learned,
            "working": {"task_contract_ref": task.reference.to_dict(), "status": "AWAITING_FRESH_VERIFICATION"},
            "episodic": episodes}
