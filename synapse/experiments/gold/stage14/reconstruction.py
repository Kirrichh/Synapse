"""LIN-04/05/07: reconstruct completion graphs from retained execution records.

No input here grants execution authority. The existing verifier reads C1,
Stage 10 and reuse evidence before this projection binds their exact identities.
Missing physical sources fail reconstruction; the run owner publishes the graph
before making the matching terminal record visible.
"""

from ..canonicalization import HashBoundRef
from ..contracts import LineageEdgeKind as Edge
from ..persistence import read_committed_snapshot_transaction
from ..runner.records import RecordKind
from ..runner.run_progress import load_attempt_progress, AttemptProgressPhase, require_progress_payload
from ..stage10.context_codec import decode_canonical
from ..stage12.outcome import restore_attempt_outcome
from ..stage13.publication_store import PublicationResult
from .execution import execution_graph
from .graph import (GraphBuilder, LineageGraph, LineageNodeClass as Node, LineageViolation,
                    LineageFailureCode as Failure, LINEAGE_SCHEMA_V1)


def require_stored_graph(store, kind, key, expected):
    record = store.get(kind=kind, key=key)
    if record is None:
        raise LineageViolation(Failure.MISSING_RECORD, "terminal result lacks its committed lineage")
    graph = LineageGraph.from_dict(record.payload)
    if graph.to_dict() != expected.to_dict():
        raise LineageViolation(Failure.PHYSICAL_MISMATCH, "lineage differs from retained physical evidence")
    return graph


def _publication_fragment(publisher, transaction_id):
    if publisher is None:
        raise LineageViolation(Failure.MISSING_RECORD, "publication has no physical owner")
    result = PublicationResult(publisher.root, transaction_id)
    result.payload()  # Reopens every atomic participant and verifies the graph.
    _, members = read_committed_snapshot_transaction(publisher.root / "committed", transaction_id=transaction_id)
    return result, LineageGraph.from_dict(decode_canonical(members["lineage.json"]))


def reconstruct_attempt(*, manifest, context, result, store, verification, publisher):
    outcome = restore_attempt_outcome(result.structured_outcome, verification=verification)
    key = str(context.attempt_index)
    sources = store.get(kind=RecordKind.LINEAGE_SOURCES, key=key)
    if sources is None or sources.sha256 != context.phase_refs.lineage_sources_sha256:
        raise LineageViolation(Failure.MISSING_RECORD, "attempt lacks its bound physical source catalog")
    catalog = sources.payload
    b = GraphBuilder("attempt-incomplete/v1" if verification.payload()["failure_codes"] else "attempt/v1",
                     manifest.run_id.value, context.attempt_id.value)
    b.merge("", execution_graph(catalog, verification.to_dict()))
    b.add("outcome", Node.STRUCTURED_OUTCOME, outcome.reference)
    b.record("result", Node.ATTEMPT_RESULT, result.stored_dict(), result.payload()["schema_version"])
    progress = load_attempt_progress(store, manifest=manifest, context=context)
    publication = store.get(kind=RecordKind.PUBLICATION_RESULT, key=key)
    if publication is not None:
        if publication.payload["state"] == "COMMITTED":
            published, fragment = _publication_fragment(publisher, publication.payload["transaction_id"])
            b.merge("published", fragment)
            b.add("publication", Node.PUBLICATION_RESULT, published.reference)
            # Final outcome depends on publication. The pre-publication proof
            # remains a separate immutable verification occurrence.
            b.link("published.manifest", Edge.PUBLISHED_AS, "publication")
        else:
            b.record("publication", Node.PUBLICATION_RESULT, publication.payload,
                     "synapse.stage4.gold.run-publication/v1")
    mechanism = progress.get(AttemptProgressPhase.REUSE_GUARD_COMPLETED)
    if mechanism is not None:
        raw, ref = require_progress_payload(mechanism)
        record = decode_canonical(raw)
        b.add("mechanism_use", Node.MECHANISM_USE, ref)
        published, fragment = _publication_fragment(publisher, record["publication_transaction_id"])
        b.merge("producer", fragment)
        b.add("producer.publication", Node.PUBLICATION_RESULT, published.reference)
        b.link("producer.manifest", Edge.PUBLISHED_AS, "producer.publication")
        b.link("producer.publication", Edge.DERIVED_FROM, "mechanism_use")
        b.link("mechanism_use", Edge.DERIVED_FROM, "verification")
    promotion = store.get(kind=RecordKind.REUSE_PROMOTION, key=key)
    if promotion is not None:
        b.record("promotion", Node.REUSE_PROMOTION, promotion.payload, promotion.payload["schema_version"])
    b.link_roles()
    return b.finish()


def reconstruct_run(*, store, manifest, attempts, terminal, result):
    b = GraphBuilder("run/v1", manifest.run_id.value, "run")
    b.record("run", Node.RUN, manifest.stored_dict(), manifest.payload()["schema_version"])
    b.record("decision", Node.RUN_DECISION, terminal.stored_dict(), terminal.payload()["schema_version"])
    b.record("run_result", Node.RUN_RESULT, result.stored_dict(), result.payload()["schema_version"])
    for attempt in attempts:
        key = str(attempt.attempt_index)
        record = store.get(kind=RecordKind.ATTEMPT_LINEAGE, key=key)
        if record is None:
            raise LineageViolation(Failure.MISSING_RECORD, "run lacks a completed attempt graph")
        graph = LineageGraph.from_dict(record.payload)
        prefix = "attempt." + key
        b.merge(prefix, graph)
        b.link(prefix + ".result", Edge.DERIVED_FROM, "run_result")
    for kind in (RecordKind.DECISION, RecordKind.PREPARATION_STARTED,
                 RecordKind.PREPARATION_FAILURE, RecordKind.CONTINUATION_EVIDENCE):
        for key in store.iter_keys(kind=kind):
            record = store.get(kind=kind, key=key)
            role = kind + "." + key
            b.record(role, Node.RUN_DECISION, record.payload, "synapse.stage4.gold.lineage-run-decision/v1")
            b.link(role, Edge.DERIVED_FROM, "run_result")
    b.link_roles()
    return b.finish()
