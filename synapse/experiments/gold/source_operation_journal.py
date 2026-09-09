"""Durable source-operation checkpoints and committed history reads.

The source-operation journal has one writer/reader contract. It grants no
publication, execution or knowledge-admission authority.
"""
from pathlib import Path
import hashlib
import re
from .admission_journal import FileSnapshotFence, require_live_guard
from .canonicalization import HashBoundRef
from .persistence import (commit_snapshot_transaction, committed_transaction_exists,
    read_committed_snapshot_transaction, require_directory, ensure_directory, stage_snapshot_transaction, store_transaction)
from .source_verification import canonical, inspect_source_claim, MAX_EVIDENCE_BYTES, source_ref
from .stage10.context_codec import decode_canonical

SOURCE_CHECKPOINT_V2 = "synapse.stage4.gold.source-checkpoint/v2"
SOURCE_OBSERVATION_CHECKPOINT_V1 = "synapse.stage4.gold.source-observation-checkpoint/v1"
SOURCE_RESULT_CHECKPOINT_V2 = "synapse.stage4.gold.source-result-checkpoint/v2"
SOURCE_INPUT_BYTES_V1 = "synapse.stage4.gold.source-input-bytes/v1"


def write_checkpoint(root, name, payload, fence, guard, *, evidence=None):
    evidence = {} if evidence is None else evidence
    if sum(len(raw) for raw in evidence.values()) > MAX_EVIDENCE_BYTES:
        raise ValueError("source checkpoint exceeds its retained byte budget")
    members = {}
    for ref, value in evidence.items():
        if (type(ref) is not HashBoundRef or type(value) is not bytes
                or len(value) != ref.byte_length or hashlib.sha256(value).hexdigest() != ref.sha256):
            raise ValueError("source checkpoint evidence differs from its identity")
        members[ref.sha256] = value
    raw = canonical({"schema_version": SOURCE_CHECKPOINT_V2, "payload": payload,
                     "evidence_refs": [ref.to_dict() for ref in sorted(evidence, key=lambda ref: canonical(ref.to_dict()))]})
    members["record.json"] = raw
    with store_transaction(fence, guard=guard) as ticket:
        staged = stage_snapshot_transaction(root, transaction_id=name, members=members,
                                            maximum_bytes=MAX_EVIDENCE_BYTES, ticket=ticket)
        commit_snapshot_transaction(root, transaction_id=name, members=staged,
            boundary_id=hashlib.sha256(raw).hexdigest(), marker_sha256=hashlib.sha256(raw).hexdigest(), ticket=ticket)
    return hashlib.sha256(raw).hexdigest()


def read_checkpoint(root, name):
    value = _read_retained_checkpoint(root, name)[0]
    return value["result"] if value.get("schema_version") == SOURCE_RESULT_CHECKPOINT_V2 else value


def _read_retained_checkpoint(root, name):
    marker, members = read_committed_snapshot_transaction(root, transaction_id=name)
    raw = members.get("record.json")
    if (raw is None or marker["marker_sha256"] != hashlib.sha256(raw).hexdigest()
            or marker["boundary_id"] != hashlib.sha256(raw).hexdigest()):
        raise ValueError("source operation lost its committed checkpoint")
    value = decode_canonical(raw)
    digest = hashlib.sha256(raw).hexdigest()
    if type(value) is not dict:
        raise ValueError("source operation checkpoint must be an exact object")
    if value.get("schema_version") != SOURCE_CHECKPOINT_V2:
        if set(members) != {"record.json"}:
            raise ValueError("legacy source checkpoint has unexpected evidence")
        return value, {}, digest
    if (set(value) != {"schema_version", "payload", "evidence_refs"}
            or type(value["payload"]) is not dict or type(value["evidence_refs"]) is not list):
        raise ValueError("source checkpoint has an unknown shape")
    evidence = {}
    for item in value["evidence_refs"]:
        ref = HashBoundRef.from_dict(item)
        held = members.get(ref.sha256)
        if (ref in evidence or held is None or len(held) != ref.byte_length
                or hashlib.sha256(held).hexdigest() != ref.sha256):
            raise ValueError("source operation lost retained evidence")
        evidence[ref] = held
    if set(members) != {"record.json", *(ref.sha256 for ref in evidence)}:
        raise ValueError("source checkpoint has undeclared evidence")
    return value["payload"], evidence, digest


