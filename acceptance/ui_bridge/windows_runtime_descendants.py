"""Heavy, serial: actual run_process and MCP stdio owners under the native Job.

Requires the declared agent-mcp extra. Uses synthetic local servers/tasks and
scratch outside source. Retains process handles before killing the private owner;
cleanup after failed assertions is not counted as containment success.
"""
from __future__ import annotations
import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
from queue import Queue
import subprocess
import sys
import tempfile
from threading import Thread
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

def wait_marker(path):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except json.JSONDecodeError:
                pass
        time.sleep(.02)
    raise TimeoutError('runtime did not publish its synthetic witness')

def server(role, scratch):
    grandchild = subprocess.Popen([sys.executable, '-B', __file__, '--role', 'grandchild'],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (scratch / f'{role}.json').write_text(json.dumps([os.getpid(), grandchild.pid]))
    if role == 'adapter':
        while True:
            time.sleep(1)
    # Synthetic MCP peer; the product uses the real SDK client/stdio transport.
    for line in sys.stdin:
        request = json.loads(line)
        if 'id' not in request:
            continue
        if request['method'] == 'initialize':
            result = {'protocolVersion': request['params']['protocolVersion'], 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'containment-witness', 'version': '1'}}
        elif request['method'] == 'tools/list':
            result = {'tools': []}
        else:
            result = {}
        print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)

def owners(scratch):
    from synapse.agents.contracts import AgentExecutionRequest, AgentProfile, AgentRuntimeContext, AgentTransportKind, LocalInformationPolicy
    from synapse.agents.policy import ResourceBudget, RuntimePolicy, IsolationKind
    from synapse.agents.runtime import run_process
    from synapse.memory_consolidation.tools.contracts import ToolConfiguration, ToolServer
    from synapse.memory_consolidation.tools.mcp_transport import McpToolTransport
    task = 'Synthetic containment witness'
    budget = ResourceBudget(timeout_seconds=120)
    request = AgentExecutionRequest('witness-invocation', 'witness-attempt', 'witness-context', task,
        hashlib.sha256(task.encode()).hexdigest(), len(task), '0' * 64,
        ('witness',), 'witness/v1', (), (), LocalInformationPolicy.NOT_SUPPORTED, resource_budget=budget)
    profile = AgentProfile('witness-profile', 'witness-agent', '1', 'witness-adapter', '1', AgentTransportKind.STDIO,
        ('witness',), (), ('witness/v1',), LocalInformationPolicy.NOT_SUPPORTED, (), 'synthetic',
        runtime_policy=RuntimePolicy(isolation=IsolationKind.TRUSTED_PROCESS))
    invocation = scratch / 'invocation'
    invocation.mkdir()
    runtime = AgentRuntimeContext(scratch, scratch / 'evidence', invocation)
    def adapter():
        run_process(request=request, runtime=runtime, profile=profile,
                    argv=(sys.executable, '-B', __file__, '--role', 'adapter', '--scratch', str(scratch)),
                    environment=(('SystemRoot', os.environ['SystemRoot']),))
    Thread(target=adapter, daemon=True).start()
    adapter_pids = wait_marker(scratch / 'adapter.json')
    transport = McpToolTransport(ToolConfiguration(
        {'witness': ToolServer('witness', (sys.executable, '-B', __file__, '--role', 'mcp', '--scratch', str(scratch)),
                               (('SystemRoot', os.environ['SystemRoot']),))}, {}, {}, {}))
    transport._server_queue('witness')
    mcp_pids = wait_marker(scratch / 'mcp.json')
    print(json.dumps({'python_owner': os.getpid(), 'adapter': adapter_pids, 'mcp': mcp_pids}), flush=True)
    while True:
        time.sleep(1)

def check(executable, workspace, mode):
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [w.DWORD, w.BOOL, w.DWORD], w.HANDLE
    kernel.OpenJobObjectW.argtypes, kernel.OpenJobObjectW.restype = [w.DWORD, w.BOOL, w.LPCWSTR], w.HANDLE
    kernel.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)]
    kernel.WaitForSingleObject.argtypes, kernel.WaitForSingleObject.restype = [w.HANDLE, w.DWORD], w.DWORD
    kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handles = []
    with tempfile.TemporaryDirectory(prefix='runtime-witness-', dir=workspace) as temporary:
        process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            configuration = {'python': sys.executable, 'script': str(Path(__file__).resolve()), 'scratch': temporary}
            process.stdin.write(json.dumps(configuration).encode() + b'\n')
            process.stdin.flush()
            reports = Queue()
            Thread(target=lambda: reports.put(process.stdout.readline()), daemon=True).start()
            raw = reports.get(timeout=45)
            assert raw, 'native runtime witness exited before reporting'
            report = json.loads(raw)
            pids = [report['native_child'], report['python_owner'], *report['adapter'], *report['mcp']]
            job = kernel.OpenJobObjectW(4, False, 'Local\\Synapse.Desktop.Core.v1')
            assert job, 'product Job not found'
            try:
                for pid in pids:
                    handle = kernel.OpenProcess(0x100000 | 0x1000 | 1, False, pid)
                    assert handle, 'could not retain actual runtime process'
                    handles.append(handle)
                    member = w.BOOL()
                    assert kernel.IsProcessInJob(handle, job, c.byref(member)) and member.value
            finally:
                kernel.CloseHandle(job)
            if mode == 'abrupt':
                process.kill()
            else:
                process.stdin.close()
            process.wait(timeout=5)
            deadline = time.monotonic() + 5
            exited = [kernel.WaitForSingleObject(handle, max(0, int((deadline - time.monotonic()) * 1000))) == 0
                      for handle in handles]
            assert all(exited), 'actual adapter/MCP process or nested child survived owner loss'
            return {'scenario': mode, 'result': 'PASS', 'agent_supervisor': 'real run_process',
                    'mcp_transport': 'real McpToolTransport and mcp 2.2.0 stdio client',
                    'all_in_product_job': True, 'all_exited': True}
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for handle in handles:
                kernel.TerminateProcess(handle, 9)
                kernel.WaitForSingleObject(handle, 5000)
                kernel.CloseHandle(handle)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    stream.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=['owners', 'adapter', 'mcp', 'grandchild', 'check'], default='check')
    parser.add_argument('--scratch', type=Path)
    parser.add_argument('--executable', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.role == 'grandchild':
        while True:
            time.sleep(1)
    if args.role in ('adapter', 'mcp'):
        return server(args.role, args.scratch)
    if args.role == 'owners':
        return owners(args.scratch)
    assert not args.scratch.resolve().is_relative_to(ROOT), 'private runtime stores must remain outside source'
    records = []
    for mode in ('orderly', 'abrupt'):
        try:
            record = check(args.executable.resolve(), args.scratch, mode)
        except AssertionError as error:
            record = {'scenario': mode, 'result': 'FAIL', 'error_kind': type(error).__name__}
            print(mode, str(error), flush=True)
        except Exception as error:
            record = {'scenario': mode, 'result': 'INFRASTRUCTURE_FAILURE', 'error_kind': type(error).__name__}
            print(mode, str(error), flush=True)
        records.append(record)
        args.output.write_text(json.dumps({'schema': 1, 'records': records}, indent=2) + '\n')
        print(mode, record['result'], flush=True)
    return 0 if all(record['result'] == 'PASS' for record in records) else 1

if __name__ == '__main__':
    raise SystemExit(main())
