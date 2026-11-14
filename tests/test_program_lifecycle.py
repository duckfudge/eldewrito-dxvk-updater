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


@pytest.mark.skipif(os.name != "nt", reason="Packaged Windows preparation boundary")
def test_declined_preparation_never_starts_replacement(staged, monkeypatch):
    copy, job, target = protected_copy(staged, monkeypatch)
    before, protected_before = files(target), files(job)
    monkeypatch.setattr(module.sys, "executable", str(target / EXE))
    monkeypatch.setattr(module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(windows, "elevated", lambda: False)
    monkeypatch.setattr(module, "writable", lambda p: False)
    monkeypatch.setattr(module, "prune_cache", lambda *a: None)
    def denied(*args):
        raise UpdaterError("Administrator approval was cancelled.")
    monkeypatch.setattr(windows, "run_elevated", denied)
    monkeypatch.setattr(module, "spawn_helper", lambda *a: pytest.fail("No worker after denial"))
    monkeypatch.setattr(module, "launch", lambda *a: pytest.fail("No broker after denial"))
    with pytest.raises(UpdaterError, match="approval was cancelled"):
        module.prepare_update(staged[2])
    assert files(target) == before
    assert files(job) == protected_before


def test_declined_replacement_uses_only_the_protected_runner(staged, monkeypatch):
    _, job, target = protected_copy(staged, monkeypatch)
    before, protected_before, requests = files(target), files(job), []
    clock = [0]
    monkeypatch.setattr(windows, "elevated", lambda: False)
    monkeypatch.setattr(module, "worker_alive", lambda *a: False)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    def sleep(seconds):
        clock[0] += 1
    monkeypatch.setattr(module.time, "sleep", sleep)
    def denied(executable, arguments):
        requests.append((executable, arguments))
        raise UpdaterError("Administrator approval was cancelled.")
    monkeypatch.setattr(windows, "run_elevated", denied)
    monkeypatch.setattr(module, "launch", lambda *a: pytest.fail("No restart after denial"))
    with pytest.raises(UpdaterError, match="approval was cancelled"):
        module.broker(job)
    assert requests == [(job / "runner" / EXE, ["--program-start-admin", job])]
    assert files(target) == before
    assert files(job) == protected_before


@pytest.mark.skipif(os.name != "nt", reason="Packaged Windows recovery dispatch")
def test_protected_recovery_bootstrap_does_not_elevate_a_user_cache_copy(staged, monkeypatch):
    _, job, target = protected_copy(staged, monkeypatch)
    module.publish_program_marker(target, job)
    before, calls = files(target), []
    monkeypatch.setattr(module.sys, "executable", str(target / EXE))
    monkeypatch.setattr(module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(windows, "elevated", lambda: False)
    monkeypatch.setattr(windows, "run_elevated", lambda *a: pytest.fail("Bootstrap must keep the user token"))
    monkeypatch.setattr(module, "launch", lambda exe, args: calls.append((exe, args)))
    assert module.dispatch([]) == 0
    assert len(calls) == 1
    executable, arguments = calls[0]
    assert arguments == ["--program-broker", job]
    copy = executable.parent.parent
    assert copy.parent == module.user_cache()
    assert module.read_broker(copy)["job_id"] == job.name
    assert files(target) == before


@pytest.mark.parametrize("protected", [False, True])
def test_dead_startup_worker_recovers_before_launching_target(staged, monkeypatch, protected):
    _, job, target = protected_copy(staged, monkeypatch) if protected else (staged[0], staged[0], staged[1])
    before = files(target)
    def crash():
        raise SystemExit("interrupted worker")
    with pytest.raises(SystemExit):
        module.apply_update(job, crash)
    (target / "_dxvk_updater/python313.dll").write_bytes(b"unstartable runtime")
    clock, requests, launches = [0], [], []
    monkeypatch.setattr(module, "trusted_job", lambda value: value)
    monkeypatch.setattr(windows, "elevated", lambda: False)
    monkeypatch.setattr(module, "worker_alive", lambda *a: False)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "sleep", lambda _: clock.__setitem__(0, clock[0] + 1))
    def recover(*args):
        requests.append(args)
        module.apply_update(job, lambda: pytest.fail("Recovery must not run the broken target"))
    monkeypatch.setattr(windows, "run_elevated", recover)
    monkeypatch.setattr(module, "spawn_helper", recover)
    def launch(executable, arguments):
        assert arguments == [], "The broken runtime must not be started before recovery"
        assert files(target) == before
        launches.append(executable)
    monkeypatch.setattr(module, "launch", launch)
    module.broker(job)
    assert len(requests) == 1
    assert launches == [target / EXE]
    assert module.load_job(job)["phase"] == "rolled_back"


@pytest.mark.parametrize("protected", [False, True])
def test_dead_worker_releases_only_its_unacknowledged_child_before_retry(staged, monkeypatch, protected):
    _, job, target = protected_copy(staged, monkeypatch) if protected else (staged[0], staged[0], staged[1])
    data = module.load_job(job)
    data["phase"] = "awaiting_startup"
    clock, active, stopped, launches = [0], [True], [], []
    class Child:
        def poll(self):
            return None
        def terminate(self):
            stopped.append(True)
        def wait(self, timeout):
            assert timeout == 5
    def launch(executable, arguments):
        launches.append(arguments)
        if arguments:
            active[0] = False
        return Child()
    def recover(*args):
        assert stopped == [True], "The owned child must release the executable before rollback"
        data["phase"] = "rolled_back"
    monkeypatch.setattr(module, "trusted_job", lambda value: value)
    monkeypatch.setattr(module, "load_job", lambda value: data)
    monkeypatch.setattr(windows, "elevated", lambda: False)
    monkeypatch.setattr(module, "worker_alive", lambda *a: active[0])
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "sleep", lambda _: clock.__setitem__(0, clock[0] + 1))
    monkeypatch.setattr(module, "launch", launch)
    monkeypatch.setattr(windows, "run_elevated", recover)
    monkeypatch.setattr(module, "spawn_helper", recover)
    module.broker(job)
    assert launches == [["--program-health", job.name], []]
    assert stopped == [True]


