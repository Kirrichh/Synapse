"""Acceptance-side driver: one durable session whose process dies at its first removal from store D.

``python -m acceptance.memory._crash_driver STATE CONFIG PROGRAM RUNS RUN_ID INPUTS`` runs the same
durable launch the CLI composes, with the owner's store D wrapped so that the
process exits the moment retention removes a body. Retention records its acts
before any removal, so the crash lands exactly between the recorded fact and
the physical change — the point the checker must find consistent afterwards.
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


def main(state, configuration, program, runs, run_id, inputs) -> None:
    factory = MemoryFactory(Path(state), read_memory_configuration(Path(configuration)))
    factory.gateway.evidence = _DyingStore(factory.gateway.evidence.root)
    execute_durable_run(DurableRunRequest(source_path=Path(program), state_dir=Path(runs), run_id=run_id,
                                          input_file=Path(inputs), memory=factory), stdin=sys.stdin)
    os._exit(0)


if __name__ == "__main__":
    main(*sys.argv[1:7])
