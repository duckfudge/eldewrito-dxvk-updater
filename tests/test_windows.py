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


@pytest.mark.skipif(os.name != "nt", reason="Windows elevation boundary")
@pytest.mark.parametrize("outcome,expected", [
    ("success", None),
    ("timeout", "has not finished"),
    ("wait_failed", "has not finished"),
    ("child_error", "preparation failed"),
    ("exit_query_failed", "preparation failed"),
    ("launch_denied", 5),
])
def test_elevation_failure_paths_release_only_owned_handles(monkeypatch, tmp_path, outcome, expected):
    from types import SimpleNamespace
    kernel = SimpleNamespace(WaitForSingleObject=Mock(return_value=0),
                             GetExitCodeProcess=Mock(), CloseHandle=Mock())
    def launch(info):
        if outcome == "launch_denied":
            ctypes.set_last_error(5)
            return False
        info._obj.process = 123
        return True
    def exit_code(handle, code):
        code._obj.value = 1 if outcome == "child_error" else 0
        return outcome != "exit_query_failed"
    kernel.GetExitCodeProcess.side_effect = exit_code
    if outcome in {"timeout", "wait_failed"}:
        kernel.WaitForSingleObject.return_value = 258 if outcome == "timeout" else 0xFFFFFFFF
    shell = SimpleNamespace(ShellExecuteExW=Mock(side_effect=launch))
    ole = SimpleNamespace(CoInitializeEx=Mock(return_value=0), CoUninitialize=Mock())
    monkeypatch.setattr(windows, "libraries", lambda: (kernel, None, shell))
    monkeypatch.setattr(ctypes, "WinDLL", lambda name: ole)
    if expected == 5:
        with pytest.raises(OSError) as error:
            windows.run_elevated(tmp_path / "updater.exe", [])
        assert error.value.winerror == 5
    elif expected:
        with pytest.raises(UpdaterError, match=expected):
            windows.run_elevated(tmp_path / "updater.exe", [])
    else:
        windows.run_elevated(tmp_path / "updater.exe", [])
    if outcome == "launch_denied":
        kernel.CloseHandle.assert_not_called()
        kernel.WaitForSingleObject.assert_not_called()
    else:
        kernel.CloseHandle.assert_called_once_with(123)
        kernel.WaitForSingleObject.assert_called_once_with(123, 300_000)
    ole.CoUninitialize.assert_called_once()


@pytest.mark.skipif(os.name != "nt", reason="Windows COM ownership boundary")
@pytest.mark.parametrize("initialized,uninitialize", [(1, True), (-2147417850, False)])
def test_uac_does_not_uninitialize_someone_elses_com_apartment(monkeypatch, tmp_path, initialized, uninitialize):
    from types import SimpleNamespace
    kernel = SimpleNamespace(WaitForSingleObject=Mock(), GetExitCodeProcess=Mock(), CloseHandle=Mock())
    def cancelled(info):
        ctypes.set_last_error(1223)
        return False
    shell = SimpleNamespace(ShellExecuteExW=Mock(side_effect=cancelled))
    ole = SimpleNamespace(CoInitializeEx=Mock(return_value=initialized), CoUninitialize=Mock())
    monkeypatch.setattr(windows, "libraries", lambda: (kernel, None, shell))
    monkeypatch.setattr(ctypes, "WinDLL", lambda name: ole)
    with pytest.raises(UpdaterError, match="approval was cancelled"):
        windows.run_elevated(tmp_path / "updater.exe", [])
    assert ole.CoUninitialize.called is uninitialize
