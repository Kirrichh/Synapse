"""Two real canonical runs sharing the first run's committed negative evidence."""

from dataclasses import replace
import hashlib
import json

from acceptance.stage4.stage11._project_inputs import project_input_case
from acceptance.stage4.stage11._oracle_process import create_oracle_process
from synapse.experiments.gold.persistence import read_committed_snapshot_transaction
from synapse.experiments.gold.contracts import record_id_reference_from_dict
from synapse.experiments.gold.stage10.context_codec import decode_canonical, encode_canonical


def observed_reuse_case(root):
    producer = project_input_case(root, outcomes=("PATCH", "PATCH"))
    create_oracle_process(root / "harness", (False,))
    code, pending = producer.start()
    assert code == 3, pending
    code, result = producer.approve(pending)
    assert code == 0, result
    original = result["result"]["structured_outcome"]
    assert original["payload"]["status"] == "VERIFIED_REUSABLE_PARTIAL", result
    publication_root = producer.state_root / "publications"
    transactions = list((publication_root / "committed").iterdir())
    assert len(transactions) == 1
    _, members = read_committed_snapshot_transaction(publication_root / "prepared", transaction_id=transactions[0].name)
    request = decode_canonical(members["request.json"])
    raw_by_digest = {hashlib.sha256(raw).hexdigest(): raw for raw in members.values()}
    for name in ("domain", "policy_input", "environment_input", "host_abi_input", "builder_input"):
        raw = encode_canonical(request[name])
        raw_by_digest[hashlib.sha256(raw).hexdigest()] = raw
    knowledge = json.loads(producer.knowledge_path.read_text())
    existing = {encode_canonical(item["ref"]) for item in knowledge["files"]}

    def retain_refs(value):
        if isinstance(value, list):
            for item in value:
                retain_refs(item)
        elif isinstance(value, dict):
            if {"kind", "ref_id", "sha256", "byte_length", "schema_id", "media_type"} == set(value):
                digest = value["sha256"]
                if digest in raw_by_digest and encode_canonical(value) not in existing:
                    path = root / "evidence" / digest
                    path.write_bytes(raw_by_digest[digest])
                    knowledge["files"].append({"ref": value, "path": str(path)})
                    existing.add(encode_canonical(value))
            else:
                for item in value.values():
                    retain_refs(item)

    retain_refs(request)
    # Conditions use their own wire vocabulary, with the same retained bytes.
    condition = request["unit"]["core"]["input_contract"]["preconditions"][0]
    from synapse.experiments.gold.behavior import ConditionRef
    from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
    condition = ConditionRef.from_dict(condition)
    retain_refs(HashBoundRef(RefKind.CONTRACT_CONDITION, condition.condition_id, condition.condition_schema_id,
        condition.sha256, condition.byte_length, condition.media_type).to_dict())
    knowledge["conflicts"] = [{"left": knowledge["candidates"][0]["unit"]["content_key"]["value"],
        "right": request["unit"]["content_key"]["value"], "kind": None,
        "evidence_refs": request["attestation"]["verification_refs"]}]
    knowledge["candidates"].append({
        "unit": request["unit"], "manifest_id": request["manifest"]["manifest_id"],
        "attestation": request["attestation"], "bindings": [], "lifecycle_context": request["lifecycle_context"],
        "taint": {"profiles": [request["taint"]], "derivations": [], "decisions": [], "root_id": record_id_reference_from_dict(request["taint"]["profile_id"]).value},
    })
    consumer = replace(producer, run_root=root / "consumer-run", input_path=root / "consumer-input.json",
                       knowledge_path=root / "consumer-knowledge.json", cli_timeout_seconds=600)
    consumer.knowledge_path.write_text(json.dumps(knowledge))
    inputs = json.loads(producer.input_path.read_text())
    inputs.update(run_id="observed-reuse-consumer", knowledge_path=str(consumer.knowledge_path))
    consumer.input_path.write_text(json.dumps(inputs))
    return producer, consumer, original, request
