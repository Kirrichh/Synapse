"""Freeze operator-selected adapter definitions before loading plugin code."""

from dataclasses import asdict, replace
import hashlib
from importlib import metadata
from pathlib import Path
import re
import shutil
import platform
import sys

from .codec import canonical_bytes, decode_json, digest
from .contracts import AgentProfile, AgentTransportKind, LocalInformationPolicy
from .policy import ResourceBudget, RuntimePolicy, IsolationKind
from .registry import AgentRegistry, CapabilityAdmission, load_admitted_adapter, discover_adapter_factories


AGENT_CONFIGURATION_V1 = "synapse.agent.configuration/v1"
CAPABILITY_ADMISSION_V1 = "synapse.agent.capability-admission/v1"
_EXECUTION_DEPENDENCIES = {"jsonschema", "psutil", "filelock", "packaging"}
_BUILTINS = {
    "stdio": ("synapse.agents.stdio_adapter", "StdioAdapterFactory"),
    "docling": ("synapse.agents.docling_adapter", "DoclingAdapterFactory"),
    "acp": ("synapse.agents.acp_adapter", "AcpAdapterFactory"),
    "a2a": ("synapse.agents.a2a_adapter", "A2AAdapterFactory"),
}


def profile_from_dict(value):
    if type(value) is not dict:
        raise TypeError("agent profile must be an object")
    data = dict(value)
    for key in ("capabilities", "accepted_media_types", "output_profiles", "effect_classes"):
        if type(data.get(key)) is not list:
            raise ValueError("profile collections must be JSON arrays")
        data[key] = tuple(data[key])
    data["transport"] = AgentTransportKind(data["transport"])
    data["local_information_policy"] = LocalInformationPolicy(data["local_information_policy"])
    limits = data.get("resource_limits")
    policy = dict(data["runtime_policy"])
    policy["isolation"] = IsolationKind(policy["isolation"])
    for key in ("read_only_paths", "gpu_devices"):
        policy[key] = tuple(policy.get(key, ()))
    data["runtime_policy"] = RuntimePolicy(**policy)
    data["resource_limits"] = ResourceBudget(**limits)
    return AgentProfile(**data)


