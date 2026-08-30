"""Handing an attempt's context to the worker, and the §22 gate in front of it.

This is the run's delivery owner. §22 requires the consumption gate to be
evaluated against the state that will actually be used, immediately before the
knowledge is used, and Patch 8's exit criterion is that no path to a worker
reaches one without crossing that gate. This module is that path, so the call to
``admit_for_use_now`` is written here in full and by name rather than behind a
helper — a wrapper would let a delivery owner satisfy the requirement by
importing something that merely promises to admit.

The crossing is made consequential by the order of operations. The barrier runs
first and returns a ``CurrentAdmittedKnowledge`` that names exactly which
subjects were admitted, for which consumer context and under which boundary.
Only then is the worker context built, *from that result*, and the context is
re-checked against it before dispatch: a context resting on an older admission
is refused rather than delivered. That is what makes the gate a barrier instead
of an observation — a run cannot revalidate one thing and deliver another.

Dispatch itself is somebody else's job. The transport port is injected, the
evidence it returns is verified by the Stage 10 owner (``verify_delivery``), and
this module holds no transport, no process and no retry policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from synapse.experiments.gold.point_of_use import (
    CurrentAdmittedKnowledge,
    PointOfUseAdmissionRequest,
    admit_for_use_now,
    require_point_of_use_admission_request,
)
from synapse.experiments.gold.runner.vocabulary import (
    GoldRunFailureCode,
    GoldRunViolation,
)
from synapse.experiments.gold.stage10.context import (
    WorkerContextRecord,
    validate_worker_context,
)
from synapse.experiments.gold.stage10.delivery_verification import (
    DeliveryReceipt,
    verify_delivery,
)
from synapse.experiments.gold.stage10.worker_transport import (
    WorkerDeliveryEvidence,
    WorkerInvocation,
)

#: Builds the worker context *from* the fresh admission, never beside it.
WorkerContextSource = Callable[[CurrentAdmittedKnowledge], WorkerContextRecord]
#: Renders the invocation for an exact context.
WorkerInvocationSource = Callable[[WorkerContextRecord], WorkerInvocation]
#: The dispatch port: hands an invocation to a worker, returns transport evidence.
WorkerTransportPort = Callable[[WorkerInvocation], WorkerDeliveryEvidence]


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


@dataclass(frozen=True)
class AttemptDeliveryPlan:
    """The four delivery inputs for one attempt, bound together by the caller.

    Spelling the four out at every call site is one rule written twice: the
    admission request and the three sources belong to the same attempt, and a
    signature that lost one of them would still type-check. Binding them here
    lets the controller pass an attempt's delivery as a single value it cannot
    partially assemble.
    """

    admission_request: PointOfUseAdmissionRequest
    context_source: WorkerContextSource
    invocation_source: WorkerInvocationSource
    transport: WorkerTransportPort

    def __post_init__(self) -> None:
        require_point_of_use_admission_request(self.admission_request)
        for name in ("context_source", "invocation_source", "transport"):
            if not callable(getattr(self, name)):
                raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be callable")


@dataclass(frozen=True)
class WorkerDelivery:
    """What one verified delivery produced: the admission it rests on, and its receipt."""

    admitted: CurrentAdmittedKnowledge
    context: WorkerContextRecord
    invocation: WorkerInvocation
    receipt: DeliveryReceipt

    def __post_init__(self) -> None:
        if type(self.admitted) is not CurrentAdmittedKnowledge:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "delivery must name an exact fresh admission")
        if type(self.context) is not WorkerContextRecord:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "delivery must name an exact worker context")
        if type(self.receipt) is not DeliveryReceipt:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "delivery must carry an exact receipt")


def deliver_attempt_context(
    *,
    admission_request: PointOfUseAdmissionRequest,
    context_source: WorkerContextSource,
    invocation_source: WorkerInvocationSource,
    transport: WorkerTransportPort,
) -> WorkerDelivery:
    """Cross the §22 barrier, then deliver the context that rests on it.

    Refusal is typed and happens before any dispatch: the run learns that this
    attempt could not be delivered, and no worker was given anything.
    """

    request = require_point_of_use_admission_request(admission_request)
    for name, source in (
        ("context_source", context_source),
        ("invocation_source", invocation_source),
        ("transport", transport),
    ):
        if not callable(source):
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be callable")

    admitted = admit_for_use_now(
        request.handle,
        binding=request.binding,
        chain=request.chain,
        evidence=request.evidence,
        entitlements=request.entitlements,
        requested=request.requested,
    )
    if type(admitted) is not CurrentAdmittedKnowledge:
        raise _fail(GoldRunFailureCode.CONSUMPTION_REFUSED, "the consumption gate returned no fresh admission")

    context = context_source(admitted)
    if type(context) is not WorkerContextRecord:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "context source returned an invalid worker context")
    validate_worker_context(context)
    if context.admitted_knowledge is not admitted:
        raise _fail(
            GoldRunFailureCode.CONSUMPTION_REFUSED,
            "the worker context rests on an admission other than the one just taken",
        )

    invocation = invocation_source(context)
    if type(invocation) is not WorkerInvocation:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "invocation source returned an invalid invocation")
    if invocation.context_id != context.context_id:
        raise _fail(GoldRunFailureCode.DELIVERY_MISMATCH, "invocation belongs to another context")

    evidence = transport(invocation)
    if type(evidence) is not WorkerDeliveryEvidence:
        raise _fail(GoldRunFailureCode.DELIVERY_MISMATCH, "transport returned invalid delivery evidence")
    receipt = verify_delivery(context=context, invocation=invocation, evidence=evidence)
    return WorkerDelivery(
        admitted=admitted, context=context, invocation=invocation, receipt=receipt
    )
