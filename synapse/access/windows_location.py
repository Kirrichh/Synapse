"""Resolve the machine-wide registry location without process environment input.

The Shell/userenv path helpers expand the caller's SystemDrive, including in a
minimal native child environment. Read the machine's configured ProgramData
value instead and expand only the system drive obtained from the Windows API.
Unknown expansions fail closed; neither a workspace nor a caller selects a root.
"""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import re

from .records import AccessRefused


def installation_root() -> Path:
    import winreg

    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.GetSystemWindowsDirectoryW.argtypes = [W.LPWSTR, W.UINT]
    kernel.GetSystemWindowsDirectoryW.restype = W.UINT
    buffer = C.create_unicode_buffer(32768)
    length = kernel.GetSystemWindowsDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise AccessRefused("ACCESS_UNAVAILABLE")
    drive = Path(buffer.value).drive
    if re.fullmatch(r"[A-Za-z]:", drive) is None:
        raise AccessRefused("ACCESS_UNAVAILABLE")
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList", 0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            configured, kind = winreg.QueryValueEx(key, "ProgramData")
    except OSError as error:
        raise AccessRefused("ACCESS_UNAVAILABLE") from error
    if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or type(configured) is not str:
        raise AccessRefused("ACCESS_UNAVAILABLE")
    if kind == winreg.REG_EXPAND_SZ:
        configured = re.sub(r"%SystemDrive%", lambda _: drive, configured, flags=re.IGNORECASE)
    root = Path(configured)
    if (not root.is_absolute() or root.drive.startswith("\\\\") or "%" in configured
            or "\0" in configured or ".." in root.parts):
        raise AccessRefused("ACCESS_UNAVAILABLE")
    return root / "SynapseAccessV1"