def _file_identity(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("runtime identity requires a regular non-symlink file")
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(1024**2):
            hasher.update(block)
            size += len(block)
    return {"path": str(path.absolute()), "sha256": hasher.hexdigest(), "byte_length": size}


def distribution_identity(names):
    records = []
    # Resolve the installed dependency closure, including markers for this
    # interpreter. Uninstalled extras are not silently treated as installed.
    from packaging.requirements import Requirement
    pending, resolved = list(names), set()
    while pending:
        name = pending.pop()
        canonical = re.sub(r"[-_.]+", "-", name).lower()
        if canonical in resolved:
            continue
        dist = metadata.distribution(name)
        resolved.add(canonical)
        for requirement in dist.requires or ():
            dependency = Requirement(requirement)
            if dependency.marker is None or dependency.marker.evaluate({"extra": ""}):
                pending.append(dependency.name)
    for name in sorted(resolved):
        dist = metadata.distribution(name)
        files = []
        for item in sorted(dist.files or (), key=str):
            if str(item).endswith((".pyc", ".pyo")):
                continue
            path = Path(dist.locate_file(item)).absolute()
            if path.is_file():
                files.append({"name": str(item), **_file_identity(path)})
        records.append({"name": dist.metadata["Name"], "version": dist.version,
                        "files_sha256": digest(files), "file_count": len(files)})
    return records


def validate_configuration(value):
    if type(value) is not dict or set(value) != {"schema_version", "profiles", "preferred_profiles"}:
        raise ValueError("agent configuration has an unknown shape")
    if value["schema_version"] != AGENT_CONFIGURATION_V1 or type(value["profiles"]) is not list or not value["profiles"]:
        raise ValueError("agent configuration requires explicit profiles")
    if type(value["preferred_profiles"]) is not list:
        raise ValueError("agent preferences must be an ordered array")
    ids = []
    for definition in value["profiles"]:
        if type(definition) is not dict or set(definition) != {
            "factory", "profile", "native", "runtime_files", "distributions", "runtime_environment", "admission"
        }:
            raise ValueError("agent definition has an unknown shape")
        profile = profile_from_dict(definition["profile"])
        ids.append(profile.profile_id)
        if type(definition["factory"]) is not str or not definition["factory"]:
            raise ValueError("agent factory must be explicitly selected")
        if profile.configuration_sha256 != digest({"factory": definition["factory"], "native": definition["native"]}):
            raise ValueError("agent configuration digest differs from its profile")
        identity = {"files": definition["runtime_files"], "distributions": definition["distributions"],
                    "environment": definition["runtime_environment"]}
        if profile.runtime_identity != digest(identity):
            raise ValueError("agent runtime identity differs from its profile")
        admission = definition["admission"]
        if type(admission) is not dict or set(admission) != {"schema_version", "profile_sha256", "capabilities", "evidence"}:
            raise ValueError("agent requires a separately retained capability-admission record")
        if admission["schema_version"] != CAPABILITY_ADMISSION_V1 or admission["profile_sha256"] != digest(profile):
            raise ValueError("capability admission refers to another profile")
        if type(admission["evidence"]) is not list or not admission["evidence"]:
            raise ValueError("capability admission lacks retained acceptance evidence")
        CapabilityAdmission(admission["profile_sha256"], tuple(admission["capabilities"]), digest(admission))
    if len(set(ids)) != len(ids) or not set(value["preferred_profiles"]).issubset(ids):
        raise ValueError("duplicate profiles or unknown operator preference")
    return value


def verify_configuration_runtime(value):
    validate_configuration(value)
    for definition in value["profiles"]:
        if definition["runtime_environment"] != runtime_environment():
            raise ValueError("agent interpreter or platform changed since admission")
        for record in definition["runtime_files"]:
            if _file_identity(record["path"]) != record:
                raise ValueError("agent executable, adapter or runtime file changed")
        required = required_runtime_files(definition["factory"], definition["native"])
        paths = {item["path"] for item in definition["runtime_files"]}
        if not set(required).issubset(paths):
            raise ValueError("agent identity omits its executable or adapter implementation")
        names = [item["name"] for item in definition["distributions"]]
        minimum = {"docling": {"docling-agent", "docling"},
                   "acp": {"agent-client-protocol"}, "a2a": {"a2a-sdk", "httpx"}}.get(definition["factory"], set())
        normalized = {re.sub(r"[-_.]+", "-", n).lower() for n in names}
        if not (minimum | _EXECUTION_DEPENDENCIES).issubset(normalized):
            raise ValueError("agent identity omits its required installed SDK")
        if definition["factory"] not in _BUILTINS:
            matches = [p for p in discover_adapter_factories() if p.name == definition["factory"]]
            if len(matches) != 1 or matches[0].dist.metadata["Name"] not in names:
                raise ValueError("plugin identity must include its installed distribution")
        if distribution_identity(names) != definition["distributions"]:
            raise ValueError("installed agent dependencies changed since admission")
        for record in definition["admission"]["evidence"]:
            if _file_identity(record["path"]) != record:
                raise ValueError("retained capability-acceptance evidence changed")


def registry_from_configuration(value, *, context=None):
    verify_configuration_runtime(value)
    adapters, admissions, retained = [], [], []
    for definition in value["profiles"]:
        name = definition["factory"]
        if name in _BUILTINS:
            from importlib import import_module
            module, symbol = _BUILTINS[name]
            factory = getattr(import_module(module), symbol)()
            adapter = factory.create({"profile": definition["profile"], "native": definition["native"], "context": context})
        else:
            adapter = load_admitted_adapter(entry_point_name=name,
                configuration={"profile": definition["profile"], "native": definition["native"], "context": context})
        if digest(adapter.profile) != digest(profile_from_dict(definition["profile"])):
            raise ValueError("loaded adapter profile differs from its admitted identity")
        record = definition["admission"]
        retained.append(canonical_bytes(record))
        retained.extend(Path(item["path"]).read_bytes() for item in record["evidence"])
        adapters.append(adapter)
        admissions.append(CapabilityAdmission(record["profile_sha256"], tuple(record["capabilities"]), digest(record)))
    return AgentRegistry(tuple(adapters), admissions=tuple(admissions),
                         preferred_profiles=tuple(value["preferred_profiles"]), retained_evidence=tuple(retained))


def required_runtime_files(factory, native):
    """Capture executable and trusted adapter code before importing a plugin."""
    paths = {str(p.resolve()) for p in Path(__file__).parent.glob("*.py")}
    import sys
    paths.add(str(Path(sys.executable).resolve()))
    command = native.get("command")
    if command:
        executable = shutil.which(command[0])
        if executable is None:
            raise ValueError("declared agent executable is unavailable")
        paths.add(str(Path(executable).resolve()))
        paths.update(str(Path(p).resolve()) for p in command[1:] if Path(p).is_file())
    if native.get("python"):
        paths.add(str(Path(native["python"]).resolve()))
    if native.get("artifacts_path"):
        models = Path(native["artifacts_path"])
        if not models.is_absolute() or not models.is_dir():
            raise ValueError("offline model artifacts must already be installed")
        model_files = tuple(p for p in models.rglob("*") if p.is_file())
        if not model_files:
            raise ValueError("offline model artifacts are empty")
        paths.update(str(p.absolute()) for p in model_files)
    return tuple(sorted(paths))


def capture_definition(*, factory, profile, native, distributions, evidence_paths, capabilities,
                       runtime_files=()):
    """Bind independently supplied acceptance evidence; never run or invent it.

    Operators retain evidence from their adapter acceptance, then explicitly call
    this function with the capability subset they admit for that exact runtime.
    """
    evidence = [_file_identity(Path(p).absolute()) for p in evidence_paths]
    if not evidence:
        raise ValueError("operator admission requires actual retained acceptance evidence")
    files = [_file_identity(Path(p)) for p in sorted(set((*runtime_files, *required_runtime_files(factory, native))))]
    dependencies = distribution_identity((*distributions, *_EXECUTION_DEPENDENCIES))
    environment = runtime_environment()
    identity = {"files": files, "distributions": dependencies, "environment": environment}
    bound = replace(profile, configuration_sha256=digest({"factory": factory, "native": native}),
                    runtime_identity=digest(identity))
    definition = {"factory": factory, "profile": decode_json(canonical_bytes(bound)), "native": native,
        "runtime_files": files, "distributions": dependencies, "runtime_environment": environment,
        "admission": {"schema_version": CAPABILITY_ADMISSION_V1, "profile_sha256": digest(bound),
            "capabilities": sorted(set(capabilities)), "evidence": evidence}}
    validate_configuration({"schema_version": AGENT_CONFIGURATION_V1, "profiles": [definition], "preferred_profiles": []})
    return definition


def runtime_environment():
    return {"python_version": platform.python_version(), "implementation": sys.implementation.name,
            "cache_tag": sys.implementation.cache_tag, "system": platform.system(), "machine": platform.machine()}
