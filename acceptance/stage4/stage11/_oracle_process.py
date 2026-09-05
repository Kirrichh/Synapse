"""External SWE-bench process stand-in; production C1/C2 still parses its files."""

import json
from pathlib import Path


_PROGRAM = '''import json
from pathlib import Path
import sys

args = sys.argv
run_id = args[args.index("--run_id") + 1]
instance_id = args[args.index("--instance_ids") + 1]
prediction = json.loads(Path(args[args.index("--predictions_path") + 1]).read_text())
assert "return a + b" in prediction["model_patch"]
state_path = Path("oracle_state.json")
state = json.loads(state_path.read_text())
resolved = state["outcomes"][min(state["calls"], len(state["outcomes"]) - 1)]
state["calls"] += 1
state_path.write_text(json.dumps(state))
report = Path("logs") / "run_evaluation" / run_id / prediction["model_name_or_path"].replace("/", "__") / instance_id / "report.json"
report.parent.mkdir(parents=True)
report.write_text(json.dumps({instance_id: {"resolved": resolved}}))
(report.parent / "run_instance.log").write_text("external acceptance oracle")
(report.parent / "test_output.txt").write_text("independent scripted outcome")
'''


def create_oracle_process(root: Path, outcomes: tuple[bool, ...]) -> None:
    package = root / "swebench" / "harness"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "run_evaluation.py").write_text(_PROGRAM)
    (root / "oracle_state.json").write_text(json.dumps({"outcomes": outcomes, "calls": 0}))
