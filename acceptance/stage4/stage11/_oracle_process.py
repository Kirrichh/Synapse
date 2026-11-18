"""Acceptance check for the production change oracle, with scripted outcomes.

The product oracle runs the declared check twice, in two fresh checkouts: the
base revision (clean) and the base with the candidate patch applied (dirty).
This check tells the two apart by the checkout state, fails on the base and
answers the scripted outcome after the patch; production C1/C2 still parses the
report the oracle writes. The declared command is the shared
``oracle_check_launcher.py``; until a scenario installs a check in the oracle
work directory, it reports that the check never ran, a real infrastructure
failure.
"""

from dataclasses import asdict
import json
from pathlib import Path
import sys

from synapse.experiments.applied_verification.change_oracle import ChangeOracleConfig


CHECK_SCRIPT = "acceptance_check.py"
LAUNCHER = Path(__file__).resolve().parent / "oracle_check_launcher.py"

_PROGRAM = '''import json
from pathlib import Path
import subprocess
import sys

state_path = Path(__file__).resolve().parent / "oracle_state.json"
changed = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip()
if not changed:
    sys.exit(1)  # the base revision keeps the defect
fixed = any("return a + b" in path.read_text(errors="replace")
            for path in Path.cwd().rglob("*.py") if ".git" not in path.parts)
if not fixed:
    sys.exit(4)
state = json.loads(state_path.read_text())
resolved = state["outcomes"][min(state["calls"], len(state["outcomes"]) - 1)]
state["calls"] += 1
state_path.write_text(json.dumps(state))
print("independent scripted outcome")
sys.exit(0 if resolved else 1)
'''


def acceptance_oracle(work_dir: Path) -> ChangeOracleConfig:
    """The change oracle every acceptance input declares; its check lives in ``work_dir``."""
    return ChangeOracleConfig(work_dir=work_dir, fail_to_pass=((sys.executable, "-B", str(LAUNCHER)),),
                              command_timeout_seconds=60)


def install_check(root: Path, program: str) -> None:
    """Install ``program`` as the check the launcher runs for the case of ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    (root / CHECK_SCRIPT).write_text(program)


def oracle_payload(config: ChangeOracleConfig) -> dict:
    return json.loads(json.dumps(asdict(config), default=str))


def create_oracle_process(root: Path, outcomes: tuple[bool, ...]) -> None:
    install_check(root, _PROGRAM)
    (root / "oracle_state.json").write_text(json.dumps({"outcomes": outcomes, "calls": 0}))
