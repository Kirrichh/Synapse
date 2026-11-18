"""Acceptance-only executable check for the production change oracle.

The product oracle runs the check on a fresh checkout of the base revision and
again on a fresh checkout with the candidate patch applied. This check runs the
declared command in each and, after the second run, records one observation of
both return codes and the resulting sources. No prescribed result or Gold
record is written.
"""
import json
from pathlib import Path

from acceptance.stage4.stage11._oracle_process import install_check


_PROGRAM = '''import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
contract = json.loads((root / "oracle_input.json").read_text())
pending = root / "oracle_pending.json"
changed = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip()
run = subprocess.run(contract["command"], capture_output=True, text=True)
sys.stdout.write(run.stdout)
sys.stderr.write(run.stderr)
if not changed:
    pending.write_text(json.dumps({"before_returncode": run.returncode}))
    sys.exit(run.returncode)
before = json.loads(pending.read_text())
pending.unlink()
# The checkout is the verified commit's parent: in Gold, the C1 bridge, which is
# the declared base plus C1's committed inputs. Record the declared base only
# when that is all that separates them.
head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
extra = subprocess.run(["git", "diff", "--name-only", contract["base_revision"], head], capture_output=True,
                       text=True, check=True).stdout.split()
observation = {**before, "after_returncode": run.returncode, "checkout": str(Path.cwd()),
    "base_revision": contract["base_revision"] if all(path.startswith("controlled_changes/") for path in extra) else head,
    "command": contract["command"],
    "result_sources": {path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                       for path in contract["target_paths"]}}
with (root / "oracle_observations.jsonl").open("a") as stream:
    stream.write(json.dumps(observation) + "\\n")
sys.exit(run.returncode)
'''


def create_executing_oracle(root: Path, *, repo: Path, base_revision: str, command: tuple[str, ...],
                            target_paths: tuple[str, ...]):
    install_check(root, _PROGRAM)
    (root / 'oracle_input.json').write_text(json.dumps({
        'repo': str(repo), 'base_revision': base_revision, 'command': list(command), 'target_paths': list(target_paths)}))
