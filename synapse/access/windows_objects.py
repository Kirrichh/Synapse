"""Windows handle custody and administrator-only security descriptors.

No domain rights are assigned here. Handles deny deletion while in custody;
reparse points are refused rather than followed. The access registry adapter
uses a protected DACL, with an Administrators owner, on each retained object.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path

from .records import AccessRefused, ResourceIdentity

SDDL = "O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;BU)"


class FileInformation(C.Structure):
    _fields_ = [("attributes", W.DWORD), ("creation", W.FILETIME), ("access", W.FILETIME),
                ("write", W.FILETIME), ("volume", W.DWORD), ("size_high", W.DWORD),
                ("size_low", W.DWORD), ("links", W.DWORD), ("index_high", W.DWORD),
                ("index_low", W.DWORD)]


class SecurityAttributes(C.Structure):
    _fields_ = [("length", W.DWORD), ("descriptor", C.c_void_p), ("inherit", W.BOOL)]


class WindowsObjects:
    def __init__(self):
        if os.name != "nt":
            raise AccessRefused("ACCESS_UNAVAILABLE")
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.security = C.WinDLL("advapi32", use_last_error=True)
        self.kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p,
                                           W.DWORD, W.DWORD, W.HANDLE]
        self.kernel.CreateFileW.restype = W.HANDLE
        self.kernel.CloseHandle.argtypes = [W.HANDLE]
        self.kernel.GetFileInformationByHandle.argtypes = [W.HANDLE, C.POINTER(FileInformation)]
        self.kernel.LocalFree.argtypes = [C.c_void_p]
        self.kernel.GetCurrentProcess.restype = W.HANDLE
        self.security.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
        self.security.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
        self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [W.LPCWSTR, W.DWORD, C.POINTER(C.c_void_p), C.c_void_p]
        self.security.GetSecurityInfo.argtypes = [W.HANDLE, C.c_int, W.DWORD, C.POINTER(C.c_void_p),
            C.c_void_p, C.POINTER(C.c_void_p), C.c_void_p, C.POINTER(C.c_void_p)]
        self.security.GetSecurityDescriptorOwner.argtypes = [C.c_void_p, C.POINTER(C.c_void_p), C.POINTER(W.BOOL)]
        self.security.GetSecurityDescriptorDacl.argtypes = [C.c_void_p, C.POINTER(W.BOOL), C.POINTER(C.c_void_p), C.POINTER(W.BOOL)]
        self.security.GetSecurityDescriptorControl.argtypes = [C.c_void_p, C.POINTER(W.WORD), C.POINTER(W.DWORD)]
        self.security.EqualSid.argtypes = [C.c_void_p, C.c_void_p]
        self.security.ConvertSidToStringSidW.argtypes = [C.c_void_p, C.POINTER(C.c_void_p)]

    @contextmanager
    def descriptor(self):
        pointer = C.c_void_p()
        if not self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(SDDL, 1, C.byref(pointer), None):
            raise AccessRefused("ACCESS_UNAVAILABLE")
        try:
            yield pointer
        finally:
            self.kernel.LocalFree(pointer)

    def administrator(self) -> str:
        token = W.HANDLE()
        if not self.security.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, C.byref(token)):
            raise AccessRefused("ADMIN_REQUIRED")
        try:
            elevated, size = W.DWORD(), W.DWORD()
            if (not self.security.GetTokenInformation(token, 20, C.byref(elevated), C.sizeof(elevated), C.byref(size))
                    or not elevated.value):
                raise AccessRefused("ADMIN_REQUIRED")
            self.security.GetTokenInformation(token, 1, None, 0, C.byref(size))
            if not 0 < size.value <= 65536:
                raise AccessRefused("ADMIN_REQUIRED")
            user = C.create_string_buffer(size.value)
            if not self.security.GetTokenInformation(token, 1, user, size.value, C.byref(size)):
                raise AccessRefused("ADMIN_REQUIRED")
            sid, text = C.c_void_p.from_buffer(user), C.c_void_p()
            if not self.security.ConvertSidToStringSidW(sid, C.byref(text)):
                raise AccessRefused("ADMIN_REQUIRED")
            try:
                return "sid:" + C.wstring_at(text)
            finally:
                self.kernel.LocalFree(text)
        finally:
            self.kernel.CloseHandle(token)

    @contextmanager
    def open(self, path: Path, *, write=False, create=False):
        access = 0x80000000 | 0x20000 | (0x40000000 if write else 0)
        with self.descriptor() as descriptor:
            attrs = SecurityAttributes(C.sizeof(SecurityAttributes), descriptor, False)
            handle = self.kernel.CreateFileW(str(path), access, 3, C.byref(attrs) if create else None,
                                             1 if create else 3, 0x02200000, None)
        if handle == C.c_void_p(-1).value:
            raise AccessRefused("ACCESS_UNAVAILABLE")
        try:
            info = FileInformation()
            if not self.kernel.GetFileInformationByHandle(handle, C.byref(info)) or info.attributes & 0x400:
                raise AccessRefused("RESOURCE_CHANGED")
            yield handle, info
        finally:
            self.kernel.CloseHandle(handle)

    def protected(self, handle) -> None:
        owner, dacl, actual = C.c_void_p(), C.c_void_p(), C.c_void_p()
        if self.security.GetSecurityInfo(handle, 1, 5, C.byref(owner), None, C.byref(dacl), None, C.byref(actual)):
            raise AccessRefused("ACCESS_UNAVAILABLE")
        try:
            with self.descriptor() as expected:
                expected_owner, expected_acl = C.c_void_p(), C.c_void_p()
                present, defaulted = W.BOOL(), W.BOOL()
                control, revision = W.WORD(), W.DWORD()
                ok = (self.security.GetSecurityDescriptorOwner(expected, C.byref(expected_owner), C.byref(defaulted))
                      and self.security.GetSecurityDescriptorDacl(expected, C.byref(present), C.byref(expected_acl), C.byref(defaulted))
                      and self.security.GetSecurityDescriptorControl(actual, C.byref(control), C.byref(revision)))
                if not ok or not dacl.value or not self.security.EqualSid(owner, expected_owner) or not control.value & 0x1000:
                    raise AccessRefused("ACCESS_UNAVAILABLE")
                # ACL size is the WORD at byte offset two. Exact comparison
                # refuses inherited/extra write ACEs and null DACLs.
                size = C.c_ushort.from_address(expected_acl.value + 2).value
                actual_size = C.c_ushort.from_address(dacl.value + 2).value
                if actual_size != size or C.string_at(dacl, size) != C.string_at(expected_acl, size):
                    raise AccessRefused("ACCESS_UNAVAILABLE")
        finally:
            self.kernel.LocalFree(actual)

    @contextmanager
    def directory(self, path: Path):
        path = path.absolute()
        if path.drive.startswith("\\\\") or ".." in path.parts:
            raise AccessRefused("RESOURCE_CHANGED")
        with ExitStack() as stack:
            for parent in (*reversed(path.parents), path):
                handle, info = stack.enter_context(self.open(parent))
                if not info.attributes & 0x10:
                    raise AccessRefused("RESOURCE_CHANGED")
            yield handle, info

    def identify(self, path: Path) -> ResourceIdentity:
        with self.directory(path) as (_, info):
            return ResourceIdentity(str(path.absolute()), str(info.volume),
                                    f"{info.index_high:08x}{info.index_low:08x}")

    @contextmanager
    def hold(self, resource: ResourceIdentity):
        with self.directory(Path(resource.path)) as (_, info):
            if (str(info.volume), f"{info.index_high:08x}{info.index_low:08x}") != (resource.volume, resource.file_id):
                raise AccessRefused("RESOURCE_CHANGED")
            yield
