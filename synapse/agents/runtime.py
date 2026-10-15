"""One supervisor for bounded local agent processes and their retained evidence."""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time
import tempfile
import ctypes
import ctypes.util
import errno

from .contracts import AgentExecutionRequest, AgentProfile, AgentRuntimeContext
from .policy import AgentExecutionError, AgentFailureCode, IsolationKind
from .retention import InvocationStore


@dataclass(frozen=True)
class ProcessObservation:
    returncode: int
    stdout: bytes
    stderr: bytes
    failure: AgentFailureCode | None
    evidence_refs: tuple[str, ...]


def _terminate_tree(process):
    import psutil
    try:
        children = process.children(recursive=True) if getattr(process, "_synapse_observable", False) else []
    except psutil.Error:
        children = []
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    for child in reversed(children):
        try:
            child.kill()
        except psutil.Error:
            pass
    try:
        process.kill()
    except psutil.Error:
        pass
    psutil.wait_procs(children, timeout=2)


def _network_filter():
    """Compile a deny-socket filter with libseccomp; descendants inherit it."""
    name = ctypes.util.find_library("seccomp")
    if not name:
        raise AgentExecutionError(AgentFailureCode.RUNTIME_NOT_FOUND, "libseccomp is required for network isolation")
    lib = ctypes.CDLL(name, use_errno=True)
    lib.seccomp_init.argtypes, lib.seccomp_init.restype = [ctypes.c_uint32], ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    context = lib.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW
    if not context:
        raise OSError("libseccomp could not allocate a filter")
    stream = tempfile.TemporaryFile()
    try:
        for syscall in (b"socket", b"socketcall", b"connect", b"sendto", b"sendmsg", b"sendmmsg",
                        b"io_uring_setup", b"bpf", b"ptrace"):
            number = lib.seccomp_syscall_resolve_name(syscall)
            if number >= 0 and lib.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0):
                raise OSError("libseccomp refused a network isolation rule")
        if lib.seccomp_export_bpf(context, stream.fileno()):
            raise OSError("libseccomp could not export its filter")
        stream.seek(0)
        return stream
    except BaseException:
        stream.close()
        raise
    finally:
        lib.seccomp_release(context)


def _writable_targets(request, runtime):
    targets = set()
    root = runtime.execution_root.resolve()
    for entry in request.allowed_scope:
        path = root / entry
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise AgentExecutionError(AgentFailureCode.INPUT_INVALID, "writable target escapes the workspace")
        files = path.rglob("*") if path.is_dir() else (path,)
        for item in files:
            if item.is_symlink() or not item.resolve().is_relative_to(root) or ".git" in item.relative_to(root).parts:
                raise AgentExecutionError(AgentFailureCode.INPUT_INVALID, "writable scope contains an unsafe target")
            if item.is_file():
                targets.add(str(item))
            if len(targets) > 4096:
                raise AgentExecutionError(AgentFailureCode.RESOURCE_LIMIT, "writable target count exceeds the runtime limit")
    return tuple(sorted(targets))


