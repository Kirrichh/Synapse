"""Verification of repository knowledge before a consumer task exists.

The verifier owns the meaning of a source claim. A proposal is neither a
verification result nor permission to publish. Sources are exact committed
regular files; Python identity is resolved by the existing binding owner.
Command recipes are observed in a detached checkout through Controlled
Change's command verifier. Their claim is the observed command contract,
never correctness of a later task.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from importlib.metadata import distributions
from pathlib import Path
import platform
import sys
import time
from typing import Callable

from synapse.change.contract import CommandExpectation, validate_repo_relative_path
from synapse.change.verification import run_expected_command
from synapse.change.workspace import (
    assert_clean_worktree, cleanup_worktree, create_detached_worktree,
    load_committed_bytes, require_tree_mode, resolve_revision,
)

from .bindings import (
    PYTHON_BINDING_RESOLVER_V1, PythonSymbolKind, binding_to_ref, binding_canonical_payload_bytes,
    resolve_python_binding, inspect_retained_python_binding,
    discover_retained_python_bindings, BindingViolation,
)
from .canonicalization import (
    HashBoundRef, RefKind, canonicalize_stage4_payload,
    STAGE4_CANONICAL_PROFILE_V1, STABLE_CANONICAL_CODEC_ID,
)
from .contracts import ActorIdentity, AttemptId, RepositoryRevision, RunId
from .provenance import ObservedExternalInput, ExternalInputKind


SOURCE_CLAIM_V1 = "synapse.stage4.gold.source-claim/v1"
SOURCE_CLAIM_V2 = "synapse.stage4.gold.source-claim/v2"
SOURCE_VERIFICATION_V1 = "synapse.stage4.gold.source-verification/v1"
SOURCE_KNOWLEDGE_V1 = "synapse.stage4.gold.source-knowledge/v1"
SOURCE_FILE_V1 = "synapse.stage4.gold.repository-source/v1"
SOURCE_RUNTIME_V1 = "synapse.stage4.gold.source-runtime/v1"
SOURCE_OBSERVATION_V1 = "synapse.stage4.gold.source-observation/v1"
SOURCE_POLICY_V1 = "source-verification.v1"
SOURCE_VERIFIER = ActorIdentity("synapse.gold.source-verifier")
SOURCE_EXTRACTOR = ActorIdentity("synapse.gold.source-extractor")
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_BYTES = 16 * 1024 * 1024
_SEAL = object()


def canonical(value):
    return canonicalize_stage4_payload(value, profile_id=STAGE4_CANONICAL_PROFILE_V1,
                                       codec_id=STABLE_CANONICAL_CODEC_ID)


def source_ref(raw: bytes, schema: str, kind=RefKind.SOURCE_EVIDENCE):
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(kind, digest, schema, digest, len(raw),
                        "application/octet-stream" if schema == SOURCE_FILE_V1 else "application/json")


def observe_source_runtime():
    """Measure this verifier's actual Python/package environment, without secrets.

    Package versions are observed metadata, not a claim to hash all installed
    code or contain side effects. Recipes in this profile use this interpreter.
    """
    return {"schema_version": SOURCE_RUNTIME_V1, "python": sys.version,
        "implementation": platform.python_implementation(), "system": platform.system(), "machine": platform.machine(),
        "packages": [list(item) for item in sorted({(item.metadata["Name"].lower(), item.version) for item in distributions()
                            if item.metadata["Name"]})]}


def _shape(value, fields, label):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError(f"{label} has an unknown contract")
    return value


def _strings(value, label, *, nonempty=True):
    if (type(value) is not list or nonempty and not value
            or any(type(item) is not str or not item or "\x00" in item for item in value)
            or len(value) != len(set(value))):
        raise ValueError(f"{label} requires distinct nonempty strings")
    return tuple(value)


def inspect_source_claim(value):
    _shape(value, {"schema_version", "operation_id", "revision", "kind", "sources",
                   "symbols", "recipe", "policy_inputs", "environment_inputs", "tool_inputs", "replay_gas_budget"}, "source claim")
    if value["schema_version"] not in {SOURCE_CLAIM_V1, SOURCE_CLAIM_V2}:
        raise ValueError("unknown source claim schema")
    RunId(value["operation_id"])
    RepositoryRevision.git_commit(value["revision"])
    if type(value["replay_gas_budget"]) is not int or not 8 <= value["replay_gas_budget"] <= 1_000_000:
        raise ValueError("source replay requires an explicit bounded execution budget")
    sources = _strings(value["sources"], "source paths")
    if len(sources) > 64:
        raise ValueError("source claim exceeds its file budget")
    for path in sources:
        if validate_repo_relative_path(path, "source path") != path:
            raise ValueError("source path must be canonical")
    study = value["schema_version"] == SOURCE_CLAIM_V2
    if study and (value["kind"] != "SOURCE_STUDY" or value["symbols"] != [] or value["recipe"] is not None):
        raise ValueError("source study derives identities from materials and cannot declare a recipe")
    if type(value["symbols"]) is not list or not study and not value["symbols"] or len(value["symbols"]) > 128:
        raise ValueError("source symbols exceed their contract")
    identities = set()
    for symbol in value["symbols"]:
        _shape(symbol, {"path", "module", "qualname", "symbol_kind", "contract_version"}, "source symbol")
        if symbol["path"] not in sources:
            raise ValueError("symbol is outside the retained sources")
        identity = canonical(symbol)
        if identity in identities:
            raise ValueError("source symbol is repeated")
        identities.add(identity)
        PythonSymbolKind(symbol["symbol_kind"])
    for field, kind in (("policy_inputs", ExternalInputKind.POLICY),
                        ("environment_inputs", ExternalInputKind.ENVIRONMENT), ("tool_inputs", ExternalInputKind.TOOL)):
        if type(value[field]) is not list or not value[field]:
            raise ValueError("source verification needs observed policy, environment and tools")
        inputs = tuple(ObservedExternalInput.from_dict(item) for item in value[field])
        if any(item.kind is not kind for item in inputs):
            raise ValueError("source dependency is assigned to another input kind")
        if len({item.name for item in inputs}) != len(inputs):
            raise ValueError("source input names are repeated")
        if [item.name for item in inputs] != sorted(item.name for item in inputs):
            raise ValueError("source inputs must use canonical name order")
    if study:
        return value
    if value["kind"] == "REPOSITORY_FACT_CHECK":
        if not value["symbols"] or value["recipe"] is not None:
            raise ValueError("repository facts require symbols and no command recipe")
    elif value["kind"] == "VERIFICATION_RECIPE":
        recipe = _shape(value["recipe"], {"command", "expectation"}, "verification recipe")
        command = recipe["command"]
        if (type(command) is not list or not command
                or any(type(item) is not str or not item or "\x00" in item for item in command)):
            raise ValueError("recipe requires explicit command arguments")
        exp = _shape(recipe["expectation"], {"expected_exit_codes", "expected_nonzero_exit",
            "combined_output_contains", "combined_output_not_contains", "timeout_seconds"}, "recipe expectation")
        if (type(exp["expected_exit_codes"]) is not list or not exp["expected_exit_codes"]
                or any(type(code) is not int for code in exp["expected_exit_codes"])
                or type(exp["expected_nonzero_exit"]) is not bool
                or type(exp["timeout_seconds"]) is not int or not 1 <= exp["timeout_seconds"] <= 600):
            raise ValueError("recipe needs bounded, explicit execution expectations")
        _strings(exp["combined_output_contains"], "required command observations")
        _strings(exp["combined_output_not_contains"], "forbidden command observations", nonempty=False)
    else:
        raise ValueError("unsupported source knowledge kind")
    return value


@dataclass(frozen=True, init=False)
class SourceVerification:
    """Sealed result of actual source verification; does not grant admission."""

    _raw: bytes
    evidence: tuple[tuple[HashBoundRef, bytes], ...]
    _identity: tuple
    _seal: object

    def __new__(cls, *args, **kwargs):
        raise TypeError("source verification is produced by the source verifier")

    def payload(self):
        if (type(self) is not SourceVerification or self._seal is not _SEAL
                or self._identity != (self._raw, self.evidence)):
            raise ValueError("source verification changed after observation")
        value = json.loads(self._raw)
        inspect_source_verification(value, evidence=dict(self.evidence))
        return value

    @property
    def reference(self):
        return source_ref(canonical(self.payload()), SOURCE_VERIFICATION_V1, RefKind.ARTIFACT)

    def to_dict(self):
        value = self.payload()
        return {"schema_version": SOURCE_VERIFICATION_V1, "payload": value,
                "verification_ref": source_ref(canonical(value), SOURCE_VERIFICATION_V1, RefKind.ARTIFACT).to_dict()}


def inspect_source_command(value, *, claim):
    """Read a historical command observation against its declared predicate."""
    result = _shape(value, {"name", "status", "command", "returncode", "stdout", "stderr", "duration_ms", "diagnostics"},
                    "observed command")
    if (claim["recipe"] is None or result["command"] != claim["recipe"]["command"]
            or result["name"] != "source-verification" or result["status"] not in {"PASS", "FAIL"}
            or result["returncode"] is not None and type(result["returncode"]) is not int
            or type(result["duration_ms"]) is not int or result["duration_ms"] < 0
            or type(result["stdout"]) is not str or type(result["stderr"]) is not str
            or type(result["diagnostics"]) is not list or any(type(item) is not str for item in result["diagnostics"])):
        raise ValueError("source command observation differs from its invocation")
    exp, combined = claim["recipe"]["expectation"], result["stdout"] + result["stderr"]
    satisfied = (result["returncode"] is not None and result["returncode"] in exp["expected_exit_codes"]
                 and (not exp["expected_nonzero_exit"] or result["returncode"] != 0)
                 and all(item in combined for item in exp["combined_output_contains"])
                 and not any(item in combined for item in exp["combined_output_not_contains"]))
    if result["status"] != ("PASS" if satisfied else "FAIL") or satisfied and result["diagnostics"]:
        raise ValueError("source command status contradicts its observed predicate")
    return result


def inspect_source_verification(value, *, evidence):
    """Reopen every retained byte and the closed claim; never mint authority."""
    _shape(value, {"schema_version", "claim", "claim_ref", "operation_id", "verification_attempt_id",
        "verifier", "started_at_utc", "finished_at_utc", "elapsed_ns", "sources", "bindings",
        "command_result", "knowledge", "knowledge_ref", "external_inputs", "observed_runtime", "runtime_ref"}, "source verification")
    if value["schema_version"] != SOURCE_VERIFICATION_V1 or value["verifier"] != SOURCE_VERIFIER.to_dict():
        raise ValueError("unknown source verifier")
    claim = inspect_source_claim(value["claim"])
    if claim["schema_version"] != SOURCE_CLAIM_V1:
        raise ValueError("retained material is not a verified executable source claim")
    if value["operation_id"] != claim["operation_id"]:
        raise ValueError("source verification names another operation")
    AttemptId(value["verification_attempt_id"])
    if type(value["elapsed_ns"]) is not int or value["elapsed_ns"] < 0:
        raise ValueError("source duration is unavailable")
    for field in ("started_at_utc", "finished_at_utc"):
        moment = datetime.fromisoformat(value[field])
        if moment.tzinfo is None or moment.utcoffset().total_seconds() != 0:
            raise ValueError("source observations require UTC")
    if value["finished_at_utc"] < value["started_at_utc"]:
        raise ValueError("source observation ends before it starts")
    for reference, raw in evidence.items():
        if type(reference) is not HashBoundRef or type(raw) is not bytes:
            raise TypeError("retained source evidence requires exact bytes and references")
        if hashlib.sha256(raw).hexdigest() != reference.sha256 or len(raw) != reference.byte_length:
            raise ValueError("retained source evidence changed")
    def retained(raw_ref):
        ref = HashBoundRef.from_dict(raw_ref)
        if ref not in evidence:
            raise ValueError("source verification lost retained evidence")
        return evidence[ref]
    runtime = _shape(value["observed_runtime"], {"schema_version", "python", "implementation", "system", "machine", "packages"},
                     "source runtime observation")
    runtime_bytes = canonical(runtime)
    if (runtime["schema_version"] != SOURCE_RUNTIME_V1
            or retained(value["runtime_ref"]) != runtime_bytes
            or source_ref(runtime_bytes, SOURCE_RUNTIME_V1).to_dict() != value["runtime_ref"]):
        raise ValueError("source verification lost its actual runtime observation")
    if (retained(value["claim_ref"]) != canonical(claim)
            or source_ref(canonical(claim), SOURCE_CLAIM_V1, RefKind.CONTRACT_CONDITION).to_dict() != value["claim_ref"]):
        raise ValueError("source claim differs from its retained contract")
    if type(value["sources"]) is not list or [s["path"] for s in value["sources"]] != claim["sources"]:
        raise ValueError("source verification changed its source set")
    for item in value["sources"]:
        _shape(item, {"path", "ref"}, "retained source")
        raw = retained(item["ref"])
        if len(raw) > MAX_SOURCE_BYTES or source_ref(raw, SOURCE_FILE_V1).to_dict() != item["ref"]:
            raise ValueError("retained repository source has an invalid identity")
    expected_inputs = [item["ref"] for field in ("policy_inputs", "environment_inputs", "tool_inputs")
                       for item in claim[field]]
    if value["external_inputs"] != expected_inputs:
        raise ValueError("source verification changed observed dependencies")
    for ref in expected_inputs:
        retained(ref)
    if type(value["bindings"]) is not list or len(value["bindings"]) != len(claim["symbols"]):
        raise ValueError("source verification lost its bindings")
    for item, symbol in zip(value["bindings"], claim["symbols"]):
        if any(item[key] != val for key, val in symbol.items()) or item["repository_revision"] != RepositoryRevision.git_commit(claim["revision"]).to_dict():
            raise ValueError("source binding differs from its asserted identity")
        source = next(source for source in value["sources"] if source["path"] == item["path"])
        binding = inspect_retained_python_binding(item, retained(source["ref"]))
        if retained(binding_to_ref(binding).to_dict()) != binding_canonical_payload_bytes(binding):
            raise ValueError("source binding lost its retained canonical payload")
    if claim["kind"] == "REPOSITORY_FACT_CHECK":
        if value["command_result"] is not None:
            raise ValueError("structural knowledge cannot claim command execution")
    else:
        result = inspect_source_command(value["command_result"], claim=claim)
        if result["status"] != "PASS":
            raise ValueError("recipe has no successful observed command contract")
    expected_knowledge = {"schema_version": SOURCE_KNOWLEDGE_V1, "kind": claim["kind"],
        "revision": claim["revision"], "sources": value["sources"], "bindings": value["bindings"],
        "recipe": claim["recipe"], "claim_ref": value["claim_ref"],
        "meaning": "verified-source-identity" if claim["kind"] == "REPOSITORY_FACT_CHECK" else "historically-observed-command-contract"}
    if (value["knowledge"] != expected_knowledge or retained(value["knowledge_ref"]) != canonical(expected_knowledge)
            or source_ref(canonical(expected_knowledge), SOURCE_KNOWLEDGE_V1).to_dict() != value["knowledge_ref"]):
        raise ValueError("delivered knowledge exceeds independently checked observations")
    return value


def verify_source_claim(*, repo_root: Path, claim, external_evidence, attempt_id: AttemptId,
                        observation_sink: Callable | None = None):
    """Execute an explicit source claim against committed inputs.

    An unsuccessful command is returned as a failure with its observations;
    it cannot be converted to a sealed successful knowledge record.
    """
    claim = json.loads(canonical(inspect_source_claim(claim)))
    AttemptId.from_dict(attempt_id.to_dict())
    start, started = time.monotonic_ns(), datetime.now(timezone.utc).isoformat()

    def observe(phase, data, retained=()):
        # The source-operation owner commits these observations. A failed
        # checkpoint stops verification, including before any recipe effects.
        if observation_sink is not None:
            observation_sink({"schema_version": SOURCE_OBSERVATION_V1,
                "operation_id": claim["operation_id"], "verification_attempt_id": attempt_id.value,
                "phase": phase, "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                "elapsed_ns": time.monotonic_ns() - start, "data": data}, dict(retained))

    revision = RepositoryRevision.git_commit(claim["revision"])
    if resolve_revision(repo_root, revision.git_sha) != revision.git_sha:
        raise ValueError("source revision is not the exact commit")
    runtime = json.loads(canonical(observe_source_runtime()))
    runtime_ref = source_ref(canonical(runtime), SOURCE_RUNTIME_V1)
    observe("RUNTIME_OBSERVED", {"runtime_ref": runtime_ref.to_dict()},
            ((runtime_ref, canonical(runtime)),))
    evidence = {}
    for field in ("policy_inputs", "environment_inputs", "tool_inputs"):
        for item in claim[field]:
            ref = HashBoundRef.from_dict(item["ref"])
            raw = external_evidence[ref]
            if hashlib.sha256(raw).hexdigest() != ref.sha256 or len(raw) != ref.byte_length:
                raise ValueError("source environment evidence changed")
            evidence[ref] = raw
    sources, bindings = [], []
    for path in claim["sources"]:
        require_tree_mode(repo_root, revision.git_sha, path, {"100644", "100755"}, "SOURCE_NOT_REGULAR")
        raw = load_committed_bytes(repo_root, revision.git_sha, path)
        if len(raw) > MAX_SOURCE_BYTES:
            raise ValueError("source exceeds byte budget")
        ref = source_ref(raw, SOURCE_FILE_V1)
        evidence[ref] = raw
        sources.append({"path": path, "ref": ref.to_dict()})
        observe("SOURCE_CAPTURED", sources[-1], ((ref, raw),))
    if claim["kind"] == "SOURCE_STUDY":
        for item in sources:
            if not item["path"].endswith(".py"):
                observe("SOURCE_UNINTERPRETED", {"path": item["path"], "reason": "NO_STRUCTURAL_RESOLVER"})
                continue
            try:
                discovered = discover_retained_python_bindings(evidence[HashBoundRef.from_dict(item["ref"])],
                    repository_revision=revision, path=item["path"])
            except BindingViolation as exc:
                observe("SOURCE_UNINTERPRETED", {"path": item["path"], "reason": exc.failure_code.value})
                continue
            for binding in discovered:
                observe("BINDING_RESOLVED", {"binding": binding.to_dict()},
                        ((binding_to_ref(binding), binding_canonical_payload_bytes(binding)),))
        return {"status": "RETAINED", "operation_id": claim["operation_id"],
                "elapsed_ns": time.monotonic_ns() - start, "reason": "SOURCE_MATERIALS_RETAINED"}
    for symbol in claim["symbols"]:
        binding = resolve_python_binding(repo_root, repository_revision=revision,
            **{k: v for k, v in symbol.items() if k != "symbol_kind"},
            symbol_kind=PythonSymbolKind(symbol["symbol_kind"]), resolver_version=PYTHON_BINDING_RESOLVER_V1)
        bindings.append(binding.to_dict())
        evidence[binding_to_ref(binding)] = binding_canonical_payload_bytes(binding)
        observe("BINDING_RESOLVED", {"binding": binding.to_dict()},
                ((binding_to_ref(binding), binding_canonical_payload_bytes(binding)),))
    command_result = None
    if claim["recipe"] is not None:
        recipe = claim["recipe"]
        if recipe["command"][0] != sys.executable:
            raise ValueError("source recipe profile requires this measured Python interpreter")
        exp = dict(recipe["expectation"])
        for name in ("expected_exit_codes", "combined_output_contains", "combined_output_not_contains"):
            exp[name] = tuple(exp[name])
        checkout = create_detached_worktree(repo_root, revision.git_sha)
        try:
            observe("COMMAND_STARTED", {"command": recipe["command"]})
            result = run_expected_command(recipe["command"], checkout.path,
                                          CommandExpectation(**exp), "source-verification")
            command_result = result.to_json()
            # Retain output before cleanup or further checks can fail. A
            # checked sub-result survives failure of the complete operation.
            observe("COMMAND_FINISHED", {"command_result": command_result})
            clean = assert_clean_worktree(checkout.path)
            observe("WORKTREE_CHECKED", {"clean": clean})
            if result.status != "PASS" or not clean:
                return {"schema_version": SOURCE_VERIFICATION_V1,
                        "status": "INTERRUPTED" if result.returncode is None else "REJECTED",
                        "claim": claim, "command_result": command_result,
                        "elapsed_ns": time.monotonic_ns() - start,
                        "reason": "SOURCE_COMMAND_UNVERIFIED_OR_MUTATED"}
        finally:
            cleanup_worktree(checkout, keep=False)
        unchanged = canonical(observe_source_runtime()) == canonical(runtime)
        observe("RUNTIME_RECHECKED", {"unchanged": unchanged})
        if not unchanged:
            raise ValueError("source command changed the measured Python environment")
    claim_ref = source_ref(canonical(claim), SOURCE_CLAIM_V1, RefKind.CONTRACT_CONDITION)
    evidence[claim_ref] = canonical(claim)
    knowledge = {"schema_version": SOURCE_KNOWLEDGE_V1, "kind": claim["kind"], "revision": claim["revision"],
        "sources": sources, "bindings": bindings, "recipe": claim["recipe"], "claim_ref": claim_ref.to_dict(),
        "meaning": "verified-source-identity" if claim["kind"] == "REPOSITORY_FACT_CHECK" else "historically-observed-command-contract"}
    knowledge_ref = source_ref(canonical(knowledge), SOURCE_KNOWLEDGE_V1)
    evidence[knowledge_ref] = canonical(knowledge)
    runtime_ref = source_ref(canonical(runtime), SOURCE_RUNTIME_V1)
    evidence[runtime_ref] = canonical(runtime)
    if sum(len(raw) for raw in evidence.values()) > MAX_EVIDENCE_BYTES:
        raise ValueError("source verification exceeds its retained byte budget")
    value = {"schema_version": SOURCE_VERIFICATION_V1, "claim": claim, "claim_ref": claim_ref.to_dict(),
        "operation_id": claim["operation_id"], "verification_attempt_id": attempt_id.value,
        "verifier": SOURCE_VERIFIER.to_dict(), "started_at_utc": started,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(), "elapsed_ns": time.monotonic_ns() - start,
        "sources": sources, "bindings": bindings, "command_result": command_result,
        "observed_runtime": runtime, "runtime_ref": runtime_ref.to_dict(),
        "knowledge": knowledge, "knowledge_ref": knowledge_ref.to_dict(),
        "external_inputs": [item["ref"] for field in ("policy_inputs", "environment_inputs", "tool_inputs") for item in claim[field]]}
    raw, retained = canonical(value), tuple(evidence.items())
    if len(raw) > MAX_EVIDENCE_BYTES:
        raise ValueError("source observations exceed their retained byte budget")
    verified = object.__new__(SourceVerification)
    for name, item in {"_raw": raw, "evidence": retained, "_identity": (raw, retained), "_seal": _SEAL}.items():
        object.__setattr__(verified, name, item)
    verified.payload()
    observe("VERIFIED", {"verification_ref": verified.reference.to_dict()}, (
        (verified.reference, raw), (claim_ref, canonical(claim)), (knowledge_ref, canonical(knowledge))))
    return verified


def inspect_source_observations(claim, observations, evidence):
    """Reopen observed parts without converting an unfinished claim to proof."""
    inspect_source_claim(claim)
    for ref, raw in evidence.items():
        if (type(ref) is not HashBoundRef or type(raw) is not bytes
                or len(raw) != ref.byte_length or hashlib.sha256(raw).hexdigest() != ref.sha256):
            raise ValueError("source observation lost retained bytes")

    def retained(raw_ref):
        ref = HashBoundRef.from_dict(raw_ref)
        if ref not in evidence:
            raise ValueError("source observation lost retained evidence")
        return evidence[ref]

    sources, bindings, uninterpreted = [], [], []
    command_started, command_result, clean, unchanged, proof = False, None, None, None, None
    runtime_ref, attempt_id, last_elapsed = None, None, -1
    for observation in observations:
        _shape(observation, {"schema_version", "operation_id", "verification_attempt_id", "phase",
                            "observed_at_utc", "elapsed_ns", "data"}, "source observation")
        if (observation["schema_version"] != SOURCE_OBSERVATION_V1
                or observation["operation_id"] != claim["operation_id"]
                or attempt_id is not None and observation["verification_attempt_id"] != attempt_id or proof is not None):
            raise ValueError("source observation belongs to another operation or follows final verification")
        attempt_id = AttemptId(observation["verification_attempt_id"]).value
        moment = datetime.fromisoformat(observation["observed_at_utc"])
        elapsed = observation["elapsed_ns"]
        if (moment.tzinfo is None or moment.utcoffset().total_seconds() != 0
                or type(elapsed) is not int or elapsed < last_elapsed):
            raise ValueError("source observation has invalid time evidence")
        last_elapsed = elapsed
        phase, data = observation["phase"], observation["data"]
        if phase == "RUNTIME_OBSERVED":
            _shape(data, {"runtime_ref"}, phase)
            if runtime_ref is not None or sources or bindings:
                raise ValueError("source runtime observation is repeated or out of order")
            runtime_ref = data["runtime_ref"]
            runtime = json.loads(retained(runtime_ref))
            if source_ref(canonical(runtime), SOURCE_RUNTIME_V1).to_dict() != runtime_ref:
                raise ValueError("source runtime identity differs")
        elif phase == "SOURCE_CAPTURED":
            _shape(data, {"path", "ref"}, phase)
            if (runtime_ref is None or bindings or command_started
                    or len(sources) >= len(claim["sources"]) or data["path"] != claim["sources"][len(sources)]
                    or source_ref(retained(data["ref"]), SOURCE_FILE_V1).to_dict() != data["ref"]):
                raise ValueError("retained source differs from the observed claim prefix")
            sources.append(data)
        elif phase == "BINDING_RESOLVED":
            _shape(data, {"binding"}, phase)
            item = data["binding"]
            source = next((source for source in sources if source["path"] == item["path"]), None)
            if source is None or command_started or len(sources) != len(claim["sources"]):
                raise ValueError("binding observation precedes its sources or follows command execution")
            binding = inspect_retained_python_binding(item, retained(source["ref"]))
            if (item["repository_revision"] != RepositoryRevision.git_commit(claim["revision"]).to_dict()
                    or retained(binding_to_ref(binding).to_dict()) != binding_canonical_payload_bytes(binding)):
                raise ValueError("observed binding differs from retained identity")
            if claim["schema_version"] == SOURCE_CLAIM_V1:
                if len(bindings) >= len(claim["symbols"]) or any(item[k] != v for k, v in claim["symbols"][len(bindings)].items()):
                    raise ValueError("observed binding differs from the requested symbol prefix")
            if item in bindings:
                raise ValueError("observed binding is repeated")
            bindings.append(item)
        elif phase == "SOURCE_UNINTERPRETED":
            _shape(data, {"path", "reason"}, phase)
            if (claim["kind"] != "SOURCE_STUDY" or data["path"] not in [item["path"] for item in sources]
                    or type(data["reason"]) is not str or not data["reason"]):
                raise ValueError("uninterpreted material differs from retained sources")
            uninterpreted.append(data)
        elif phase == "COMMAND_STARTED":
            _shape(data, {"command"}, phase)
            if (command_started or claim["recipe"] is None or data["command"] != claim["recipe"]["command"]
                    or len(bindings) != len(claim["symbols"]) or len(sources) != len(claim["sources"])):
                raise ValueError("source command was not preceded by complete input observations")
            command_started = True
        elif phase == "COMMAND_FINISHED":
            _shape(data, {"command_result"}, phase)
            if not command_started or command_result is not None:
                raise ValueError("source command observation differs from its invocation")
            command_result = inspect_source_command(data["command_result"], claim=claim)
        elif phase == "WORKTREE_CHECKED":
            _shape(data, {"clean"}, phase)
            if command_result is None or clean is not None or type(data["clean"]) is not bool:
                raise ValueError("source worktree check has no matching command")
            clean = data["clean"]
        elif phase == "RUNTIME_RECHECKED":
            _shape(data, {"unchanged"}, phase)
            if clean is not True or unchanged is not None or type(data["unchanged"]) is not bool:
                raise ValueError("source runtime recheck has no clean completed command")
            unchanged = data["unchanged"]
        elif phase == "VERIFIED":
            _shape(data, {"verification_ref"}, phase)
            proof = json.loads(retained(data["verification_ref"]))
            inspect_source_verification(proof, evidence=evidence)
            if (source_ref(canonical(proof), SOURCE_VERIFICATION_V1, RefKind.ARTIFACT).to_dict() != data["verification_ref"]
                    or proof["claim"] != claim or proof["sources"] != sources or proof["bindings"] != bindings
                    or proof["verification_attempt_id"] != attempt_id
                    or proof["command_result"] != command_result or proof["runtime_ref"] != runtime_ref
                    or command_started and (clean is not True or unchanged is not True)):
                raise ValueError("source verification differs from its actual observation history")
        else:
            raise ValueError("source observation has an unknown phase")
    return {"sources": sources, "bindings": bindings, "uninterpreted": uninterpreted,
            "command_started": command_started, "command_result": command_result,
            "worktree_clean": clean, "runtime_unchanged": unchanged, "verification": proof}
