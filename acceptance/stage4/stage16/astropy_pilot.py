"""Pinned Astropy task preparation and calibration of the existing SWE-bench oracle.

This module prepares external experiment data. It never supplies Gold knowledge,
admission decisions, model responses or task outcomes. Calibration patches belong
to the evaluator and are not inputs to a worker run.
"""

from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

from synapse.experiments.swebench.contract import BaselineTask
from synapse.experiments.swebench.swebench_harness_oracle import (
    SWEbenchHarnessOracleConfig, SWEbenchHarnessOracleRunner,
)
from synapse.worker.provider_transport import GEMINI_CHAT_ENDPOINT

from .protocol import canonical, source, read_source


INSTANCE = "astropy__astropy-12907"
BASE = "d16bfe05a744909de4b27f5875fe0d4ed41ce607"
DATASET_REVISION = "78f471bf655a3137b2e8a75af1501690ec009ec3"
DATASET_SHA256 = "030cfd7f2a704c4c0226e7f104c725a3b41230b1d3517f9c915ad7ea5be3fa25"
DATASET_URL = ("https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified/resolve/"
               + DATASET_REVISION + "/data/test-00000-of-00001.parquet")
SCOPE = "astropy/modeling/separable.py"
SWEBENCH_VERSION = "4.1.0"


def prepare_astropy(root: Path) -> dict:
    """Fetch hash-pinned source data; leave all live allocations unexecuted."""
    import pyarrow.parquet as parquet

    root = root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    parquet_path = root / "verified.parquet"
    with urllib.request.urlopen(DATASET_URL, timeout=60) as response:
        raw = response.read(7_000_000)
    if hashlib.sha256(raw).hexdigest() != DATASET_SHA256:
        raise ValueError("SWE-bench bytes differ from the frozen dataset")
    parquet_path.write_bytes(raw)
    rows = parquet.read_table(parquet_path, filters=[("instance_id", "=", INSTANCE)]).to_pylist()
    if len(rows) != 1 or rows[0]["base_commit"] != BASE or rows[0]["repo"] != "astropy/astropy":
        raise ValueError("the selected task does not match the frozen source revision")
    row = rows[0]
    if not row["FAIL_TO_PASS"] or not row["PASS_TO_PASS"]:
        raise ValueError("the task lacks one of its independent oracle test groups")
    evaluator = root / "evaluator"
    evaluator.mkdir()
    dataset = evaluator / "instance.json"
    dataset.write_bytes(canonical([row]))
    public_task = root / "task.json"
    public_task.write_bytes(canonical({"task_id": INSTANCE, "instance_id": INSTANCE,
        "statement": row["problem_statement"], "allowed_scope": [SCOPE]}))
    repo = root / "base-repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "fetch", "--depth=1",
                    "https://github.com/astropy/astropy.git", BASE], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "--detach", "-q", "FETCH_HEAD"], check=True)
    if subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip() != BASE:
        raise ValueError("Astropy checkout differs from the frozen base")
    value = {"schema_version": "synapse.acceptance.stage16.astropy-preparation/v1",
        "status": "PREPARED", "instance_id": INSTANCE, "base_revision": BASE,
        "dataset_revision": DATASET_REVISION, "dataset_ref": source(parquet_path),
        "evaluator_input_ref": source(dataset), "task_ref": source(public_task), "repo_root": str(repo),
        "fail_to_pass": row["FAIL_TO_PASS"], "pass_to_pass": row["PASS_TO_PASS"],
        "swebench_version": SWEBENCH_VERSION,
        "proposed_runs": {"pairs": 4, "max_attempts": 3, "model": "gemini-3.1-flash-lite",
            "endpoint": GEMINI_CHAT_ENDPOINT, "initial_order_seed": 17},
        "execution_status": "NOT_STARTED",
        "gold_precondition": "existing admitted task-compatible corpus with retained provenance; no fixture bootstrap"}
    (root / "preparation.json").write_bytes(canonical(value))
    return value