def _command(argv, request, runtime, profile, scratch, *, filter_fd=None, environment=()):
    policy = profile.runtime_policy
    if policy.isolation is IsolationKind.TRUSTED_PROCESS:
        return list(argv), runtime.execution_root
    if policy.isolation is IsolationKind.REMOTE:
        raise AgentExecutionError(AgentFailureCode.NETWORK_DENIED, "remote profile cannot launch a local process")
    for path in policy.read_only_paths:
        source = Path(path)
        if source.resolve() == Path(source.anchor) or (
            runtime.evidence_root is not None and runtime.evidence_root.resolve().is_relative_to(source.resolve())
        ):
            raise AgentExecutionError(AgentFailureCode.LOCAL_INFORMATION_POLICY_VIOLATION, "runtime mount would expose retained execution state")
        if not source.exists():
            raise AgentExecutionError(AgentFailureCode.RUNTIME_NOT_FOUND, "declared runtime mount is unavailable")
    if policy.isolation is IsolationKind.BUBBLEWRAP:
        executable = policy.executable or shutil.which("bwrap")
        if executable is None:
            raise AgentExecutionError(AgentFailureCode.RUNTIME_NOT_FOUND, "bubblewrap is required by the admitted profile")
        command = [executable, "--unshare-all", "--share-net", "--die-with-parent", "--new-session",
                   "--cap-drop", "ALL", "--seccomp", str(filter_fd)]
        for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc/ld.so.cache"):
            if Path(path).exists():
                command.extend(("--ro-bind", path, path))
        command.extend(("--proc", "/proc", "--dev", "/dev", "--size", str(request.resource_budget.scratch_bytes),
                        "--tmpfs", "/tmp", "--symlink", "/tmp", str(scratch), "--dir", "/tmp/home"))
        for path in policy.read_only_paths:
            command.extend(("--ro-bind", path, path))
        if policy.writable_workspace:
            command.extend(("--ro-bind", str(runtime.execution_root), str(runtime.execution_root)))
            for target in _writable_targets(request, runtime):
                command.extend(("--bind", target, target))
        for artifact in request.artifacts:
            command.extend(("--ro-bind", artifact.path, artifact.path))
        for device in policy.gpu_devices:
            command.extend(("--dev-bind", device, device))
        cwd = runtime.execution_root if policy.writable_workspace else scratch
        limiter = shutil.which("prlimit")
        if limiter is None:
            raise AgentExecutionError(AgentFailureCode.RUNTIME_NOT_FOUND, "prlimit is required by local process policy")
        limited = [limiter, f"--as={request.resource_budget.memory_bytes}",
            f"--cpu={request.resource_budget.cpu_seconds}", f"--fsize={request.resource_budget.scratch_bytes}",
            "--core=0", "--nofile=256", "--", *argv]
        command.extend(("--chdir", str(cwd), "--", *limited))
        return command, scratch
    executable = policy.executable or shutil.which("docker") or shutil.which("podman")
    if executable is None:
        raise AgentExecutionError(AgentFailureCode.RUNTIME_NOT_FOUND, "an OCI runtime is required by the admitted profile")
    if policy.gpu_devices:
        raise AgentExecutionError(AgentFailureCode.INPUT_INVALID, "OCI GPU device admission is not supported by this runtime profile")
    if request.resource_budget.cpu_seconds < request.resource_budget.timeout_seconds:
        raise AgentExecutionError(AgentFailureCode.RESOURCE_LIMIT,
            "the OCI profile bounds CPU at one core for the wall-clock budget")
    container_name = "synapse-" + runtime.invocation_root.name
    command = [executable, "run", "--name", container_name, "--rm", "--interactive", "--network=none", "--read-only",
        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit", str(request.resource_budget.process_limit),
        "--memory", str(request.resource_budget.memory_bytes), "--cpus", "1", "--user", f"{os.getuid()}:{os.getgid()}",
        "--tmpfs", f"/tmp:rw,nosuid,nodev,size={request.resource_budget.scratch_bytes}"]
    cwd = str(runtime.execution_root) if policy.writable_workspace else "/tmp"
    for path in policy.read_only_paths:
        command.extend(("--mount", f"type=bind,source={path},target={path},readonly"))
    for artifact in request.artifacts:
        command.extend(("--mount", f"type=bind,source={artifact.path},target={artifact.path},readonly"))
    if policy.writable_workspace:
        command.extend(("--mount", f"type=bind,source={runtime.execution_root},target={runtime.execution_root},readonly"))
        for target in _writable_targets(request, runtime):
            command.extend(("--mount", f"type=bind,source={target},target={target}"))
    for key, value in environment:
        command.extend(("--env", key + "=" + value))
    command.extend(("--workdir", cwd, policy.image, *argv))
    return command, scratch