def read_captured_source_operation(state_root: Path, capture):
    """Reopen one immutable recorded prefix; later checkpoints do not alter it."""
    if (type(capture) is not dict or set(capture) != {"operation_key", "checkpoint_complete", "checkpoints"}
            or type(capture["checkpoint_complete"]) is not bool or type(capture["checkpoints"]) is not list):
        raise ValueError("source operation capture has an unknown shape")
    key = capture["operation_key"]
    if type(key) is not str or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("source operation capture has an invalid identity")
    directory = state_root / "source-operations" / key
    require_directory(directory)
    identities = {}
    for item in capture["checkpoints"]:
        if type(item) is not dict or set(item) != {"name", "sha256"} or item["name"] in identities:
            raise ValueError("source operation capture repeats or changes a checkpoint")
        name = item["name"]
        if name not in {"started", "result"} and re.fullmatch(r"(?:input|observation)-[0-9]{8}", name) is None:
            raise ValueError("source operation capture names an unknown checkpoint")
        identities[name] = item["sha256"]
    if list(identities) != sorted(identities):
        raise ValueError("source operation capture order differs")
    def present(name):
        return name in identities
    def read(name):
        value, evidence, digest = _read_retained_checkpoint(directory, name)
        if digest != identities[name]:
            raise ValueError("source operation checkpoint changed after capture")
        return value, evidence, digest
    if not present("started"):
        raise ValueError("source operation has no committed input")
    started, retained, head = read("started")
    claim = inspect_source_claim(started["claim"])
    if (started["claim_sha256"] != hashlib.sha256(canonical(claim)).hexdigest()
            or directory.name != hashlib.sha256(claim["operation_id"].encode()).hexdigest()):
        raise ValueError("source operation input identity differs")
    observations, inputs = [], []
    complete = capture["checkpoint_complete"]
    required_inputs = {HashBoundRef.from_dict(item["ref"]) for field in
                       ("policy_inputs", "environment_inputs", "tool_inputs") for item in claim[field]}
    input_names = sorted(name for name in identities if name.startswith("input-"))
    for sequence, name in enumerate(input_names, 1):
        if name != f"input-{sequence:08d}":
            raise ValueError("source input sequence is incomplete")
        value, evidence, digest = read(name)
        if (set(value) != {"input_ref", "observed_ref", "identity_matches", "previous_checkpoint_sha256"}
                or value["previous_checkpoint_sha256"] != head):
            raise ValueError("source input checkpoint chain differs")
        ref = HashBoundRef.from_dict(value["input_ref"])
        observed_ref = HashBoundRef.from_dict(value["observed_ref"])
        if set(evidence) != {observed_ref} or ref not in required_inputs or ref.to_dict() in [x["input_ref"] for x in inputs]:
            raise ValueError("source input checkpoint differs from retained bytes")
        raw = evidence[observed_ref]
        matches = len(raw) == ref.byte_length and hashlib.sha256(raw).hexdigest() == ref.sha256
        if (value["identity_matches"] is not matches
                or observed_ref != (ref if matches else source_ref(raw, SOURCE_INPUT_BYTES_V1))):
            raise ValueError("source input identity observation differs from retained bytes")
        inputs.append({key: value[key] for key in ("input_ref", "observed_ref", "identity_matches")})
        retained.update(evidence)
        head = digest
    names = sorted(name for name in identities if name.startswith("observation-"))
    if any(not item["identity_matches"] for item in inputs) and (names or any(not item["identity_matches"] for item in inputs[:-1])):
        raise ValueError("source verification continued after input identity mismatch")
    for sequence, name in enumerate(names, 1):
        if name != f"observation-{sequence:08d}":
            raise ValueError("source operation observation sequence is incomplete")
        value, evidence, digest = read(name)
        if (set(value) != {"schema_version", "sequence", "previous_checkpoint_sha256", "observation"}
                or value["schema_version"] != SOURCE_OBSERVATION_CHECKPOINT_V1
                or value["sequence"] != sequence or value["previous_checkpoint_sha256"] != head
                or value["observation"]["operation_id"] != claim["operation_id"]):
            raise ValueError("source operation observation chain differs")
        observations.append(value["observation"])
        retained.update(evidence)
        head = digest
    result = None
    if present("result"):
        result, evidence, _ = read("result")
        if result.get("schema_version") == SOURCE_RESULT_CHECKPOINT_V2:
            if (set(result) != {"schema_version", "observation_count", "observation_head_sha256", "result"}
                    or result["observation_count"] != len(observations)
                    or result["observation_head_sha256"] != head):
                raise ValueError("source result differs from its observed history")
            result = result["result"]
        retained.update(evidence)
    return {"claim": claim, "inputs": inputs, "observations": observations, "result": result,
        "checkpoint_complete": complete, "head_sha256": head, "evidence": retained,
        "journal_capture": capture}


def capture_source_operation(directory: Path, *, guard=None):
    """Capture committed identities while the caller holds this operation's fence."""
    fence = FileSnapshotFence(directory / "fence")
    if guard is None:
        with fence.exclusive() as held:
            return capture_source_operation(directory, guard=held)
    require_live_guard(guard, coordinator_id=fence.coordinator_id())
    complete = fence.current_epoch() % 2 == 0
    records = []
    for path in sorted(directory.iterdir()):
        if path.name == "fence":
            continue
        if path.name not in {"started", "result"} and re.fullmatch(r"(?:input|observation)-[0-9]{8}", path.name) is None:
            raise ValueError("source operation has an unknown checkpoint entry")
        if committed_transaction_exists(directory, transaction_id=path.name):
            _, _, digest = _read_retained_checkpoint(directory, path.name)
            records.append({"name": path.name, "sha256": digest})
        else:
            complete = False
    return {"operation_key": directory.name, "checkpoint_complete": complete, "checkpoints": records}

def source_operation_directories(state_root: Path):
    root = state_root / "source-operations"
    if not root.exists():
        return ()
    require_directory(root)
    directories = tuple(sorted(root.iterdir()))
    for directory in directories:
        if (len(directory.name) != 64 or any(c not in "0123456789abcdef" for c in directory.name)
                or directory.is_symlink() or not directory.is_dir()):
            raise ValueError("source operation journal contains an unknown entry")
    return directories


def read_source_operations(state_root: Path):
    return tuple(read_captured_source_operation(state_root, capture_source_operation(directory))
                 for directory in source_operation_directories(state_root))


def allocate_source_operation(state_root: Path, operation_key: str):
    """Allocate the existing journal under the source-inventory coordinator."""
    if re.fullmatch(r"[0-9a-f]{64}", operation_key) is None:
        raise ValueError("invalid source operation key")
    fence = FileSnapshotFence(state_root / "source-operations-coordinator")
    with fence.exclusive():
        root = state_root / "source-operations" / operation_key
        ensure_directory(root.parent)
        ensure_directory(root)
        return root
