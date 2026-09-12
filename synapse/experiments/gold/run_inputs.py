"""Frozen experimental inputs and their durable run binding.

This owner records operator declarations and observed dependency identities.
It cannot supply authority decisions, load arbitrary Python factories or run
workers. Resume reopens exactly the input bytes the manifest names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil

from .project_memory_selection import PROJECT_KNOWLEDGE_INPUT_V4, require_run_memory_selection
from .contracts import RunId
from .persistence import read_regular_bytes
from .runner.models import GoldRunBudgets, GoldRunConfig, GoldRunManifest, GoldRunVersions, GoldReplicatePolicy
from .runner.vocabulary import FallbackPolicy
from .stage10.context_codec import encode_canonical, decode_canonical
from .stage10.task_contract import GoverningTaskContract, TASK_CONTRACT_SCHEMA_V3


EXPERIMENT_INPUT_SCHEMA_V2 = "synapse.stage4.gold.experiment-input/v2"
EXPERIMENT_INPUT_SCHEMA_V3 = "synapse.stage4.gold.experiment-input/v3"
FROZEN_INPUT_SCHEMA_V2 = "synapse.stage4.gold.frozen-input/v2"
FROZEN_INPUT_SCHEMA_V3 = "synapse.stage4.gold.frozen-input/v3"
FROZEN_INPUT_SCHEMA_V4 = "synapse.stage4.gold.frozen-input/v4"
FROZEN_INPUT_SCHEMA_V5 = "synapse.stage4.gold.frozen-input/v5"
FROZEN_INPUT_SCHEMA_V6 = "synapse.stage4.gold.frozen-input/v6"
PROJECT_KNOWLEDGE_INPUT_V3 = "synapse.stage4.gold.knowledge-input/v3"
EXPERIMENT_INPUT_SCHEMA_V1 = "synapse.stage4.gold.experiment-input/v1"
FROZEN_INPUT_SCHEMA_V1 = "synapse.stage4.gold.frozen-input/v1"
MAX_INPUT_BYTES = 16 * 1024 * 1024
_DECLARATION_FIELDS = {
    "schema_version", "run_id", "config", "versions", "task_contract", "target_records",
    "command_policy", "worker", "oracle", "actor_namespace", "observation", "knowledge_path", "replay_profile",
}


def _validate_declaration(declaration):
    if (type(declaration) is not dict or type(declaration.get("schema_version")) is not str
            or declaration["schema_version"] not in {EXPERIMENT_INPUT_SCHEMA_V1, EXPERIMENT_INPUT_SCHEMA_V2, EXPERIMENT_INPUT_SCHEMA_V3}):
        raise ValueError("experimental declaration has an unknown schema")
    automatic = declaration["schema_version"] == EXPERIMENT_INPUT_SCHEMA_V3
    fields = _DECLARATION_FIELDS - {"target_records"} if automatic else _DECLARATION_FIELDS
    if set(declaration) != fields:
        raise ValueError("experimental declaration has an unknown shape")
    task = GoverningTaskContract.from_dict(declaration["task_contract"])
    if automatic != (task.schema_version == TASK_CONTRACT_SCHEMA_V3):
        raise ValueError("experiment and task target-resolution schemas differ")
    if task.repository_revision_sha256 != declaration["config"]["base_revision"]:
        raise ValueError("task resolution revision differs from the run configuration")
    return task


def _captured_worker(declaration):
    return (declaration["schema_version"] == EXPERIMENT_INPUT_SCHEMA_V2
        or declaration["schema_version"] == EXPERIMENT_INPUT_SCHEMA_V3 and "accounting" in declaration["worker"])


def read_input_json(path: Path) -> dict[str, object]:
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("experimental JSON contains a duplicate key")
            result[key] = value
        return result
    raw = read_regular_bytes(path, maximum_bytes=MAX_INPUT_BYTES)
    def refuse_nonfinite(_):
        raise ValueError("non-finite input")
    result = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                        parse_constant=refuse_nonfinite)
    if type(result) is not dict:
        raise ValueError("experimental input must be a JSON object")
    return result


def runtime_source_digest() -> str:
    """Fingerprint actual runtime sources, including an uncommitted local build."""
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if path.is_symlink():
            raise ValueError("runtime source fingerprint refuses a symlink")
        raw = path.read_bytes()
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(raw).digest())
    return digest.hexdigest()


@dataclass(frozen=True)
class FrozenGoldInputs:
    canonical_bytes: bytes

    def __post_init__(self) -> None:
        if type(self.canonical_bytes) is not bytes or len(self.canonical_bytes) > MAX_INPUT_BYTES:
            raise ValueError("frozen experimental inputs exceed the contract limit")
        data = decode_canonical(self.canonical_bytes)
        automatic = type(data) is dict and data.get("schema_version") in {FROZEN_INPUT_SCHEMA_V5, FROZEN_INPUT_SCHEMA_V6}
        source_snapshot = type(data) is dict and data.get("schema_version") in {FROZEN_INPUT_SCHEMA_V4, FROZEN_INPUT_SCHEMA_V5, FROZEN_INPUT_SCHEMA_V6}
        source_accounting = (source_snapshot and type(data.get("declaration")) is dict
            and _captured_worker(data["declaration"]))
        fields = {
            "schema_version", "declaration", "knowledge", "project_state_root", "project_record_sha256",
            "trusted_heads", "repo_root", "run_root", "frozen_at_utc", "runtime_sha256", "worker_files",
        }
        if type(data) is dict and data.get("schema_version") in {FROZEN_INPUT_SCHEMA_V2, FROZEN_INPUT_SCHEMA_V3} or source_accounting:
            fields.add("worker_runtime")
        if type(data) is dict and data.get("schema_version") == FROZEN_INPUT_SCHEMA_V3 or source_accounting:
            from synapse.resource_usage import RESOURCE_PROFILE
            fields.add("resource_profile")
            if data.get("resource_profile") != RESOURCE_PROFILE:
                raise ValueError("frozen resource observation profile differs")
        if source_snapshot:
            fields.add("source_snapshot")
        if automatic:
            fields.add("target_resolution")
        if type(data) is dict and data.get("schema_version") == FROZEN_INPUT_SCHEMA_V6:
            from .stage10.planning_basis import METHOD_SELECTION_V1
            fields.add("planning_profile")
            if data.get("planning_profile") != METHOD_SELECTION_V1:
                raise ValueError("frozen method-selection profile differs")
        if type(data) is not dict or set(data) != fields or data["schema_version"] not in {FROZEN_INPUT_SCHEMA_V1, FROZEN_INPUT_SCHEMA_V2, FROZEN_INPUT_SCHEMA_V3, FROZEN_INPUT_SCHEMA_V4, FROZEN_INPUT_SCHEMA_V5, FROZEN_INPUT_SCHEMA_V6}:
            raise ValueError("frozen experimental input has an unknown shape")
        declaration = data["declaration"]
        _validate_declaration(declaration)
        if automatic != (declaration["schema_version"] == EXPERIMENT_INPUT_SCHEMA_V3):
            raise ValueError("frozen task-resolution schema differs from the declaration")
        modern = _captured_worker(declaration)
        if modern != (data["schema_version"] in {FROZEN_INPUT_SCHEMA_V2, FROZEN_INPUT_SCHEMA_V3} or source_accounting):
            raise ValueError("experiment and frozen accounting schemas differ")
        if modern:
            from .stage15.worker_accounting import validate_accounting_declaration
            validate_accounting_declaration(declaration["worker"])
            if type(data["worker_runtime"]) is not dict or set(data["worker_runtime"]) != {"profile", "distributions"}:
                raise ValueError("frozen worker runtime is not an exact accounting dependency identity")
        elif "accounting" in declaration["worker"]:
            raise ValueError("historical experiment schema cannot claim Stage 15 capture")
        GoverningTaskContract.from_dict(declaration["task_contract"])
        if source_snapshot:
            snapshot = data["source_snapshot"]
            snapshot_fields = {"schema_version", "project_state_root", "project_record_sha256",
                               "task_contract", "operations", "publications", "recall"}
            if type(snapshot) is dict:
                if ((data["schema_version"] == FROZEN_INPUT_SCHEMA_V6)
                        != (snapshot.get("schema_version") == "synapse.stage4.gold.source-experience-snapshot/v3")):
                    raise ValueError("active memory profile differs from the frozen input version")
                if snapshot.get("schema_version") == "synapse.stage4.gold.source-experience-snapshot/v3":
                    snapshot_fields.update({"source_schema_version", "project_memory", "run_memory_selection"})
                    require_run_memory_selection(snapshot.get("run_memory_selection"))
                    from .project_memory_store import memory_job_identity
                    if (snapshot.get("project_memory", {}).get("job_key") != memory_job_identity(
                            data["project_record_sha256"], data["run_root"], declaration["run_id"])):
                        raise ValueError("active memory belongs to another run lifecycle")
                if (snapshot.get("source_schema_version", snapshot.get("schema_version"))
                        == "synapse.stage4.gold.source-experience-snapshot/v2"):
                    snapshot_fields.add("run_publications")
            if (type(snapshot) is not dict
                    or set(snapshot) != snapshot_fields
                    or snapshot["schema_version"] not in {"synapse.stage4.gold.source-experience-snapshot/v1",
                                                         "synapse.stage4.gold.source-experience-snapshot/v2",
                                                         "synapse.stage4.gold.source-experience-snapshot/v3"}
                    or snapshot["task_contract"] != declaration["task_contract"]
                    or snapshot["project_state_root"] != data["project_state_root"]
                    or snapshot["project_record_sha256"] != data["project_record_sha256"]):
                raise ValueError("frozen source experience differs from its run binding")
        for field in ("project_state_root", "repo_root", "run_root"):
            if type(data[field]) is not str or not Path(data[field]).is_absolute():
                raise ValueError(f"frozen {field} must be absolute")
        moment = datetime.fromisoformat(data["frozen_at_utc"])
        if moment.tzinfo is None or moment.utcoffset().total_seconds() != 0:
            raise ValueError("freeze time must use UTC")

    @property
    def data(self) -> dict[str, object]:
        return decode_canonical(self.canonical_bytes)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    @property
    def manifest(self) -> GoldRunManifest:
        declaration = self.data["declaration"]
        config = dict(declaration["config"])
        config["budgets"] = GoldRunBudgets(**config["budgets"])
        config["replicate_policy"] = GoldReplicatePolicy(**config["replicate_policy"])
        config["fallback_policy"] = FallbackPolicy(config["fallback_policy"])
        return GoldRunManifest.create(
            run_id=RunId(declaration["run_id"]), gold_run_id=declaration["run_id"],
            config=GoldRunConfig(**config), versions=GoldRunVersions(**declaration["versions"]),
            inputs_sha256=self.sha256,
        )

    def verify_runtime(self, run_root: Path) -> None:
        data = self.data
        if Path(data["run_root"]) != run_root or data["runtime_sha256"] != runtime_source_digest():
            raise ValueError("run location or runtime sources differ from the frozen experiment")
        if "worker_runtime" in data:
            from synapse.worker.provider_transport import frozen_mini_runtime
            if data["worker_runtime"] != frozen_mini_runtime(data["declaration"]["worker"]["command"]):
                raise ValueError("captured SDK implementation differs from the frozen run")
        self.verify_project()
        if "target_resolution" in data:
            self.resolve_targets()
        if "source_snapshot" in data:
            from .source_snapshot import read_source_snapshot
            read_source_snapshot(data["source_snapshot"], task=GoverningTaskContract.from_dict(data["declaration"]["task_contract"]))
        for item in data["worker_files"]:
            if hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("worker executable or declared command file changed since freeze")

    def verify_project(self) -> None:
        data = self.data
        record = read_regular_bytes(Path(data["project_state_root"]) / "project.json", maximum_bytes=MAX_INPUT_BYTES)
        if hashlib.sha256(record).hexdigest() != data["project_record_sha256"]:
            raise ValueError("project declaration changed since the experiment was frozen")

    def resolve_targets(self):
        """Reopen target evidence using the declared historical or automatic path."""
        from .bindings import binding_from_dict, binding_to_ref
        from .contracts import RepositoryRevision
        from .task_targets import read_task_targets
        data = self.data
        task = GoverningTaskContract.from_dict(data["declaration"]["task_contract"])
        repo = Path(data["repo_root"])
        if task.schema_version == TASK_CONTRACT_SCHEMA_V3:
            return read_task_targets(encode_canonical(data["target_resolution"]), task=task, repository_root=repo)
        targets = tuple(binding_from_dict(item, repo_root=repo,
            consumer_revision=RepositoryRevision.git_commit(task.repository_revision_sha256))
            for item in data["declaration"]["target_records"])
        if tuple(binding_to_ref(item) for item in targets) != task.target_bindings:
            raise ValueError("governing target refs differ from resolved records")
        return targets


def freeze_gold_inputs(*, declaration_path: Path, project, run_root: Path) -> FrozenGoldInputs:
    from synapse.resource_usage import RESOURCE_PROFILE
    declaration = read_input_json(declaration_path)
    task = _validate_declaration(declaration)
    target_resolution = None
    if task.schema_version == TASK_CONTRACT_SCHEMA_V3:
        from .task_targets import resolve_task_targets
        target_resolution = decode_canonical(resolve_task_targets(task=task, repository_root=project.declaration.repo_root))
    knowledge_path = Path(declaration["knowledge_path"])
    if not knowledge_path.is_absolute():
        knowledge_path = declaration_path.parent / knowledge_path
    knowledge = read_input_json(knowledge_path)
    if target_resolution is not None and knowledge.get("schema_version") not in {PROJECT_KNOWLEDGE_INPUT_V3, PROJECT_KNOWLEDGE_INPUT_V4}:
        raise ValueError("automatic tasks require project knowledge selection")
    source_snapshot, source_heads = None, None
    if knowledge.get("schema_version") in {PROJECT_KNOWLEDGE_INPUT_V3, PROJECT_KNOWLEDGE_INPUT_V4}:
        from .canonicalization import HashBoundRef
        from .source_snapshot import capture_project_source_snapshot
        fields = {"schema_version", "files", "experience_limit"}
        selection = "ALL"
        if knowledge["schema_version"] == PROJECT_KNOWLEDGE_INPUT_V4:
            fields.add("run_memory_selection")
            if target_resolution is None:
                raise ValueError("memory class selection requires automatic task inputs")
            selection = require_run_memory_selection(knowledge.get("run_memory_selection"))
        if set(knowledge) != fields or type(knowledge["files"]) is not list:
            raise ValueError("project knowledge input has an unknown selection contract")
        support = knowledge["files"]
        source_snapshot, knowledge, source_heads = capture_project_source_snapshot(project=project,
            task=task, limit=knowledge["experience_limit"], target_resolution=target_resolution,
            run_root=run_root, run_id=declaration["run_id"], run_memory_selection=selection)
        references = {HashBoundRef.from_dict(item["ref"]) for item in knowledge["files"]}
        supplied = set()
        for item in support:
            if type(item) is not dict or set(item) != {"ref", "path"}:
                raise ValueError("project support evidence requires a reference and path")
            ref = HashBoundRef.from_dict(item["ref"])
            path = Path(item["path"])
            path = path if path.is_absolute() else knowledge_path.parent / path
            raw = read_regular_bytes(path, maximum_bytes=MAX_INPUT_BYTES)
            if ref in supplied or len(raw) != ref.byte_length or hashlib.sha256(raw).hexdigest() != ref.sha256:
                raise ValueError("project support evidence is repeated or changed")
            supplied.add(ref)
            if ref not in references:
                knowledge["files"].append({"ref": ref.to_dict(), "path": str(path.resolve())})
    command = declaration["worker"]["command"]
    if type(command) is not list or not command or any(type(item) is not str or not item or "\0" in item for item in command):
        raise ValueError("worker command must be non-empty argv")
    executable = shutil.which(command[0])
    if executable is None:
        raise ValueError("worker executable is unavailable")
    command[0] = str(Path(executable).resolve())
    worker_files = [{"path": command[0], "sha256": hashlib.sha256(Path(command[0]).read_bytes()).hexdigest()}]
    worker_runtime = None
    if _captured_worker(declaration):
        from synapse.worker.provider_transport import frozen_mini_runtime
        from .stage15.worker_accounting import validate_accounting_declaration
        validate_accounting_declaration(declaration["worker"])
        worker_runtime = frozen_mini_runtime(command)
    elif "accounting" in declaration["worker"]:
        raise ValueError("captured workers require experiment input v2")
    state_root = project.declaration.state_root
    with project.fence.exclusive():
        if project.fence.current_epoch() % 2:
            raise ValueError("project has an unfinished mutation interval")
        record = read_regular_bytes(state_root / "project.json", maximum_bytes=MAX_INPUT_BYTES)
        heads = {
            "lifecycle": project.lifecycle_store.current_anchor().to_dict(),
            "provenance": project.attestation_store.current_anchor().to_dict(),
            "taint": project.taint_store.current_anchor().to_dict(),
        }
        if source_heads is not None:
            heads = source_heads
            if hashlib.sha256(record).hexdigest() != source_snapshot["project_record_sha256"]:
                raise ValueError("project changed while its source experience was frozen")
    from .stage10.planning_basis import METHOD_SELECTION_V1
    return FrozenGoldInputs(encode_canonical({
        "schema_version": FROZEN_INPUT_SCHEMA_V6 if target_resolution is not None else (FROZEN_INPUT_SCHEMA_V4 if source_snapshot is not None else (FROZEN_INPUT_SCHEMA_V1 if worker_runtime is None else FROZEN_INPUT_SCHEMA_V3)), "declaration": declaration, "knowledge": knowledge,
        "project_state_root": str(state_root), "project_record_sha256": hashlib.sha256(record).hexdigest(),
        "trusted_heads": heads, "repo_root": str(project.declaration.repo_root), "run_root": str(run_root),
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(), "runtime_sha256": runtime_source_digest(),
        "worker_files": worker_files,
        **({"source_snapshot": source_snapshot} if source_snapshot is not None else {}),
        **({"target_resolution": target_resolution, "planning_profile": METHOD_SELECTION_V1} if target_resolution is not None else {}),
        **({"worker_runtime": worker_runtime, "resource_profile": RESOURCE_PROFILE} if worker_runtime is not None else {}),
    }))


def persist_frozen_inputs(inputs: FrozenGoldInputs, run_root: Path) -> None:
    """Publish inputs before controller execution; an incomplete allocation is refused."""
    run_root.mkdir(parents=True, exist_ok=False)
    path = run_root / "experiment.json"
    with path.open("xb") as stream:
        stream.write(inputs.canonical_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != "nt":
        descriptor = os.open(run_root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def reopen_frozen_inputs(run_root: Path) -> FrozenGoldInputs:
    inputs = FrozenGoldInputs(read_regular_bytes(run_root / "experiment.json", maximum_bytes=MAX_INPUT_BYTES))
    inputs.verify_runtime(run_root)
    return inputs