def run_process(*, request: AgentExecutionRequest, runtime: AgentRuntimeContext, profile: AgentProfile,
                argv: tuple[str, ...], input_bytes: bytes = b"", environment=()) -> ProcessObservation:
    import psutil
    if runtime.evidence_root is None or runtime.invocation_root is None:
        raise ValueError("process supervisor requires an execution-owner context")
    budget = request.resource_budget
    store = InvocationStore(runtime.evidence_root)
    scratch = runtime.invocation_root / "scratch"
    scratch.mkdir(parents=True, exist_ok=False, mode=0o700)
    (scratch / "home").mkdir(mode=0o700)
    env = {"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "HOME": str(scratch / "home"), "TMPDIR": str(scratch), "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
    if profile.runtime_policy.isolation is IsolationKind.OCI:
        env.update({"HOME": "/tmp", "TMPDIR": "/tmp"})
    env.update(dict(environment))
    if profile.runtime_policy.isolation is not IsolationKind.TRUSTED_PROCESS and any(
        key.upper().endswith(("API_KEY", "ACCESS_TOKEN", "SECRET_KEY")) for key in env
    ):
        raise AgentExecutionError(AgentFailureCode.LOCAL_INFORMATION_POLICY_VIOLATION, "sandbox agents cannot receive provider credentials")
    flags = {"start_new_session": True} if os.name == "posix" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    network_filter = _network_filter() if profile.runtime_policy.isolation is IsolationKind.BUBBLEWRAP else None
    launched_at = time.time()
    try:
        command, cwd = _command(argv, request, runtime, profile, scratch,
            filter_fd=None if network_filter is None else network_filter.fileno(), environment=tuple(sorted(env.items())))
        if network_filter is not None:
            flags["pass_fds"] = (network_filter.fileno(),)
        process = psutil.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, **flags)
    finally:
        if network_filter is not None:
            network_filter.close()
    try:
        created_at = process.create_time()
    except psutil.Error:
        created_at = None
    process._synapse_observable = created_at is not None and created_at >= launched_at - 1
    try:
        refs = [store.event(request.invocation_id, {"kind": "PROCESS_STARTED", "pid": process.pid,
                    "created_at": created_at if process._synapse_observable else None,
                    "resource_observation": "AVAILABLE" if process._synapse_observable else "UNAVAILABLE", "argv": command})]
    except BaseException:
        _terminate_tree(process)
        process.wait(timeout=5)
        raise
    exceeded = threading.Event()
    buffers = [bytearray(), bytearray()]
    def read(pipe, index, limit):
        try:
            while True:
                block = pipe.read(65536)
                if not block:
                    break
                remaining = max(0, limit - len(buffers[index]))
                buffers[index].extend(block[:remaining])
                if len(block) > remaining:
                    exceeded.set()
        finally:
            pipe.close()
    def write():
        try:
            process.stdin.write(input_bytes)
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            process.stdin.close()
    threads = [threading.Thread(target=read, args=(process.stdout, 0, budget.stdout_bytes), daemon=True),
               threading.Thread(target=read, args=(process.stderr, 1, budget.stderr_bytes), daemon=True),
               threading.Thread(target=write, daemon=True)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + budget.timeout_seconds
    failure = None
    peak_memory, cpu_seconds = 0, 0.0
    observed_cpu = {}
    try:
        while process.poll() is None:
            if store.is_cancelled(request.invocation_id):
                failure = AgentFailureCode.CANCELLED
            elif time.monotonic() >= deadline:
                failure = AgentFailureCode.TIMEOUT
            elif exceeded.is_set():
                failure = AgentFailureCode.RESOURCE_LIMIT
            else:
                try:
                    descendants = process.children(recursive=True) if process._synapse_observable else []
                    memory, cpu = 0, 0.0
                    for item in ((process, *descendants) if process._synapse_observable else ()):
                        try:
                            memory += item.memory_info().rss
                            times = item.cpu_times()
                            observed_cpu[(item.pid, item.create_time())] = times.user + times.system
                        except psutil.Error:
                            continue
                    peak_memory = max(peak_memory, memory)
                    cpu = sum(observed_cpu.values())
                    cpu_seconds = max(cpu_seconds, cpu)
                    if memory > budget.memory_bytes or cpu > budget.cpu_seconds or len(descendants) + 1 > budget.process_limit:
                        failure = AgentFailureCode.RESOURCE_LIMIT
                except psutil.Error:
                    pass
            if failure is not None:
                break
            time.sleep(0.05)
    finally:
        # Also remove detached children when the protocol parent exited normally.
        try:
            if profile.runtime_policy.isolation is IsolationKind.OCI:
                try:
                    cleanup = subprocess.run([command[0], "rm", "--force", "synapse-" + runtime.invocation_root.name],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise AgentExecutionError(AgentFailureCode.EXECUTION_STATE_UNKNOWN,
                        "OCI runtime did not confirm container termination") from exc
                if cleanup.returncode and process.poll() is None:
                    raise AgentExecutionError(AgentFailureCode.EXECUTION_STATE_UNKNOWN, "OCI runtime did not confirm container termination")
        finally:
            _terminate_tree(process)
            process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=2)
    if any(thread.is_alive() for thread in threads) or exceeded.is_set():
        failure = failure or AgentFailureCode.RESOURCE_LIMIT
    stdout, stderr = map(bytes, buffers)
    refs.extend((store.retain(stdout), store.retain(stderr)))
    refs.append(store.event(request.invocation_id, {"kind": "PROCESS_FINISHED", "returncode": process.returncode,
        "failure": failure, "peak_observed_rss_bytes": peak_memory if process._synapse_observable else None,
        "observed_cpu_seconds": cpu_seconds if process._synapse_observable else None,
        "stdout_sha256": refs[-2], "stderr_sha256": refs[-1]}))
    return ProcessObservation(process.returncode, stdout, stderr, failure, tuple(sorted(set(refs))))
