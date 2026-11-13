"""Preparation must not replace files until handoff, and saved helpers remain usable offline."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from dxvk_updater import program_update as module, windows
from dxvk_updater.bundle import EXE
from dxvk_updater.model import UpdaterError
from test_program_update import files, staged


def protected_copy(staged, monkeypatch):
    job, target, _ = staged
    cache = job.parent.parent / "user-cache"
    cache.mkdir()
    monkeypatch.setattr(module, "user_cache", lambda: cache)
    monkeypatch.setattr(windows, "admin_cache", lambda **kw: job.parent)
    copy = cache / ("b" * 32)
    copy.mkdir()
    module.create_broker(copy, target.resolve(), job.name, 0, 0, job / "runner")
    return copy, job, target


@pytest.mark.parametrize("protected", [False, True])
def test_handoff_only_starts_broker_and_cancel_never_replaces(staged, monkeypatch, protected):
    copy, job, target = protected_copy(staged, monkeypatch) if protected else (staged[0], staged[0], staged[1])
    before = files(target)
    calls = []
    monkeypatch.setattr(module, "launch", lambda exe, args: calls.append((exe, args)))
    monkeypatch.setattr(module, "spawn_helper", lambda *a: pytest.fail("Preparation must not start replacement"))
    module.handoff((copy, job))
    assert calls == [(copy / "runner" / EXE, ["--program-broker", job])]
    module.cancel_prepared((copy, job))
    assert files(target) == before
    assert module.load_job(job)["phase"] == ("prepared" if protected else "failed")


@pytest.mark.parametrize("protected", [False, True])
def test_cancel_refuses_started_replacement(staged, monkeypatch, protected):
    copy, job, target = protected_copy(staged, monkeypatch) if protected else (staged[0], staged[0], staged[1])
    module.publish_program_marker(target, job)
    before = files(target)
    with pytest.raises(UpdaterError, match="already started"):
        module.cancel_prepared((copy, job))
    assert files(target) == before


@pytest.mark.parametrize("state", ["abandoned", "completed", "cancelled", "active_parent", "active_worker", "pending_recovery"])
def test_protected_broker_collection_preserves_only_active_work(staged, monkeypatch, state):
    copy, job, target = protected_copy(staged, monkeypatch)
    data = module.load_job(job)
    if state == "completed":
        module.save_job(job, data, "committed")
    elif state == "cancelled":
        # UAC was dismissed before a protected job could be created.
        monkeypatch.setattr(windows, "admin_cache", lambda **kw: job.parent / "missing")
    elif state == "pending_recovery":
        module.publish_program_marker(target, job)
    monkeypatch.setattr(module, "process_alive", lambda *a: state == "active_parent")
    monkeypatch.setattr(module, "worker_alive", lambda *a: state == "active_worker")
    import psutil
    monkeypatch.setattr(psutil, "process_iter", lambda *a: [])
    before = files(target)
    module.prune_cache(copy.parent, target)
    assert copy.exists() == (state in {"active_parent", "active_worker", "pending_recovery"})
    assert files(target) == before


def test_running_broker_is_not_collected(staged, monkeypatch):
    copy, job, target = protected_copy(staged, monkeypatch)
    import psutil
    monkeypatch.setattr(psutil, "process_iter", lambda *a: [SimpleNamespace(info={"name": EXE, "exe": str(copy / "runner" / EXE)})])
    module.prune_cache(copy.parent, target)
    assert copy.exists()


@pytest.mark.parametrize("protected", [False, True])
def test_saved_runner_finds_interrupted_job_without_loading_target_runtime(staged, monkeypatch, protected):
    copy, job, target = protected_copy(staged, monkeypatch) if protected else (staged[0], staged[0], staged[1])
    module.publish_program_marker(target, job)
    (target / "_dxvk_updater/python313.dll").write_bytes(b"unstartable runtime")
    monkeypatch.setattr(module, "trusted_job", lambda p: p)
    assert module.saved_runner_job(copy / "runner" / EXE) == job


def test_saved_runner_never_starts_abandoned_preparation(staged, monkeypatch):
    job, _, _ = staged
    monkeypatch.setattr(module, "trusted_job", lambda p: p)
    with pytest.raises(UpdaterError, match="No interrupted"):
        module.saved_runner_job(job / "runner" / EXE)


def test_saved_protected_runner_rejects_mismatched_target(staged, monkeypatch):
    copy, job, target = protected_copy(staged, monkeypatch)
    pointer = module.read_broker(copy)
    pointer["target"] = str(target.parent.resolve())
    (copy / "broker.json").write_text(json.dumps(pointer))
    monkeypatch.setattr(module, "trusted_job", lambda p: p)
    with pytest.raises(UpdaterError, match="another installation"):
        module.saved_runner_job(copy / "runner" / EXE)


@pytest.mark.skipif(os.name != "nt", reason="Packaged Windows preparation boundary")
@pytest.mark.parametrize("cancelled", [False, True])
def test_admin_preparation_never_starts_worker(staged, monkeypatch, cancelled):
    copy, job, target = protected_copy(staged, monkeypatch)
    # Use a new broker directory, as a real preparation does.
    monkeypatch.setattr(module.sys, "executable", str(target / EXE))
    monkeypatch.setattr(module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(windows, "elevated", lambda: False)
    monkeypatch.setattr(module, "writable", lambda p: False)
    monkeypatch.setattr(module, "prune_cache", lambda *a: None)
    monkeypatch.setattr(module.uuid, "uuid4", lambda: SimpleNamespace(hex=job.name))
    calls = []
    monkeypatch.setattr(windows, "run_elevated", lambda exe, args: calls.append(args))
    monkeypatch.setattr(module, "spawn_helper", lambda *a: pytest.fail("No early elevated worker"))
    if cancelled:
        with pytest.raises(UpdaterError, match="cancelled"):
            module.prepare_update(staged[2], cancelled=lambda: True)
    else:
        prepared = module.prepare_update(staged[2])
        assert prepared == (copy.parent / job.name, job)
    assert calls[0][0] == "--program-prepare-admin"
    assert not (target / module.MARKER).exists()


@pytest.mark.skipif(os.name != "nt", reason="Packaged Windows helper dispatch")
def test_admin_dispatch_stages_without_launching_worker(staged, monkeypatch):
    job, target, release = staged
    monkeypatch.setattr(module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(module.sys, "executable", str(target / EXE))
    monkeypatch.setattr(windows, "elevated", lambda: True)
    cache = job.parent / "protected-new"
    cache.mkdir()
    monkeypatch.setattr(windows, "admin_cache", lambda **kw: cache)
    monkeypatch.setattr(module, "prune_cache", lambda *a: None)
    monkeypatch.setattr(module, "program_client", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(module, "fetch_program", lambda client: release)
    calls = []
    monkeypatch.setattr(module, "stage_job", lambda *a: calls.append(a))
    monkeypatch.setattr(module, "spawn_helper", lambda *a: pytest.fail("No replacement before handoff"))
    assert module.dispatch(["--program-prepare-admin", "c" * 32, release.version, "0", "0"]) == 0
    assert len(calls) == 1
