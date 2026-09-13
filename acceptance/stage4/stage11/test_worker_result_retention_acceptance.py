"""Real worker output survives Gold's checkpoint and a separate reader process.

The worker is an acceptance process, not Mini or a model. This checks durable
delivery bytes, not task success, replay quality or publication of a method.
"""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from acceptance.stage4.stage11._builders import run_world
from acceptance.stage4.stage11._crash_prefix import begin_attempt, dispatch_and_publish_worker, publish_delivery_started
from synapse.experiments.gold.runner.completed_delivery_codec import (
    completed_worker_delivery_bytes, completed_worker_delivery_ref, restore_completed_worker_delivery,
)
from synapse.experiments.gold.runner.run_progress import AttemptProgressPhase, load_attempt_progress, require_progress_payload
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy


@pytest.mark.parametrize("suffix,version", [("# plain\n", "v2"), ("# e\u0301\n", "v3")])
def test_worker_patch_survives_checkpoint_and_process_restart_byte_for_byte(tmp_path, suffix, version):
    world = run_world(tmp_path, max_attempts=1, fallback_policy=FallbackPolicy.FORBIDDEN,
                      oracle_outcomes=[], worker_outcomes=("PATCH",))
    scenario_path = world.worker_process.scenario_path
    scenario = json.loads(scenario_path.read_text())
    scenario["patch_source"] += suffix
    scenario_path.write_text(json.dumps(scenario))
    prefix = begin_attempt(world)
    publish_delivery_started(prefix)
    delivered = dispatch_and_publish_worker(prefix)
    assert suffix in delivered.worker_result.diff_text
    progress = load_attempt_progress(world.composition.record_store, manifest=world.manifest, context=prefix.context)
    raw, ref = require_progress_payload(progress.get(AttemptProgressPhase.WORKER_COMPLETED))
    assert raw == completed_worker_delivery_bytes(delivered)
    assert ref == completed_worker_delivery_ref(delivered)
    # Check the actual re-opened checkpoint in another Python process.
    checkpoint, identity, output = tmp_path / "delivery.json", tmp_path / "identity.json", tmp_path / "restored.patch"
    checkpoint.write_bytes(raw)
    identity.write_text(json.dumps(ref.to_dict()))
    code = '''import json, pathlib, sys
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.runner.completed_delivery_codec import restore_completed_worker_delivery, completed_worker_delivery_bytes
raw = pathlib.Path(sys.argv[1]).read_bytes()
ref = HashBoundRef.from_dict(json.loads(pathlib.Path(sys.argv[2]).read_text()))
restored = restore_completed_worker_delivery(raw, expected_ref=ref)
assert completed_worker_delivery_bytes(restored) == raw
pathlib.Path(sys.argv[3]).write_bytes(restored.worker_result.diff_text.encode("utf-8"))
'''
    child = subprocess.run([sys.executable, "-B", "-c", code, str(checkpoint), str(identity), str(output)],
        cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stderr
    assert output.read_bytes() == delivered.worker_result.diff_text.encode("utf-8")
    assert ref.schema_id == "synapse.stage4.gold.runner.completed-worker-delivery/" + version
    restored = restore_completed_worker_delivery(raw, expected_ref=ref)
    # JSON arrays are the existing transport for Python tuples; text is exact.
    assert dict(restored.worker_result.diagnostics) == json.loads(json.dumps(dict(delivered.worker_result.diagnostics)))
    assert completed_worker_delivery_ref(restored) == ref
    assert world.worker_process.calls == 1
    assert world.oracle.calls == 0
