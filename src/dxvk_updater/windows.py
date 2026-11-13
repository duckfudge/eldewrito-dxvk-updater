"""Small Windows-only boundaries for UAC, protected staging, and startup acknowledgement."""
from __future__ import annotations

import ctypes
import subprocess
from ctypes import wintypes as w
from pathlib import Path

from .model import UpdaterError
from .storage import safe_regular


def libraries():
    return (ctypes.WinDLL("kernel32", use_last_error=True),
            ctypes.WinDLL("advapi32", use_last_error=True),
            ctypes.WinDLL("shell32", use_last_error=True))


class SecurityAttributes(ctypes.Structure):
    _fields_ = [("length", w.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", w.BOOL)]


def descriptor(advapi, text):
    function = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    function.argtypes = [w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.DWORD)]
    function.restype = w.BOOL
    pointer = ctypes.c_void_p()
    if not function(text, 1, ctypes.byref(pointer), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return pointer


def admin_cache(*, create=False):
    kernel, advapi, shell = libraries()
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    shell.SHGetFolderPathW.argtypes = [w.HWND, ctypes.c_int, w.HANDLE, w.DWORD, w.LPWSTR]
    buffer = ctypes.create_unicode_buffer(32768)
    if shell.SHGetFolderPathW(None, 0x23, None, 0, buffer) != 0:
        raise UpdaterError("Could not locate Windows ProgramData.")
    root = Path(buffer.value)
    # Owner Rights removes the owner's implicit WRITE_DAC on inherited files.
    text = "O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;GRGX;;;BU)(A;OICI;RC;;;OW)"
    sd = descriptor(advapi, text)
    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), sd, False)
        kernel.CreateDirectoryW.argtypes = [w.LPCWSTR, ctypes.POINTER(SecurityAttributes)]
        kernel.CreateDirectoryW.restype = w.BOOL
        for part in ("ElDewritoDXVKUpdater", "program-updates"):
            root /= part
            safe_regular(root, directory=True)
            if not root.exists():
                if not create:
                    return root / "program-updates" if part == "ElDewritoDXVKUpdater" else root
                if not kernel.CreateDirectoryW(str(root), ctypes.byref(attributes)):
                    raise ctypes.WinError(ctypes.get_last_error())
            verify_admin_directory(root, sd, advapi)
        return root
    finally:
        kernel.LocalFree(sd)


def verify_admin_directory(path, expected, advapi):
    get = advapi.GetFileSecurityW
    get.argtypes = [w.LPCWSTR, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    get.restype = w.BOOL
    length = w.DWORD()
    get(str(path), 5, None, 0, ctypes.byref(length))  # OWNER | DACL
    if not 0 < length.value < 65536:
        raise UpdaterError("The administrator update cache has unsafe permissions.")
    actual = ctypes.create_string_buffer(length.value)
    if not get(str(path), 5, actual, length, ctypes.byref(length)):
        raise ctypes.WinError(ctypes.get_last_error())
    def access(sd):
        present, defaulted, acl, owner = w.BOOL(), w.BOOL(), ctypes.c_void_p(), ctypes.c_void_p()
        advapi.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.BOOL)]
        advapi.GetSecurityDescriptorOwner.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.BOOL)]
        if not advapi.GetSecurityDescriptorDacl(sd, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)) or not present.value or not acl.value:
            raise UpdaterError("The administrator update cache has no restricted access list.")
        if not advapi.GetSecurityDescriptorOwner(sd, ctypes.byref(owner), ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        size = ctypes.c_ushort.from_address(acl.value + 2).value
        return ctypes.string_at(acl, size), owner
    actual_acl, actual_owner = access(actual)
    expected_acl, expected_owner = access(expected)
    advapi.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    if actual_acl != expected_acl or not advapi.EqualSid(actual_owner, expected_owner):
        raise UpdaterError("The administrator update cache has unsafe permissions. Remove it as administrator and retry.")


def elevated():
    return bool(libraries()[2].IsUserAnAdmin())


def run_elevated(executable, arguments):
    kernel, _, shell = libraries()
    class ShellInfo(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("mask", w.ULONG), ("window", w.HWND),
                    ("verb", w.LPCWSTR), ("file", w.LPCWSTR), ("parameters", w.LPCWSTR),
                    ("directory", w.LPCWSTR), ("show", ctypes.c_int), ("instance", w.HINSTANCE),
                    ("idlist", ctypes.c_void_p), ("class_name", w.LPCWSTR), ("key", w.HKEY),
                    ("hotkey", w.DWORD), ("icon", w.HANDLE), ("process", w.HANDLE)]
    info = ShellInfo()
    info.size, info.mask, info.verb = ctypes.sizeof(info), 0x40 | 0x100, "runas"
    info.file, info.parameters, info.directory = str(executable), subprocess.list2cmdline(arguments), str(executable.parent)
    info.show = 0
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellInfo)]
    shell.ShellExecuteExW.restype = w.BOOL
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    ole = ctypes.WinDLL("ole32")
    initialized = ole.CoInitializeEx(None, 2) in (0, 1)
    try:
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            if ctypes.get_last_error() == 1223:
                raise UpdaterError("Administrator approval was cancelled. The program was not replaced.")
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if kernel.WaitForSingleObject(info.process, 300_000) != 0:
                raise UpdaterError("Administrator update preparation has not finished. Keep the app open and try again later.")
            code = w.DWORD()
            if not kernel.GetExitCodeProcess(info.process, ctypes.byref(code)) or code.value:
                raise UpdaterError("Administrator update preparation failed. Check the protected program-update log in ProgramData.")
        finally:
            kernel.CloseHandle(info.process)
    finally:
        if initialized:
            ole.CoUninitialize()


def health_event(job_id, *, signal=False):
    from .storage import ID_PATTERN
    if not ID_PATTERN.fullmatch(job_id):
        raise UpdaterError("Invalid startup acknowledgement.")
    kernel, advapi, _ = libraries()
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.OpenEventW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
    kernel.OpenEventW.restype = w.HANDLE
    name = "Local\\ElDewritoDXVKUpdater-" + job_id
    if signal:
        handle = kernel.OpenEventW(2, False, name)
        if not handle:
            raise UpdaterError(f"The program update helper is no longer waiting for startup (Windows error {ctypes.get_last_error()}).")
        try:
            kernel.SetEvent.argtypes = [w.HANDLE]
            if not kernel.SetEvent(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(handle)
        return
    sd = descriptor(advapi, "D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;0x100002;;;BU)(A;;0x100002;;;OW)")
    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), sd, False)
        kernel.CreateEventW.argtypes = [ctypes.POINTER(SecurityAttributes), w.BOOL, w.BOOL, w.LPCWSTR]
        kernel.CreateEventW.restype = w.HANDLE
        handle = kernel.CreateEventW(ctypes.byref(attributes), True, False, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        kernel.ResetEvent.argtypes = [w.HANDLE]
        kernel.ResetEvent(handle)
        return handle
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(sd)


def wait_health(handle, timeout=60):
    kernel = libraries()[0]
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    return kernel.WaitForSingleObject(handle, timeout * 1000) == 0


def close_handle(handle):
    kernel = libraries()[0]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle(handle)
