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

from .contracts import RunId
from .persistence import read_regular_bytes
from .runner.models import GoldRunBudgets, GoldRunConfig, GoldRunManifest, GoldRunVersions, GoldReplicatePolicy
from .runner.vocabulary import FallbackPolicy
from .stage10.context_codec import encode_canonical, decode_canonical
from .stage10.task_contract import GoverningTaskContract


EXPERIMENT_INPUT_SCHEMA_V1 = "synapse.stage4.gold.experiment-input/v1"
FROZEN_INPUT_SCHEMA_V1 = "synapse.stage4.gold.frozen-input/v1"
MAX_INPUT_BYTES = 16 * 1024 * 1024
_DECLARATION_FIELDS = {
    "schema_version", "run_id", "config", "versions", "task_contract", "target_records",
    "command_policy", "worker", "oracle", "actor_namespace", "observation", "knowledge_path", "replay_profile",
}


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
        if type(data) is not dict or set(data) != {
            "schema_version", "declaration", "knowledge", "project_state_root", "project_record_sha256",
            "trusted_heads", "repo_root", "run_root", "frozen_at_utc", "runtime_sha256", "worker_files",
        } or data["schema_version"] != FROZEN_INPUT_SCHEMA_V1:
            raise ValueError("frozen experimental input has an unknown shape")
        declaration = data["declaration"]
        if type(declaration) is not dict or set(declaration) != _DECLARATION_FIELDS or declaration["schema_version"] != EXPERIMENT_INPUT_SCHEMA_V1:
            raise ValueError("experimental declaration has an unknown shape")
        GoverningTaskContract.from_dict(declaration["task_contract"])
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
        self.verify_project()
        for item in data["worker_files"]:
            if hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("worker executable or declared command file changed since freeze")

    def verify_project(self) -> None:
        data = self.data
        record = read_regular_bytes(Path(data["project_state_root"]) / "project.json", maximum_bytes=MAX_INPUT_BYTES)
        if hashlib.sha256(record).hexdigest() != data["project_record_sha256"]:
            raise ValueError("project declaration changed since the experiment was frozen")


def freeze_gold_inputs(*, declaration_path: Path, project, run_root: Path) -> FrozenGoldInputs:
    declaration = read_input_json(declaration_path)
    if set(declaration) != _DECLARATION_FIELDS or declaration.get("schema_version") != EXPERIMENT_INPUT_SCHEMA_V1:
        raise ValueError("experimental declaration has an unknown shape")
    knowledge_path = Path(declaration["knowledge_path"])
    if not knowledge_path.is_absolute():
        knowledge_path = declaration_path.parent / knowledge_path
    knowledge = read_input_json(knowledge_path)
    command = declaration["worker"]["command"]
    if type(command) is not list or not command or any(type(item) is not str or not item or "\0" in item for item in command):
        raise ValueError("worker command must be non-empty argv")
    executable = shutil.which(command[0])
    if executable is None:
        raise ValueError("worker executable is unavailable")
    command[0] = str(Path(executable).resolve())
    worker_files = [{"path": command[0], "sha256": hashlib.sha256(Path(command[0]).read_bytes()).hexdigest()}]
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
    return FrozenGoldInputs(encode_canonical({
        "schema_version": FROZEN_INPUT_SCHEMA_V1, "declaration": declaration, "knowledge": knowledge,
        "project_state_root": str(state_root), "project_record_sha256": hashlib.sha256(record).hexdigest(),
        "trusted_heads": heads, "repo_root": str(project.declaration.repo_root), "run_root": str(run_root),
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(), "runtime_sha256": runtime_source_digest(),
        "worker_files": worker_files,
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
