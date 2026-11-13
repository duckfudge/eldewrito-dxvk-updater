"""Verify the descriptor Windows stores, including generic-rights mapping."""
import ctypes
import os
from ctypes import wintypes as w

import pytest
from unittest.mock import Mock

from dxvk_updater import windows
from dxvk_updater.model import UpdaterError


@pytest.mark.skipif(os.name != "nt", reason="Native Windows directory security")
def test_restricted_directory_descriptor_survives_windows_mapping(tmp_path, monkeypatch):
    kernel, advapi, _ = windows.libraries()
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    advapi.GetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    length = w.DWORD()
    advapi.GetFileSecurityW(str(tmp_path), 1, None, 0, ctypes.byref(length))
    owner = ctypes.create_string_buffer(length.value)
    assert advapi.GetFileSecurityW(str(tmp_path), 1, owner, length, ctypes.byref(length))
    advapi.GetSecurityDescriptorOwner.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.BOOL)]
    sid, defaulted, sid_text = ctypes.c_void_p(), w.BOOL(), w.LPWSTR()
    assert advapi.GetSecurityDescriptorOwner(owner, ctypes.byref(sid), ctypes.byref(defaulted))
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.LPWSTR)]
    assert advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text))
    # Keep the real cache ACL, but use the test user's owner so CI needs no elevation.
    text = windows.ADMIN_CACHE_SDDL.replace("O:BAG:BA", "O:" + sid_text.value)
    kernel.LocalFree(sid_text)
    expected = windows.descriptor(advapi, text)
    path = tmp_path / "restricted"
    try:
        attributes = windows.SecurityAttributes(ctypes.sizeof(windows.SecurityAttributes), expected, False)
        kernel.CreateDirectoryW.argtypes = [w.LPCWSTR, ctypes.POINTER(windows.SecurityAttributes)]
        assert kernel.CreateDirectoryW(str(path), ctypes.byref(attributes))
        windows.verify_admin_directory(path, expected, advapi)
        from dxvk_updater import program_update
        import time
        monkeypatch.setattr(program_update, "read_inventory", lambda root: {"files": []})
        started = time.monotonic()
        assert program_update.writable(path) is windows.elevated()
        assert time.monotonic() - started < 5
        unsafe = windows.descriptor(advapi, text.replace("FRFX;;;BU", "FA;;;BU"))
        try:
            with pytest.raises(UpdaterError, match="unsafe permissions"):
                windows.verify_admin_directory(path, unsafe, advapi)
        finally:
            kernel.LocalFree(unsafe)
    finally:
        kernel.LocalFree(expected)
        if path.exists():
            path.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows cancellation boundary")
def test_cancelled_windows_approval_is_reported_without_waiting(monkeypatch, tmp_path):
    from types import SimpleNamespace
    kernel = SimpleNamespace(WaitForSingleObject=Mock(), GetExitCodeProcess=Mock(), CloseHandle=Mock())
    def cancelled(info):
        ctypes.set_last_error(1223)
        return False
    shell = SimpleNamespace(ShellExecuteExW=Mock(side_effect=cancelled))
    monkeypatch.setattr(windows, "libraries", lambda: (kernel, None, shell))
    with pytest.raises(UpdaterError, match="approval was cancelled"):
        windows.run_elevated(tmp_path / "updater.exe", [])
    kernel.WaitForSingleObject.assert_not_called()
