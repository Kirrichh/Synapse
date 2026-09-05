"""Drive deterministic acceptance candidates through a real external process."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys

from synapse.worker.mini_adapter import MiniAdapterConfig


_PROGRAM = r'''from __future__ import annotations

import json
from pathlib import Path
import sys


scenario_path = Path(sys.argv[1])
state = json.loads(scenario_path.read_text(encoding="utf-8"))
index = state["calls"]
outcomes = state["outcomes"]
outcome = outcomes[min(index, len(outcomes) - 1)]
state["calls"] = index + 1
scenario_path.write_text(
    json.dumps(state, sort_keys=True, separators=(",", ":")),
    encoding="utf-8",
)
if outcome == "PATCH":
    target = Path.cwd() / "src" / "calc.py"
    target.write_text(state["patch_source"], encoding="utf-8")
elif outcome == "NO_PATCH":
    pass
elif outcome == "ERROR":
    raise SystemExit(7)
else:
    raise SystemExit(9)
print(json.dumps({"usage": {"total_tokens": 0}}))  # This deterministic process makes no model calls.
'''


@dataclass(frozen=True)
class WorkerProcessControl:
    """Files controlling and observing the real MiniWorker subprocess."""

    program_path: Path
    scenario_path: Path

    @property
    def calls(self) -> int:
        state = json.loads(self.scenario_path.read_text(encoding="utf-8"))
        return state["calls"]

    def config(self, *, model: str) -> MiniAdapterConfig:
        return MiniAdapterConfig(
            command=(sys.executable, str(self.program_path), str(self.scenario_path)),
            timeout_seconds=30,
            max_steps=5,
            cost_limit=0.0,
            model=model,
        )


def create_worker_process(
    root: Path,
    *,
    outcomes: tuple[str, ...],
    patch_source: str,
) -> WorkerProcessControl:
    """Write one external worker program and its deterministic outcome queue."""

    if not outcomes or any(item not in {"PATCH", "NO_PATCH", "ERROR"} for item in outcomes):
        raise ValueError("worker outcomes must use the acceptance vocabulary")
    root.mkdir(parents=True, exist_ok=True)
    program = root / "worker_program.py"
    scenario = root / "worker_scenario.json"
    program.write_text(_PROGRAM, encoding="utf-8")
    scenario.write_text(
        json.dumps(
            {"calls": 0, "outcomes": list(outcomes), "patch_source": patch_source},
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return WorkerProcessControl(program_path=program, scenario_path=scenario)


__all__ = ["WorkerProcessControl", "create_worker_process"]
