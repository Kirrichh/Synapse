"""Administrator-written registry outside workspaces, with locked read custody.

The fixed ProgramData location is resolved by Windows, not an environment field.
Readers cannot initialize/repair it. Every transaction verifies the owner/DACL
of the root, lock and state, holding non-deletable handles. Access decisions and
one bounded owner acceptance/delivery share a lock with grant revocation.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

from .ports import Registry
from .records import AccessRefused
from .registry_codec import decode_registry, encode_registry
from .windows_objects import SecurityAttributes, WindowsObjects
from .windows_location import installation_root


class Overlapped(C.Structure):
    _fields_ = [("internal", C.c_size_t), ("internal_high", C.c_size_t),
                ("offset", W.DWORD), ("offset_high", W.DWORD), ("event", W.HANDLE)]


class _Transaction:
    def __init__(self, owner, registry, administrator):
        self.owner, self.registry, self.administrator = owner, registry, administrator
        self.committed = False
        self.active = True

    def commit(self):
        if not self.active or self.administrator is None or self.committed:
            raise AccessRefused("ADMIN_REQUIRED")
        self.owner._write(encode_registry(self.registry))
        self.committed = True


class WindowsAccessRegistry:
    def __init__(self, root: Path | None = None):
        self.objects = WindowsObjects()
        # Alternate roots are trusted composition input for isolated installations,
        # never command/renderer parameters. The same ACL requirements apply.
        self.root = root if root is not None else installation_root()
        kernel = self.objects.kernel
        kernel.LockFileEx.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, W.DWORD, C.POINTER(Overlapped)]
        kernel.UnlockFileEx.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, C.POINTER(Overlapped)]
        kernel.CreateDirectoryW.argtypes = [W.LPCWSTR, C.POINTER(SecurityAttributes)]
        kernel.ReadFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
        kernel.WriteFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
        kernel.FlushFileBuffers.argtypes = [W.HANDLE]
        kernel.MoveFileExW.argtypes = [W.LPCWSTR, W.LPCWSTR, W.DWORD]

    def enroll(self) -> None:
        self.objects.administrator()
        with self.objects.directory(self.root.parent), self.objects.descriptor() as descriptor:
            attrs = SecurityAttributes(C.sizeof(SecurityAttributes), descriptor, False)
            if not self.objects.kernel.CreateDirectoryW(str(self.root), C.byref(attrs)):
                # Existing state, including a pre-created untrusted directory,
                # is never adopted, overwritten or silently repaired.
                raise AccessRefused("ACCESS_UNAVAILABLE")
            with self.objects.directory(self.root) as (handle, _):
                self.objects.protected(handle)
                with self.objects.open(self.root / "registry.lock", write=True, create=True) as (lock, _):
                    self.objects.protected(lock)
                self._write(encode_registry(Registry()))

    @contextmanager
    def transaction(self, *, administrative: bool):
        administrator = self.objects.administrator() if administrative else None
        with self.objects.directory(self.root) as (directory, _):
            self.objects.protected(directory)
            with self.objects.open(self.root / "registry.lock", write=administrative) as (lock, _):
                self.objects.protected(lock)
                overlap, deadline = Overlapped(), monotonic() + 5
                flags = 1 | (2 if administrative else 0)
                while not self.objects.kernel.LockFileEx(lock, flags, 0, 1, 0, C.byref(overlap)):
                    if C.get_last_error() != 33 or monotonic() >= deadline:
                        raise AccessRefused("ACCESS_UNAVAILABLE")
                    sleep(0.01)
                try:
                    with self.objects.open(self.root / "registry.json") as (state, info):
                        self.objects.protected(state)
                        if info.size_high or not 0 < info.size_low <= 65536:
                            raise AccessRefused("ACCESS_UNAVAILABLE")
                        buffer, read = C.create_string_buffer(info.size_low), W.DWORD()
                        if (not self.objects.kernel.ReadFile(state, buffer, info.size_low, C.byref(read), None)
                                or read.value != info.size_low):
                            raise AccessRefused("ACCESS_UNAVAILABLE")
                        registry = decode_registry(buffer.raw)
                    transaction = _Transaction(self, registry, administrator)
                    try:
                        yield transaction
                    finally:
                        transaction.active = False
                finally:
                    if not self.objects.kernel.UnlockFileEx(lock, 0, 1, 0, C.byref(overlap)):
                        raise AccessRefused("ACCESS_UNAVAILABLE")

    def _write(self, raw: bytes):
        temporary = self.root / (str(uuid4()) + ".pending")
        try:
            with self.objects.open(temporary, write=True, create=True) as (handle, _):
                self.objects.protected(handle)
                written = W.DWORD()
                if (not self.objects.kernel.WriteFile(handle, raw, len(raw), C.byref(written), None)
                        or written.value != len(raw) or not self.objects.kernel.FlushFileBuffers(handle)):
                    raise AccessRefused("ACCESS_UNAVAILABLE")
            if not self.objects.kernel.MoveFileExW(str(temporary), str(self.root / "registry.json"), 9):
                raise AccessRefused("ACCESS_UNAVAILABLE")
        except OSError as exc:
            raise AccessRefused("ACCESS_UNAVAILABLE") from exc
        finally:
            temporary.unlink(missing_ok=True)
