"""Acceptance-side driver: one durable session whose process dies at a chosen point.

``python -m acceptance.memory._crash_driver STATE CONFIG PROGRAM RUNS RUN_ID INPUTS [POINT]`` runs the
same durable launch the CLI composes, wrapped so that the process exits:

* ``discard`` (the default) — the moment retention removes a body from store D.
  Retention records its acts before any removal, so the crash lands exactly
  between the recorded fact and the physical change;
* ``consolidation`` — when the finished session asks for its consolidation:
  every result is recorded, nothing is consolidated;
* ``boundary`` — when the court writes the snapshot boundary of a decision it
  has already committed: the decision is applied, the boundary lags.

These are the points the checker must find consistent afterwards.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

from synapse.application import DurableRunRequest, execute_durable_run
from synapse.memory_consolidation.configuration import read_memory_configuration
from synapse.memory_consolidation.factory import MemoryFactory
from synapse.memory_consolidation.tools.evidence import EvidenceStore


class _DyingStore(EvidenceStore):
    def discard(self, ref, reason, *, tombstone=None):
        os._exit(9)


class _DyingFactory(MemoryFactory):
    def court(self, mode, *, current=None):
        os._exit(9)


def _dying(*_, **__):
    os._exit(9)


def main(state, configuration, program, runs, run_id, inputs, point="discard") -> None:
    kind = _DyingFactory if point == "consolidation" else MemoryFactory
    factory = kind(Path(state), read_memory_configuration(Path(configuration)))
    if point == "discard":
        factory.gateway.evidence = _DyingStore(factory.gateway.evidence.root)
    elif point == "boundary":
        factory.owner.put_boundary = _dying
    execute_durable_run(DurableRunRequest(source_path=Path(program), state_dir=Path(runs), run_id=run_id,
                                          input_file=Path(inputs), memory=factory), stdin=sys.stdin)
    os._exit(0)


if __name__ == "__main__":
    main(*sys.argv[1:8])
