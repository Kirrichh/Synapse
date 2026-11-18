"""Inject bounded synthetic bytes into an owned installed core's actual stdout.

Windows x64 acceptance only. Read the child's native process parameters, duplicate
its existing pipe handle, and write a short frame. No remote code, DLL injection,
file modification, product hook, or unrelated process access is used.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import struct

import psutil

FAULTS = {
    'zero': bytes(4), 'oversize': struct.pack('>I', 65537), 'maximum': struct.pack('>I', 0xffffffff),
    'utf8': b'\0\0\0\1\xff', 'json': b'\0\0\0\1{',
    'header_eof': b'\0\0', 'body_eof': b'\0\0\0\x0a{',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--owner', type=int, required=True)
    parser.add_argument('--core', type=Path, required=True)
    parser.add_argument('--fault', choices=FAULTS, required=True)
    args = parser.parse_args()
    assert c.sizeof(c.c_void_p) == 8, 'the observation layout is Windows x64 only'
    core = psutil.Process(args.pid)
    assert core.ppid() == args.owner and Path(core.exe()).resolve() == args.core.resolve()
    assert core.cmdline()[1:] == ['-I', '-B', '-m', 'synapse', 'machine']
    kernel = c.WinDLL('kernel32', use_last_error=True)
    ntdll = c.WinDLL('ntdll')
    def api(library, name, arguments, result):
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, result
        return function
    open_process = api(kernel, 'OpenProcess', [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)
    close = api(kernel, 'CloseHandle', [w.HANDLE], w.BOOL)
    read = api(kernel, 'ReadProcessMemory', [w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.c_void_p], w.BOOL)
    query = api(ntdll, 'NtQueryInformationProcess', [w.HANDLE, c.c_ulong, c.c_void_p, c.c_ulong, c.c_void_p], c.c_long)
    duplicate = api(kernel, 'DuplicateHandle', [w.HANDLE, w.HANDLE, w.HANDLE, c.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD], w.BOOL)
    current = api(kernel, 'GetCurrentProcess', [], w.HANDLE)
    file_type = api(kernel, 'GetFileType', [w.HANDLE], w.DWORD)
    write = api(kernel, 'WriteFile', [w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p], w.BOOL)
    process = open_process(0x0400 | 0x0010 | 0x0040, False, args.pid)
    assert process, 'owned core query access'
    pipe = w.HANDLE()
    try:
        basic = c.create_string_buffer(48)
        assert query(process, 0, basic, len(basic), None) == 0
        assert struct.unpack_from('<Q', basic.raw, 32)[0] == args.pid
        def pointer(address):
            value = c.c_ulonglong()
            count = c.c_size_t()
            assert read(process, address, c.byref(value), 8, c.byref(count)) and count.value == 8
            return value.value
        # Windows x64 PEB.ProcessParameters and RTL_USER_PROCESS_PARAMETERS.StdOutput.
        # Validate the resulting object is a pipe and require the caller's UI
        # observer to distinguish native framing failure from a core-side refusal.
        parameters = pointer(struct.unpack_from('<Q', basic.raw, 8)[0] + 0x20)
        stdout = pointer(parameters + 0x28)
        assert duplicate(process, stdout, current(), c.byref(pipe), 0, False, 2)
        assert file_type(pipe) == 3, 'owned stdout must be a pipe'
        payload = FAULTS[args.fault]
        written = w.DWORD()
        assert write(pipe, payload, len(payload), c.byref(written), None) and written.value == len(payload)
        if args.fault.endswith('_eof'):
            core.kill()
            core.wait(timeout=10)
    finally:
        if pipe.value:
            close(pipe)
        close(process)
    print(json.dumps({'fault': args.fault, 'status': 'INJECTED', 'verified_owned_pipe': True}))


if __name__ == '__main__':
    main()
