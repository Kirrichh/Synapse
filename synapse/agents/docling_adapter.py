"""Version-pinned Docling integration with per-source extraction accounting."""

import json
from pathlib import Path

from .codec import canonical_bytes, decode_json
from .contracts import LocalInformationPolicy
from .outputs import DOCUMENT_OBSERVATIONS_V1
from .policy import IsolationKind
from .stdio_adapter import StdioAgentAdapter, StdioAgentConfig


class DoclingAdapterFactory:
    def create(self, configuration):
        from .configuration import profile_from_dict
        profile = profile_from_dict(configuration["profile"])
        native = configuration["native"]
        if (profile.agent_id != "docling-agent" or profile.agent_version != "0.6.0"
                or not set(profile.capabilities).issubset({'document.pdf.read', 'document.fields.extract'})
                or not set(profile.accepted_media_types).issubset({'application/pdf', 'image/png', 'image/jpeg'})
                or profile.output_profiles != (DOCUMENT_OBSERVATIONS_V1,)
                or profile.effect_classes != ("READ_ONLY",)
                or profile.runtime_policy.isolation not in (IsolationKind.BUBBLEWRAP, IsolationKind.OCI)
                or profile.runtime_policy.network != "NONE" or profile.runtime_policy.writable_workspace
                or profile.local_information_policy is not LocalInformationPolicy.NOT_SUPPORTED):
            raise ValueError("Docling extraction requires its exact read-only offline profile")
        # 0.6.0's extractor ignores **kwargs. Refuse private context instead of
        # falsely claiming that handing it an extra parameter uses local memory.
        if set(native) != {"python", "artifacts_path"}:
            raise ValueError("unknown Docling native configuration")
        return StdioAgentAdapter(StdioAgentConfig(
            command=(native["python"], "-m", "synapse.agents.native_worker", "docling"),
            timeout_seconds=profile.resource_limits.timeout_seconds, profile=profile,
            environment=tuple(sorted({
                "SYNAPSE_AGENT_NATIVE_CONFIGURATION": canonical_bytes(native).decode("utf-8"),
                "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            }.items())),
        ))


def extract_documents(request, configuration):
    """Called only in the isolated child, never inside the Gold process."""
    from importlib.metadata import version
    from .native_worker import native_response
    if version("docling-agent") != "0.6.0":
        raise ValueError("Docling native version differs from the admitted integration")
    if request["local_information"] is not None:
        return native_response(request, "REFUSED", failure="LOCAL_INFORMATION_POLICY_VIOLATION")
    schema = decode_json(request["task"]["text"].encode("utf-8"))
    if type(schema) is not dict or not schema:
        return native_response(request, "REFUSED", failure="INPUT_INVALID")
    # A concrete schema avoids an implicit remote model call to invent a schema.
    from docling_agent.agents import DoclingExtractingAgent
    from docling.document_extractor import DocumentExtractor, ExtractionFormatOption
    from docling.datamodel.base_models import InputFormat, ConversionStatus
    from docling.datamodel.pipeline_options import VlmExtractionPipelineOptions
    from docling.pipeline.extraction_vlm_pipeline import ExtractionVlmPipeline
    from docling.backend.docling_parse_backend import DoclingParseDocumentBackend
    from docling.backend.image_backend import ImageDocumentBackend
    options = VlmExtractionPipelineOptions(artifacts_path=Path(configuration["artifacts_path"]))
    agent = DoclingExtractingAgent(tools=[])
    # The native API does not expose extractor configuration or typed results.
    # These two 0.6.0 fields are the version-pinned translation boundary.
    agent._extractor = DocumentExtractor(allowed_formats=[InputFormat.PDF, InputFormat.IMAGE],
        extraction_format_options={
            InputFormat.PDF: ExtractionFormatOption(pipeline_cls=ExtractionVlmPipeline,
                pipeline_options=options, backend=DoclingParseDocumentBackend),
            InputFormat.IMAGE: ExtractionFormatOption(pipeline_cls=ExtractionVlmPipeline,
                pipeline_options=options, backend=ImageDocumentBackend),
        })
    sources = []
    for artifact in request["artifacts"]:
        observations = []
        failure = None
        path = Path(artifact["path"])
        if artifact["media_type"] not in {"application/pdf", "image/png", "image/jpeg"}:
            failure = "UNSUPPORTED_MEDIA_TYPE"
        else:
            try:
                agent.run(task=json.dumps(schema), sources=[path])
                results = agent._last_results.get(str(path), [])
                if not results:
                    failure = "EXTRACTION_FAILED"
                for result in results:
                    if result.status is not ConversionStatus.SUCCESS or result.errors:
                        raise ValueError("Docling source extraction did not complete")
                    pages = getattr(result, "pages", None)
                    if not pages:
                        raise ValueError("unsupported Docling extraction result")
                    for page in pages:
                        if page.errors:
                            raise ValueError("Docling page extraction failed")
                        extracted = getattr(page, "extracted_data", None)
                        if type(extracted) is not dict:
                            raise ValueError("Docling extracted fields are not a JSON object")
                        page_number = getattr(page, "page_no", None)
                        if type(page_number) is not int or page_number < 1:
                            page_number = None
                        for name, value in extracted.items():
                            observations.append({"kind": "EXTRACTED_FIELD", "field": name,
                                                 "value": value, "page": page_number})
            except Exception as exc:
                failure = type(exc).__name__
                observations = []
        sources.append({"artifact_id": artifact["artifact_id"], "source_sha256": artifact["sha256"],
            "status": "PARSED" if failure is None else "ERROR", "observations": observations,
            "failure_code": failure})
    if not sources:
        return native_response(request, "REFUSED", failure="INPUT_INVALID")
    good = sum(s["status"] == "PARSED" for s in sources)
    status = "COMPLETED" if good == len(sources) else ("PARTIAL" if good else "ERROR")
    return native_response(request, status, [(DOCUMENT_OBSERVATIONS_V1, {"sources": sources})])
