"""Acceptance-only external oracle using the existing report transport.

It derives the verdict by applying the received patch to a fresh checkout and
running a separate executable check. No prescribed result or Gold record is
written. This is an acceptance oracle, not the official SWE-bench harness.
"""
import json
from pathlib import Path


_PROGRAM = '''import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

args = sys.argv
run_id = args[args.index("--run_id") + 1]
instance_id = args[args.index("--instance_ids") + 1]
prediction = json.loads(Path(args[args.index("--predictions_path") + 1]).read_text())
contract = json.loads(Path("oracle_input.json").read_text())
checkout = Path(tempfile.mkdtemp(prefix="oracle-checkout-", dir=Path.cwd()))
subprocess.run(["git", "clone", "--no-local", "--no-checkout", "-q", contract["repo"], str(checkout)], check=True)
subprocess.run(["git", "checkout", "-q", "--detach", contract["base_revision"]], cwd=checkout, check=True)
before = subprocess.run(contract["command"], cwd=checkout, capture_output=True, text=True)
assert before.returncode != 0, "independent baseline must reproduce the failure"
patch = prediction["model_patch"].encode("utf-8")
subprocess.run(["git", "apply", "-"], input=patch, cwd=checkout, check=True)
after = subprocess.run(contract["command"], cwd=checkout, capture_output=True, text=True)
observation = {"before_returncode": before.returncode, "after_returncode": after.returncode,
    "patch_sha256": hashlib.sha256(patch).hexdigest(), "checkout": str(checkout),
    "base_revision": contract["base_revision"], "command": contract["command"],
    "result_sources": {path: hashlib.sha256((checkout / path).read_bytes()).hexdigest()
                       for path in contract["target_paths"]}}
with Path("oracle_observations.jsonl").open("a") as stream:
    stream.write(json.dumps(observation) + "\\n")
report = Path("logs") / "run_evaluation" / run_id / prediction["model_name_or_path"].replace("/", "__") / instance_id / "report.json"
report.parent.mkdir(parents=True)
report.write_text(json.dumps({instance_id: {"resolved": after.returncode == 0}}))
(report.parent / "run_instance.log").write_text(json.dumps(observation))
(report.parent / "test_output.txt").write_text(after.stdout + after.stderr)
'''


def create_executing_oracle(root: Path, *, repo: Path, base_revision: str, command: tuple[str, ...],
                            target_paths: tuple[str, ...]):
    package = root / 'swebench' / 'harness'
    package.mkdir(parents=True)
    (package.parent / '__init__.py').write_text('')
    (package / '__init__.py').write_text('')
    (package / 'run_evaluation.py').write_text(_PROGRAM)
    (root / 'oracle_input.json').write_text(json.dumps({
        'repo': str(repo), 'base_revision': base_revision, 'command': list(command), 'target_paths': list(target_paths)}))
