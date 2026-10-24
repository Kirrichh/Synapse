"""Serial x64 acceptance: interrupt real launcher APIs without product hooks.

The debugger owns only the explicit acceptance executable and duplicates its child
identity for observation/cleanup. Breakpoints change API return values in that
private process for fault cases; product files and Windows security settings are
unchanged. Raw diagnostics and host paths are never written to evidence.
"""
from __future__ import annotations

import argparse
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import struct
import subprocess
import time

K = c.WinDLL('kernel32', use_last_error=True)
H, P, D, Q = w.HANDLE, c.c_void_p, w.DWORD, c.c_ulonglong

def api(name, args, result=w.BOOL):
    function = getattr(K, name)
    function.argtypes, function.restype = args, result
    return function

close = api('CloseHandle', [H])
current = api('GetCurrentProcess', [], H)
open_thread = api('OpenThread', [D, w.BOOL, D], H)
get_context = api('GetThreadContext', [H, P])
set_context = api('SetThreadContext', [H, P])
read_memory = api('ReadProcessMemory', [H, P, P, c.c_size_t, P])
write_memory = api('WriteProcessMemory', [H, P, P, c.c_size_t, P])
flush = api('FlushInstructionCache', [H, P, c.c_size_t])
duplicate = api('DuplicateHandle', [H, H, H, c.POINTER(H), D, w.BOOL, D])
wait = api('WaitForSingleObject', [H, D], D)
terminate = api('TerminateProcess', [H, w.UINT])
get_pid = api('GetProcessId', [H], D)
open_job = api('OpenJobObjectW', [D, w.BOOL, w.LPCWSTR], H)
in_job = api('IsProcessInJob', [H, H, c.POINTER(w.BOOL)])
module = api('GetModuleHandleW', [w.LPCWSTR], H)
address = api('GetProcAddress', [H, c.c_char_p], P)
module_from_address = api('GetModuleHandleExW', [D, P, c.POINTER(H)])
module_filename = api('GetModuleFileNameW', [H, w.LPWSTR, D], D)

class Module(c.Structure):
    _fields_ = [('size', D), ('id', D), ('pid', D), ('global_usage', D), ('usage', D),
                ('base', P), ('bytes', D), ('module', H), ('name', w.WCHAR * 256), ('path', w.WCHAR * 260)]

snapshot = api('CreateToolhelp32Snapshot', [D, D], H)
module_first = api('Module32FirstW', [H, c.POINTER(Module)])
module_next = api('Module32NextW', [H, c.POINTER(Module)])

class DebugEvent(c.Structure):
    _fields_ = [('kind', D), ('pid', D), ('tid', D), ('padding', D), ('data', c.c_ubyte * 160)]

wait_event = api('WaitForDebugEvent', [c.POINTER(DebugEvent), D])
continue_event = api('ContinueDebugEvent', [D, D, D])

def checked(success, label):
    if not success:
        raise RuntimeError(f'{label}: Win32 error {c.get_last_error()}')

class Context:
    def __init__(self, thread):
        self.storage = c.create_string_buffer(1232 + 16)
        self.pointer = (c.addressof(self.storage) + 15) & ~15
        c.c_uint32.from_address(self.pointer + 48).value = 0x100003
        checked(get_context(thread, self.pointer), 'GetThreadContext')
    def get(self, offset):
        return Q.from_address(self.pointer + offset).value
    def put(self, offset, value):
        Q.from_address(self.pointer + offset).value = value
    def save(self, thread):
        checked(set_context(thread, self.pointer), 'SetThreadContext')

def remote_modules(pid):
    handle = snapshot(0x8 | 0x10, pid)
    checked(handle and handle != P(-1).value, 'module snapshot')
    entry = Module(size=c.sizeof(Module))
    result = {}
    try:
        valid = module_first(handle, c.byref(entry))
        while valid:
            result[entry.name.lower()] = entry.base
            valid = module_next(handle, c.byref(entry))
    finally:
        close(handle)
    return result

