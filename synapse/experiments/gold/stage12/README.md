# Stage 12 product code

| Module | Responsibility |
| --- | --- |
| `verification.py` | Read and verify retained attempt evidence; produce the sealed verification record. |
| `outcome.py` | Apply the single final-status matrix; identify, serialize and revalidate structured outcomes. |

The existing controller in `../runner/` persists the result and recovers an
unfinished verification suffix. `../runner/c1_boundary.py` owns the sole
interface to unchanged C1/C2. Historical plan decoding stays with Stage 10's
record and transport owners. These are existing responsibilities, not copies
of the Stage 12 implementation.

Normative details: [Stage 12 contract](../../../../docs/STAGE4_STAGE12_OUTCOME_CONTRACT.md).
Acceptance lives in `acceptance/stage4/stage12/`, outside product code. Heavy
acceptance files have separate CI jobs. Module boundaries follow responsibility
and dependency direction; there is no LOC limit.