def test_uac_wait_does_not_consume_broker_work_deadline(staged, monkeypatch):
    _, job, target = protected_copy(staged, monkeypatch)
    data = module.load_job(job)
    clock, active, launches = [0], [False], []
    def approved(*args):
        clock[0] += 600
        active[0] = True
        data["phase"] = "awaiting_startup"
    def launch(executable, arguments):
        launches.append(arguments)
        data["phase"] = "committed"
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(module, "trusted_job", lambda value: value)
    monkeypatch.setattr(module, "load_job", lambda value: data)
    monkeypatch.setattr(windows, "elevated", lambda: False)
    monkeypatch.setattr(module, "worker_alive", lambda *a: active[0])
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "sleep", lambda _: clock.__setitem__(0, clock[0] + 1))
    monkeypatch.setattr(module, "launch", launch)
    monkeypatch.setattr(windows, "run_elevated", approved)
    module.broker(job)
    assert launches == [["--program-health", job.name]]


@pytest.mark.skipif(os.name != "nt", reason="Packaged Windows recovery dispatch")
def test_recovery_dispatch_records_a_locked_destination(staged, monkeypatch):
    job, _, _ = staged
    def crash():
        raise SystemExit("interrupted worker")
    with pytest.raises(SystemExit):
        module.apply_update(job, crash)
    def locked(*args):
        raise PermissionError("QA locked destination")
    closed = []
    monkeypatch.setattr(module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(module.sys, "executable", str(job / "runner" / EXE))
    monkeypatch.setattr(windows, "elevated", lambda: False)
    monkeypatch.setattr(windows, "health_event", lambda *a: "test event")
    monkeypatch.setattr(windows, "close_handle", closed.append)
    monkeypatch.setattr(module, "rollback_program", locked)
    assert module.dispatch(["--program-apply", str(job)]) == 1
    data = module.load_job(job)
    assert data["phase"] == "recovery_required"
    assert "QA locked destination" in data["error"]
    assert closed == ["test event"]


def test_committed_startup_child_survives_helper_cleanup_retry(staged, monkeypatch):
    job, _, _ = staged
    module.publish_program_marker(Path(module.load_job(job)["target"]), job)
    data = module.load_job(job)
    data["phase"] = "awaiting_startup"
    clock, launches = [0], []
    class Child:
        def poll(self):
            return None
        def terminate(self):
            pytest.fail("A durably committed app must remain open during marker cleanup")
    def launch(executable, arguments):
        launches.append(arguments)
        data["phase"] = "committed"
        return Child()
    monkeypatch.setattr(module, "trusted_job", lambda value: value)
    monkeypatch.setattr(module, "load_job", lambda value: data)
    monkeypatch.setattr(windows, "elevated", lambda: False)
    monkeypatch.setattr(module, "worker_alive", lambda *a: data["phase"] != "committed")
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "sleep", lambda _: clock.__setitem__(0, clock[0] + 1))
    monkeypatch.setattr(module, "launch", launch)
    monkeypatch.setattr(module, "spawn_helper", lambda _: (Path(data["target"]) / module.MARKER).unlink())
    module.broker(job)
    assert launches == [["--program-health", job.name]]
