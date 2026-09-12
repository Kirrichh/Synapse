"""LIN-06: the proof fragment committed by the existing publication owner.

This projection consumes already independently checked request/decision/write
set records. The publication reader reopens those records and reconstructs this
same fragment. It neither authorizes nor writes a publication.
"""

from dataclasses import replace

from ..canonicalization import HashBoundRef, content_key_digest
from ..contracts import LineageEdgeKind
from ..stage12.verification_contract import inspect_verification_record
from .graph import GraphBuilder, LineageNodeClass, LineageViolation, LineageFailureCode
from .execution import execution_graph


def publication_graph(*, request, decision, created_refs, source_catalog):
    from ..stage13.publication import SOURCE_REQUEST_SCHEMAS

    source = request["schema_version"] in SOURCE_REQUEST_SCHEMAS
    if source:
        facts = request["verification"]["payload"]
        if source_catalog != {"schema_version": "synapse.stage4.gold.source-lineage-catalog/v1",
                "verification_ref": request["verification"]["verification_ref"], "evidence_refs": request["evidence_refs"]}:
            raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "source publication lost its retained catalog")
        builder = GraphBuilder("source-publication/v1", facts["operation_id"], facts["verification_attempt_id"])
        builder.add("source_operation", LineageNodeClass.SOURCE_OPERATION, HashBoundRef.from_dict(facts["claim_ref"]))
        builder.add("source_claim", LineageNodeClass.SOURCE_CLAIM, HashBoundRef.from_dict(facts["claim_ref"]))
        builder.add("verification", LineageNodeClass.VERIFICATION,
                    HashBoundRef.from_dict(request["verification"]["verification_ref"]))
        for index, item in enumerate(facts["sources"]):
            role = f"source.{index}"
            builder.add(role, LineageNodeClass.REPOSITORY_SOURCE, HashBoundRef.from_dict(item["ref"]))
            builder.link(role, LineageEdgeKind.DERIVED_FROM, "source_claim")
    else:
        facts = inspect_verification_record(request["verification"])
        builder = GraphBuilder("publication/v1", facts["run_id"], facts["attempt_id"])
        execution = execution_graph(source_catalog, request["verification"])
        if execution.profile != "execution/v1":
            raise LineageViolation(LineageFailureCode.MISSING_RECORD, "publication requires complete execution proof")
        builder.merge("", execution)
        builder.add("verified_outcome", LineageNodeClass.STRUCTURED_OUTCOME,
                    HashBoundRef.from_dict(request["outcome"]["outcome_ref"]))
    builder.record("request", LineageNodeClass.PUBLICATION_REQUEST, request, request["schema_version"])
    builder.record("publication_decision", LineageNodeClass.PUBLICATION_DECISION, decision, decision["schema_version"])
    for role, key, kind in (
        ("behavior", "blob", LineageNodeClass.BEHAVIOR_BLOB),
        ("manifest", "manifest", LineageNodeClass.BEHAVIOR_MANIFEST),
        ("attestation", "attestation", LineageNodeClass.ATTESTATION),
    ):
        ref = HashBoundRef.from_dict(created_refs[key])
        if role == "behavior":
            ref = replace(ref, ref_id=content_key_digest(request["unit"]["content_key"]["value"]))
        elif role == "manifest":
            ref = replace(ref, ref_id=request["manifest"]["manifest_id"]["digest_sha256"])
        builder.add(role, kind, ref)
    for role, index in (("ingestion_gate", 0), ("publication_gate", 1)):
        builder.add(role, LineageNodeClass.ADMISSION_DECISION,
                    HashBoundRef.from_dict(created_refs["admission"][index]))
    if not source:
        builder.add("consumer", LineageNodeClass.CONSUMER_CONTEXT, HashBoundRef.from_dict(request["use_context"]["ref"]))
        builder.link("consumer", LineageEdgeKind.DERIVED_FROM, "request")
    for index, ref in enumerate(created_refs["lifecycle"]):
        role = f"lifecycle.{index}"
        builder.add(role, LineageNodeClass.LIFECYCLE_RECORD, HashBoundRef.from_dict(ref))
        builder.link(role, LineageEdgeKind.DERIVED_FROM, "manifest")
    builder.link_roles()
    return builder.finish()
