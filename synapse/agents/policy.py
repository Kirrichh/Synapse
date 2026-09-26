"""Frozen, agent-independent execution limits and isolation requirements."""

from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
import re


class IsolationKind(str, Enum):
    BUBBLEWRAP = "BUBBLEWRAP"
    OCI = "OCI"
    # Only a separately admitted trusted adapter may use host process access.
    TRUSTED_PROCESS = "TRUSTED_PROCESS"
    REMOTE = "REMOTE"


@dataclass(frozen=True)
class ResourceBudget:
    timeout_seconds: int = 300
    memory_bytes: int = 4 * 1024**3
    cpu_seconds: int = 300
    process_limit: int = 64
    stdout_bytes: int = 4 * 1024**2
    stderr_bytes: int = 1024**2
    output_bytes: int = 32 * 1024**2
    scratch_bytes: int = 1024**3

    def __post_init__(self):
        if any(type(v) is not int or not 0 < v <= 2**53 - 1 for v in asdict(self).values()):
            raise ValueError("resource budgets must be positive bounded exact integers")

    def fits(self, ceiling: "ResourceBudget") -> bool:
        return all(v <= asdict(ceiling)[k] for k, v in asdict(self).items())


@dataclass(frozen=True)
class RuntimePolicy:
    isolation: IsolationKind = IsolationKind.BUBBLEWRAP
    network: str = "NONE"
    read_only_paths: tuple[str, ...] = ()
    writable_workspace: bool = False
    image: str | None = None
    executable: str | None = None
    gpu_devices: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.isolation) is not IsolationKind or self.network not in {"NONE", "LOCAL_BROKER", "REMOTE_ENDPOINT"}:
            raise ValueError("unknown runtime isolation or network policy")
        if type(self.writable_workspace) is not bool:
            raise TypeError("workspace access must be explicit")
        if self.network == "LOCAL_BROKER" and self.isolation is not IsolationKind.TRUSTED_PROCESS:
            raise ValueError("local broker access requires an admitted trusted process")
        if self.network == "REMOTE_ENDPOINT" and self.isolation is not IsolationKind.REMOTE:
            raise ValueError("remote egress is available only through the trusted endpoint transport")
        if self.isolation is IsolationKind.REMOTE and (self.network != "REMOTE_ENDPOINT" or self.writable_workspace):
            raise ValueError("remote adapters cannot acquire a local writable workspace")
        for paths in (self.read_only_paths, self.gpu_devices):
            if type(paths) is not tuple or paths != tuple(sorted(set(paths))):
                raise ValueError("runtime paths and devices must be sorted and unique")
            if any(type(p) is not str or not Path(p).is_absolute() or "\x00" in p for p in paths):
                raise ValueError("runtime paths must be absolute")
        if self.isolation is IsolationKind.OCI and (
            type(self.image) is not str or "@sha256:" not in self.image
            or re.fullmatch(r"[0-9a-f]{64}", self.image.rsplit("@sha256:", 1)[1]) is None
        ):
            raise ValueError("OCI images must be pinned by digest")


class AgentFailureCode(str, Enum):
    NO_ELIGIBLE_AGENT = "NO_ELIGIBLE_AGENT"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    INPUT_INVALID = "INPUT_INVALID"
    ARTIFACT_UNAVAILABLE = "ARTIFACT_UNAVAILABLE"
    RUNTIME_NOT_FOUND = "RUNTIME_NOT_FOUND"
    PROCESS_NOT_STARTED = "PROCESS_NOT_STARTED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    NATIVE_PROTOCOL_ERROR = "NATIVE_PROTOCOL_ERROR"
    OUTPUT_INVALID = "OUTPUT_INVALID"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    NETWORK_DENIED = "NETWORK_DENIED"
    LOCAL_INFORMATION_POLICY_VIOLATION = "LOCAL_INFORMATION_POLICY_VIOLATION"
    EXECUTION_STATE_UNKNOWN = "EXECUTION_STATE_UNKNOWN"


class AgentExecutionError(RuntimeError):
    def __init__(self, code: AgentFailureCode, detail: str):
        self.code = code
        super().__init__(detail)