class StartupRun:
    def __init__(self, executable, scenario):
        self.executable, self.scenario = executable, scenario
        self.marker = executable.with_suffix('.executed')
        assert not self.marker.exists(), 'witness marker must not preexist'
        self.process_handle = None
        self.child = None
        self.breakpoints = {}
        self.steps = []
        self.kill_at_boundary = False
        self.events = 0

    def read(self, pointer, length):
        data = c.create_string_buffer(length)
        checked(read_memory(self.process_handle, pointer, data, length, None), 'ReadProcessMemory')
        return data.raw

    def write(self, pointer, data):
        checked(write_memory(self.process_handle, pointer, data, len(data), None), 'WriteProcessMemory')
        checked(flush(self.process_handle, pointer, len(data)), 'FlushInstructionCache')

    def breakpoint(self, pointer, label):
        if pointer not in self.breakpoints:
            self.breakpoints[pointer] = (label, self.read(pointer, 1))
            self.write(pointer, b'\xcc')

    def retain_child(self, remote_handle):
        result = H()
        checked(duplicate(self.process_handle, remote_handle, current(), c.byref(result), 0, False, 2), 'DuplicateHandle')
        self.child = result.value

    def named_job_member(self):
        job = open_job(4, False, 'Local\\Synapse.Desktop.Core.v1')
        if not job:
            return False
        try:
            answer = w.BOOL()
            checked(in_job(self.child, job, c.byref(answer)), 'IsProcessInJob')
            return bool(answer.value)
        finally:
            # Never keep a lifetime handle while testing last-owner death.
            close(job)

    def on_breakpoint(self, event):
        pointer = struct.unpack_from('<Q', bytes(event.data), 16)[0]
        if pointer not in self.breakpoints:
            if self.steps:
                raise RuntimeError('Unexpected breakpoint after initialization')
            self.steps.append('debugger_ready')
            modules = remote_modules(event.pid)
            for name in ['CreateProcessW', 'AssignProcessToJobObject', 'ResumeThread', 'UpdateProcThreadAttribute']:
                local_address = address(module('kernel32.dll'), name.encode())
                checked(local_address, name)
                owner = H()
                checked(module_from_address(6, local_address, c.byref(owner)), 'API owner module')
                filename = c.create_unicode_buffer(32768)
                checked(module_filename(owner, filename, len(filename)), 'API module name')
                remote = modules[Path(filename.value).name.lower()] + local_address - owner.value
                assert self.read(remote, 8) == c.string_at(local_address, 8), 'API image differs'
                self.breakpoint(remote, name)
            return
        label, original = self.breakpoints.pop(pointer)
        self.write(pointer, original)
        thread = open_thread(0x0018, False, event.tid)
        checked(thread, 'OpenThread')
        try:
            context = Context(thread)
            context.put(248, pointer)
            self.steps.append(label)
            if label == 'CreateProcessW':
                flags = struct.unpack('<I', self.read(context.get(152) + 48, 4))[0]
                assert flags & 4, 'the real creation call omitted CREATE_SUSPENDED'
                # x64 argument 10 is PROCESS_INFORMATION*, after return/home area.
                self.info_pointer = struct.unpack('<Q', self.read(context.get(152) + 80, 8))[0]
                self.breakpoint(struct.unpack('<Q', self.read(context.get(152), 8))[0], 'created')
            elif label == 'created':
                if self.scenario == 'invalid_job':
                    assert context.get(120) == 0, 'creation accepted an invalid Job handle'
                    assert self.read(self.info_pointer, 24) == bytes(24), 'failed creation published process handles'
                else:
                    assert context.get(120) != 0, 'CreateProcessW failed before fault boundary'
                    process, _, pid, _ = struct.unpack('<QQII', self.read(self.info_pointer, 24))
                    self.retain_child(process)
                    assert get_pid(self.child) == pid
                    self.child_before_assignment_in_job = self.named_job_member()
                    assert not self.marker.exists(), 'child executed during suspended creation'
                    if self.scenario == 'owner_after_create':
                        self.kill_at_boundary = True
            elif label == 'UpdateProcThreadAttribute':
                attribute = context.get(184)  # R8: attribute selector.
                if attribute == 0x2000d:
                    if self.scenario == 'job_attribute_failure':
                        self.refuse(context, 0)
                    elif self.scenario == 'invalid_job':
                        self.write(context.get(192), struct.pack('<Q', 0xffffffffffffffff))
                else:
                    # There are two updates. Reinsert after this first call returns.
                    self.attribute_api = pointer
                    self.breakpoint(struct.unpack('<Q', self.read(context.get(152), 8))[0], 'attribute_return')
            elif label == 'attribute_return':
                self.breakpoint(self.attribute_api, 'UpdateProcThreadAttribute')
            elif label == 'AssignProcessToJobObject':
                assert not self.marker.exists(), 'child executed before Job assignment'
                if self.scenario == 'assignment_failure':
                    self.refuse(context, 0)
            elif label == 'ResumeThread':
                assert self.named_job_member(), 'resume reached before product Job membership'
                assert not self.marker.exists(), 'child executed before ResumeThread'
                if self.scenario == 'resume_failure':
                    self.refuse(context, 0xffffffff)
                elif self.scenario == 'owner_before_resume':
                    self.kill_at_boundary = True
            context.save(thread)
        finally:
            close(thread)
        if self.kill_at_boundary:
            checked(terminate(self.process_handle, 7), 'terminate owner at boundary')

    def refuse(self, context, code):
        stack = context.get(152)
        context.put(248, struct.unpack('<Q', self.read(stack, 8))[0])
        context.put(152, stack + 8)
        context.put(120, code)

    def run(self):
        process = subprocess.Popen([str(self.executable)], creationflags=2, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 20
        try:
            while time.monotonic() < deadline:
                event = DebugEvent()
                if not wait_event(c.byref(event), 100):
                    if process.stdin is not None and (self.marker.exists() or
                            (self.scenario != 'normal' and self.steps[-1:] == ['ResumeThread'])):
                        process.stdin.close()
                        process.stdin = None
                    continue
                self.events += 1
                status = 0x00010002
                data = bytes(event.data)
                if event.kind == 3:
                    file_handle, self.process_handle, thread = struct.unpack_from('<QQQ', data)
                    if file_handle:
                        close(file_handle)
                elif event.kind == 6:
                    file_handle = struct.unpack_from('<Q', data)[0]
                    if file_handle:
                        close(file_handle)
                elif event.kind == 1:
                    code = struct.unpack_from('<I', data)[0]
                    if code == 0x80000003:
                        self.on_breakpoint(event)
                    else:
                        status = 0x80010001
                checked(continue_event(event.pid, event.tid, status), 'ContinueDebugEvent')
                if event.kind == 5:
                    break
            else:
                raise TimeoutError('startup debugger deadline')
            stdout, _ = process.communicate(timeout=5)
            child_wait = None if self.child is None else wait(self.child, 5000)
            child_exited = child_wait is None or child_wait == 0
            executed = self.marker.exists()
            reply = None if self.kill_at_boundary else json.loads(stdout)
            expected = {'normal': 'STARTED', 'assignment_failure': 'CONTAINMENT_FAILED', 'resume_failure': 'SPAWN_FAILED',
                        'job_attribute_failure': 'CONTAINMENT_FAILED', 'invalid_job': 'SPAWN_FAILED'}
            passed = child_exited and (executed if self.scenario == 'normal' else not executed)
            if self.scenario in expected:
                passed = passed and reply['result'] == expected[self.scenario]
            return {'scenario': self.scenario, 'result': 'PASS' if passed else 'FAIL',
                    'child_exited': child_exited, 'child_executed': executed,
                    'child_wait': child_wait, 'owner_exit': process.returncode,
                    'member_at_create_return': getattr(self, 'child_before_assignment_in_job', None),
                    'observed_steps': self.steps, 'reply': None if reply is None else reply['result']}
        finally:
            if process.poll() is None:
                process.kill()
                # Drain the final debug event so process termination can complete.
                for _ in range(30):
                    event = DebugEvent()
                    if wait_event(c.byref(event), 100):
                        continue_event(event.pid, event.tid, 0x00010002)
                        if event.kind == 5:
                            break
            if self.child:
                terminate(self.child, 8)
                wait(self.child, 5000)
                close(self.child)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    stream.close()
            # Debug-event process/thread handles close on their exit events.
            # Closing them early can let Windows close a reused observer handle.
            self.marker.unlink(missing_ok=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--scenario', choices=['normal', 'assignment_failure', 'job_attribute_failure', 'invalid_job', 'resume_failure', 'owner_after_create', 'owner_before_resume'], action='append')
    args = parser.parse_args()
    assert c.sizeof(P) == 8, 'x64 Python required'
    records = []
    for scenario in args.scenario or ['normal', 'job_attribute_failure', 'invalid_job', 'resume_failure', 'owner_after_create', 'owner_before_resume']:
        try:
            record = StartupRun(args.executable.resolve(), scenario).run()
        except AssertionError as error:
            record = {'scenario': scenario, 'result': 'FAIL', 'assertion': str(error)}
            print(scenario, str(error), flush=True)
        except Exception as error:
            record = {'scenario': scenario, 'result': 'INFRASTRUCTURE_FAILURE', 'error_kind': type(error).__name__}
            print(scenario, str(error), flush=True)
        records.append(record)
        args.output.write_text(json.dumps({'schema': 1, 'method': 'external Win32 debugger; private acceptance process only', 'records': records}, indent=2) + '\n', encoding='utf-8')
        print(scenario, record['result'], flush=True)
    return 0 if all(record['result'] == 'PASS' for record in records) else 1

if __name__ == '__main__':
    raise SystemExit(main())