def calibrate_astropy(root: Path) -> dict:
    """Require a real negative and positive result from the product oracle adapter."""
    from swebench.harness.test_spec.test_spec import make_test_spec

    root = root.resolve()
    prepared = json.loads((root / "preparation.json").read_bytes())
    if (prepared["schema_version"] != "synapse.acceptance.stage16.astropy-preparation/v1"
            or prepared["base_revision"] != BASE or prepared["instance_id"] != INSTANCE):
        raise ValueError("unknown Astropy preparation")
    if version("swebench") != SWEBENCH_VERSION or prepared["swebench_version"] != SWEBENCH_VERSION:
        raise ValueError("the existing oracle command requires the pinned SWE-bench profile")
    row, = read_source(prepared["evaluator_input_ref"])
    task = BaselineTask(**read_source(prepared["task_ref"]))
    repo = Path(prepared["repo_root"])
    if (subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip() != BASE
            or subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"])):
        raise ValueError("calibration base is no longer clean and frozen")
    # Resolve the upstream image once, then use an immutable local tag for both
    # calibrations and the eventual experiment instead of pulling 'latest' again.
    image_name = make_test_spec(row, namespace="swebench").instance_image_key
    subprocess.run(["docker", "pull", image_name], check=True)
    image, = json.loads(subprocess.check_output(["docker", "image", "inspect", image_name]))
    tag = "synapse-" + image["Id"].removeprefix("sha256:")
    frozen_image = image_name.rsplit(":", 1)[0] + ":" + tag
    subprocess.run(["docker", "tag", image["Id"], frozen_image], check=True)
    calibration = root / "calibration"
    calibration.mkdir(exist_ok=False)
    results = []
    for label, expected in (("unchanged-behavior", False), ("reference-fix", True)):
        case = calibration / label
        case.mkdir()
        checkout = case / "repo"
        subprocess.run(["git", "clone", "--no-local", "-q", str(repo), str(checkout)], check=True)
        if expected:
            subprocess.run(["git", "-C", str(checkout), "apply", "-"], input=row["patch"].encode(), check=True)
        else:
            # The legacy oracle refuses an empty candidate without executing
            # tests. A comment-only patch exercises its real negative verdict.
            with (checkout / SCOPE).open("a") as stream:
                stream.write("\n# Synapse oracle calibration: original behavior remains unchanged.\n")
        patch_path = case / "candidate.patch"
        patch_path.write_bytes(subprocess.check_output(["git", "-C", str(checkout), "diff", "HEAD"]))
        config = SWEbenchHarnessOracleConfig(python_executable=Path(sys.executable),
            swebench_work_dir=case / "harness", dataset_name=prepared["evaluator_input_ref"]["path"],
            split="test", instance_timeout_seconds=600, process_timeout_seconds=900,
            max_workers=1, instance_image_tag=tag, model_name_or_path="synapse-astropy-calibration")
        observed = SWEbenchHarnessOracleRunner(config).verify(checkout, task)
        result_path = case / "result.json"
        result_path.write_bytes(canonical({"expected_resolved": expected, "actual": observed.to_dict(),
            "candidate_ref": source(patch_path)}))
        valid = (observed.resolved is expected and not observed.diagnostics.get("infra_error", True)
                 and bool(observed.diagnostics.get("oracle_managed_artifacts")))
        results.append({"case": label, "valid": valid, "result_ref": source(result_path),
                        "oracle_configuration": json.loads(json.dumps(asdict(config), default=str))})
    success = all(item["valid"] for item in results)
    result = {"schema_version": "synapse.acceptance.stage16.astropy-calibration/v1",
        "status": "CALIBRATED" if success else "CALIBRATION_FAILED",
        "preparation_ref": source(root / "preparation.json"), "image_id": image["Id"],
        "image_repo_digests": image.get("RepoDigests", []), "frozen_image": frozen_image,
        "swebench_version": version("swebench"), "cases": results, "live_model_calls": 0,
        "gold_execution_status": "NOT_STARTED"}
    (calibration / "result.json").write_bytes(canonical(result))
    return result
