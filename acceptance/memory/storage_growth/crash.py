"""Acceptance-only process termination before publishing a large projection."""
from __future__ import annotations

import os
from pathlib import Path
import sys

from synapse.application import DurableRunRequest, execute_durable_run
from synapse.memory_consolidation.configuration import read_memory_configuration
from synapse.memory_consolidation.factory import MemoryFactory


def _die(*args, **kwargs):
    os._exit(9)


class InterruptedPublication(MemoryFactory):
    """Inject a crash after the court decision, before one projection is stored."""

    def court(self, mode, *, current=None):
        setattr(self.owner, self.publication, _die)
        return super().court(mode, current=current)


def main(state, configuration, program, runs, run_id, inputs, point):
    assert point in {"boundary", "state"}
    factory = InterruptedPublication(Path(state), read_memory_configuration(Path(configuration)))
    factory.publication = "put_boundary" if point == "boundary" else "put_state_snapshot"
    execute_durable_run(DurableRunRequest(source_path=Path(program), state_dir=Path(runs), run_id=run_id,
                                         input_file=Path(inputs), memory=factory), stdin=sys.stdin)
    raise AssertionError("the publication fault was not reached")


if __name__ == "__main__":
    main(*sys.argv[1:8])
