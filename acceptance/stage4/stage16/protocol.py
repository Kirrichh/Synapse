"""Frozen external experiment design (§§32–33); never runtime configuration.

The operator supplies arm input files before execution. Scheduling, allocation
and measurement scope are evidence about a comparison, not Gold authority.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess


SCHEMA = "synapse.acceptance.stage16.protocol/v1"
ARMS = ("BASELINE", "GOLD")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def source(path):
    path = Path(path).absolute()
    if path.is_symlink() or not path.is_file():
        raise ValueError("experiment input must be a regular file")
    raw = path.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def read_source(ref):
    if type(ref) is not dict or set(ref) != {"path", "sha256", "bytes"}:
        raise ValueError("invalid external source reference")
    path = Path(ref["path"])
    if path.is_symlink() or not path.is_file():
        raise ValueError("retained experiment source is unavailable")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref["sha256"] or len(raw) != ref["bytes"]:
        raise ValueError("retained experiment source changed")
    return json.loads(raw)


def code_identity(root):
    root = Path(root).resolve()
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    paths = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z", "synapse", "pyproject.toml"])
    files = [(name, hashlib.sha256((root / name).read_bytes()).hexdigest())
             for name in sorted(filter(None, paths.decode().split("\0")))]
    return {"revision": revision, "runtime_sha256": digest(files)}


@dataclass(frozen=True)
class Protocol:
    """One immutable preregistration, including every expected pair and arm."""

    raw: bytes

    def __post_init__(self):
        value = json.loads(self.raw)
        if canonical(value) != self.raw or set(value) != {
            "schema_version", "experiment_id", "code", "specification", "seed", "pairs",
            "minimum_activated_pairs", "measurement_scope", "reporting_policy",
        } or value["schema_version"] != SCHEMA:
            raise ValueError("unknown experiment protocol")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value["experiment_id"]):
            raise ValueError("invalid experiment identity")
        if type(value["seed"]) is not int or not 0 <= value["seed"] < 2**63:
            raise ValueError("the allocation seed must be frozen")
        code, spec = value["code"], value["specification"]
        if (set(code) != {"revision", "runtime_sha256"} or not re.fullmatch(r"[0-9a-f]{40}", code["revision"])
                or not re.fullmatch(r"[0-9a-f]{64}", code["runtime_sha256"])
                or set(spec) != {"version", "sha256"} or not spec["version"]
                or not re.fullmatch(r"[0-9a-f]{64}", spec["sha256"])):
            raise ValueError("exact code and governing specification identities are required")
        if value["reporting_policy"] != "ALL_ATTEMPTS_ALL_PAIRS_NO_IMPUTATION":
            raise ValueError("selective reporting is forbidden")
        if value["measurement_scope"] != "ACTUAL_FULL_RUN_DIAGNOSTICS":
            raise ValueError("this acceptance platform does not estimate counterfactual token credits")
        pairs = value["pairs"]
        if type(pairs) is not list or not pairs:
            raise ValueError("an experiment requires declared pairs")
        seen, allocations, paths = set(), set(), set()
        for pair in pairs:
            if set(pair) != {"pair_id", "task_id", "replicate_id", "inputs"}:
                raise ValueError("invalid pair declaration")
            if not all(type(pair[name]) is str and pair[name] for name in ("pair_id", "task_id")):
                raise ValueError("pair and task identities are required")
            if type(pair["replicate_id"]) is not int or pair["replicate_id"] < 0:
                raise ValueError("replicate identity must be a nonnegative integer")
            allocation = pair["task_id"], pair["replicate_id"]
            if pair["pair_id"] in seen or allocation in allocations or set(pair["inputs"]) != set(ARMS):
                raise ValueError("duplicate pair, replicate, or missing arm")
            seen.add(pair["pair_id"])
            allocations.add(allocation)
            for ref in pair["inputs"].values():
                if (set(ref) != {"path", "sha256", "bytes"} or not Path(ref["path"]).is_absolute()
                        or not re.fullmatch(r"[0-9a-f]{64}", ref["sha256"])
                        or type(ref["bytes"]) is not int or ref["bytes"] <= 0):
                    raise ValueError("arm inputs require exact retained file identities")
                if ref["path"] in paths:
                    raise ValueError("each arm and replicate requires an independent input allocation")
                paths.add(ref["path"])
        minimum = value["minimum_activated_pairs"]
        if type(minimum) is not int or not 1 <= minimum <= len(pairs):
            raise ValueError("freeze a reachable positive activation threshold")

    @property
    def identity(self):
        return hashlib.sha256(self.raw).hexdigest()

    def payload(self):
        return json.loads(self.raw)

    def schedule(self):
        value = self.payload()
        # Counterbalance within each task. Hash ordering is stable across Python
        # versions and records both allocation and execution order explicitly.
        ordered = sorted(value["pairs"], key=lambda p: (p["task_id"], p["replicate_id"]))
        by_task, blocks = {}, []
        for pair in ordered:
            index = by_task.get(pair["task_id"], 0)
            by_task[pair["task_id"]] = index + 1
            flip = (int(digest([value["seed"], pair["task_id"]]), 16) + index) % 2
            order = ARMS if flip == 0 else tuple(reversed(ARMS))
            blocks.append((digest([value["seed"], pair["pair_id"]]), pair, order))
        result = []
        for _, pair, order in sorted(blocks, key=lambda item: item[0]):
            for arm in order:
                result.append({"ordinal": len(result), "pair_id": pair["pair_id"], "task_id": pair["task_id"],
                    "replicate_id": pair["replicate_id"], "arm": arm, "input_ref": pair["inputs"][arm],
                    "slot_id": digest([self.identity, pair["pair_id"], arm])})
        return result

    def validate_inputs(self):
        roots = []
        for slot in self.schedule():
            data = read_source(slot["input_ref"])
            if data.get("arm") != slot["arm"]:
                raise ValueError("arm cannot be relabelled")
            for name in ("run_root", "repo_root"):
                if name not in data or not Path(data[name]).is_absolute():
                    raise ValueError("arm storage and repository locations must be explicit")
                path = Path(data[name]).resolve()
                if any(path == old or path in old.parents or old in path.parents for old in roots):
                    raise ValueError("arm or replicate storage overlaps: carry leakage")
                roots.append(path)
            if slot["arm"] == "GOLD":
                state = Path(data["state_root"]).resolve()
                if any(state == old or state in old.parents or old in state.parents for old in roots):
                    raise ValueError("Gold knowledge state must be isolated")
                roots.append(state)


def preregister(*, experiment_id, pairs, repository, specification, seed=0, minimum_activated_pairs=1):
    value = {"schema_version": SCHEMA, "experiment_id": experiment_id, "code": code_identity(repository),
        "specification": specification, "seed": seed, "pairs": pairs,
        "minimum_activated_pairs": minimum_activated_pairs,
        "measurement_scope": "ACTUAL_FULL_RUN_DIAGNOSTICS",
        "reporting_policy": "ALL_ATTEMPTS_ALL_PAIRS_NO_IMPUTATION"}
    protocol = Protocol(canonical(value))
    protocol.validate_inputs()
    return protocol
