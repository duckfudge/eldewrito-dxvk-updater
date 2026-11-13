import copy
import hashlib
import json
import shutil
import os
import zipfile

import pytest

from dxvk_updater import program_update as module
from dxvk_updater.bundle import EXE, INVENTORY, extract_bundle, owned_path, read_inventory
from dxvk_updater.model import UpdaterError
from dxvk_updater.program_update import ProgramRelease, apply_update, load_job, stage_job


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def add_owned(root, name, content):
    (root / name).write_bytes(content)
    path = root / INVENTORY
    inventory = json.loads(path.read_text())
    inventory["files"].append({"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    path.write_text(json.dumps(inventory))


@pytest.fixture
def staged(tmp_path, monkeypatch, bundle_factory, zip_bundle):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(module, "user_cache", lambda: cache)
    target = bundle_factory(tmp_path / "mixed game ü 日本語", "1.1.0")
    for name in ("eldorado.exe", "d3d9.dll", "dxvk.conf", "eldorado.dxvk-cache", "README.txt", "_dxvk_updater/personal.txt"):
        (target / name).write_bytes(b"unrelated player file")
    package = bundle_factory(tmp_path / "new", "1.2.0", b"new program")
    archive = zip_bundle(package, tmp_path / "new.zip")
    release = ProgramRelease("1.2.0", "a" * 40, archive.stat().st_size, hashlib.sha256(archive.read_bytes()).hexdigest(), "2026-11-13T00:00:00+00:00", "new release")
    class Client:
        def download_program(self, release, target, progress, cancelled):
            shutil.copyfile(archive, target)
    job = cache / ("a" * 32)
    job.mkdir()
    stage_job(job, target.resolve(), release, 0, 0, Client(), lambda *args: None, lambda: False)
    return job, target, release


def test_update_preserves_game_and_unrelated_files(staged):
    job, target, _ = staged
    before = files(target)
    def started():
        assert (target / EXE).read_bytes() == b"new program"
        assert (target / module.MARKER).exists()
        return True
    apply_update(job, started)
    assert load_job(job)["phase"] == "committed"
    assert not (target / module.MARKER).exists()
    for name, value in before.items():
        if name not in {EXE, INVENTORY, "_dxvk_updater/python313.dll"}:
            assert (target / name).read_bytes() == value
    read_inventory(target, verify=True)


def test_failed_startup_restores_complete_program(staged):
    job, target, _ = staged
    before = files(target)
    with pytest.raises(UpdaterError, match="previous version was restored"):
        apply_update(job, lambda: False)
    assert files(target) == before
    assert load_job(job)["phase"] == "rolled_back"


def test_unowned_collision_aborts_before_replacement(staged, tmp_path, zip_bundle):
    job, target, release = staged
    before = files(target)
    add_owned(job / "payload", "_dxvk_updater/personal.txt", b"new program asset")
    archive = zip_bundle(job / "payload", tmp_path / "modified.zip")
    (job / "package.zip").write_bytes(archive.read_bytes())
    data = load_job(job)
    data["release"].update(size=archive.stat().st_size, sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    module.save_job(job, data, "prepared")
    with pytest.raises(UpdaterError, match="unrelated file"):
        apply_update(job, lambda: True)
    assert files(target) == before


def test_only_obsolete_owned_files_are_deleted(staged):
    job, target, _ = staged
    for root in (target, job / "runner"):
        add_owned(root, "_dxvk_updater/obsolete.dat", b"obsolete asset")
    apply_update(job, lambda: True)
    assert not (target / "_dxvk_updater/obsolete.dat").exists()
    assert (target / "_dxvk_updater/personal.txt").read_bytes() == b"unrelated player file"


def test_file_lock_rolls_back_without_touching_game(staged, monkeypatch):
    job, target, _ = staged
    before = files(target)
    original, failed = module.atomic_copy, False
    def locked(source, destination):
        nonlocal failed
        if destination == target / "_dxvk_updater/python313.dll" and not failed:
            failed = True
            raise PermissionError("injected lock")
        original(source, destination)
    monkeypatch.setattr(module, "atomic_copy", locked)
    with pytest.raises(UpdaterError, match="previous version was restored"):
        apply_update(job, lambda: True)
    assert files(target) == before


def test_interrupted_replacement_recovers_offline(staged, monkeypatch):
    job, target, _ = staged
    before = files(target)
    original = module.atomic_copy
    def crash(source, destination):
        if destination == target / "_dxvk_updater/python313.dll":
            raise KeyboardInterrupt("power loss")
        original(source, destination)
    monkeypatch.setattr(module, "atomic_copy", crash)
    with pytest.raises(KeyboardInterrupt):
        apply_update(job, lambda: True)
    assert (target / module.MARKER).exists()
    assert files(target) != before
    monkeypatch.setattr(module, "atomic_copy", original)
    apply_update(job, lambda: pytest.fail("Recovery must not wait for a new GUI"))
    assert files(target) == before


def test_interruption_after_commit_keeps_new_program(staged, monkeypatch):
    job, target, _ = staged
    original = module.save_job
    def crash(job, data, phase, error=""):
        original(job, data, phase, error)
        if phase == "committed":
            raise KeyboardInterrupt("after commit")
    monkeypatch.setattr(module, "save_job", crash)
    with pytest.raises(KeyboardInterrupt):
        apply_update(job, lambda: True)
    monkeypatch.setattr(module, "save_job", original)
    apply_update(job, lambda: pytest.fail("Already committed"))
    assert (target / EXE).read_bytes() == b"new program"
    assert not (target / module.MARKER).exists()


def test_damaged_backup_stops_recovery_before_any_write(staged, monkeypatch):
    job, target, _ = staged
    original = module.atomic_copy
    def crash(source, destination):
        if destination == target / "_dxvk_updater/python313.dll":
            raise KeyboardInterrupt()
        original(source, destination)
    monkeypatch.setattr(module, "atomic_copy", crash)
    with pytest.raises(KeyboardInterrupt):
        apply_update(job, lambda: True)
    monkeypatch.setattr(module, "atomic_copy", original)
    (job / "backup" / EXE).write_bytes(b"damaged")
    partial = files(target)
    with pytest.raises(UpdaterError, match="verification failed"):
        apply_update(job, lambda: True)
    assert files(target) == partial


@pytest.mark.parametrize("damage", ["payload", "archive", "inventory"])
def test_tampered_stage_is_rejected_before_program_changes(staged, damage):
    job, target, _ = staged
    before = files(target)
    if damage == "archive":
        (job / "package.zip").write_bytes(b"tampered")
    else:
        (job / "payload" / EXE).write_bytes(b"tampered")
        if damage == "inventory":
            obj = json.loads((job / "payload" / INVENTORY).read_text())
            row = next(row for row in obj["files"] if row["path"] == EXE)
            row.update(size=8, sha256=hashlib.sha256(b"tampered").hexdigest())
            (job / "payload" / INVENTORY).write_text(json.dumps(obj))
    with pytest.raises(UpdaterError):
        apply_update(job, lambda: True)
    assert files(target) == before


def test_two_helpers_cannot_replace_simultaneously(staged):
    from dxvk_updater.storage import file_lock
    job, target, _ = staged
    before = files(target)
    with file_lock(job / "lock", "test lock"):
        with pytest.raises(UpdaterError, match="already running"):
            apply_update(job, lambda: True)
    assert files(target) == before


def test_completed_cache_collection_keeps_pending_recovery(staged, monkeypatch):
    job, target, _ = staged
    data = load_job(job)
    module.save_job(job, data, "applying")
    module.prune_cache(job.parent, target)
    assert job.exists()
    module.save_job(job, data, "failed")
    monkeypatch.setattr(module, "worker_alive", lambda *a: False)
    import psutil
    monkeypatch.setattr(psutil, "process_iter", lambda *a: [])
    before = files(target)
    module.prune_cache(job.parent, target)
    assert not job.exists()
    assert files(target) == before


def test_elevated_helper_rejects_user_writable_staging(staged, monkeypatch, tmp_path):
    from dxvk_updater import windows
    job, _, _ = staged
    monkeypatch.setattr(windows, "elevated", lambda: True)
    monkeypatch.setattr(windows, "admin_cache", lambda: tmp_path / "protected")
    with pytest.raises(UpdaterError, match="protected staging"):
        module.trusted_job(job, applying=True)


def test_broker_stops_only_its_unacknowledged_startup_child(staged, monkeypatch):
    from dxvk_updater import windows
    job, target, _ = staged
    data = load_job(job)
    data["phase"] = "awaiting_startup"
    clock, calls, stopped = [0], [], []
    class Child:
        def poll(self):
            return None
        def terminate(self):
            stopped.append(True)
        def wait(self, timeout):
            assert timeout == 5
    def sleep(seconds):
        clock[0] += 10
        if stopped:
            data["phase"] = "rolled_back"
    monkeypatch.setattr(module, "trusted_job", lambda value: value)
    monkeypatch.setattr(module, "load_job", lambda value: data)
    monkeypatch.setattr(module, "worker_alive", lambda *a: True)
    monkeypatch.setattr(windows, "elevated", lambda: False)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "sleep", sleep)
    monkeypatch.setattr(module, "launch", lambda exe, args: calls.append((exe, args)) or Child())
    module.broker(job)
    assert stopped == [True]
    assert calls[0][1] == ["--program-health", job.name]
    assert calls[1] == (target / EXE, [])


def test_cancelled_handoff_never_changes_program(staged):
    job, target, _ = staged
    before = files(target)
    def parent_exited(data):
        module.save_job(job, data, "failed", "broker could not start")
    apply_update(job, lambda: pytest.fail("No startup expected"), wait_for_parent=parent_exited)
    assert files(target) == before


def test_launch_uses_explicit_executable_and_resets_frozen_environment(staged, monkeypatch):
    job, _, _ = staged
    calls = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **options: calls.append((command, options)))
    module.launch(job / "runner" / EXE, ["--program-apply", job])
    assert calls[0][1]["executable"] == str(job / "runner" / EXE)
    assert calls[0][1]["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows event API")
def test_native_startup_acknowledgement():
    import uuid
    from dxvk_updater import windows
    job_id = uuid.uuid4().hex
    handle = windows.health_event(job_id)
    try:
        windows.health_event(job_id, signal=True)
        assert windows.wait_health(handle, timeout=1)
    finally:
        windows.close_handle(handle)


@pytest.mark.parametrize("path", ["../eldorado.exe", "d3d9.dll", "README.txt", "C:/Windows/file", "_dxvk_updater/a:ads", "_dxvk_updater/CON.txt", "_dxvk_updater/a.", "_dxvk_updater/../x", "_dxvk_updater/a\\b"])
def test_program_ownership_rejects_unsafe_or_game_paths(path):
    with pytest.raises(UpdaterError):
        owned_path(path)


def test_zip_member_injection_is_rejected_before_extraction(tmp_path, bundle_factory, zip_bundle):
    root = bundle_factory(tmp_path / "bundle")
    archive = zip_bundle(root, tmp_path / "bundle.zip")
    with zipfile.ZipFile(archive, "a") as output:
        output.writestr("ElDewritoDXVKUpdater/eldorado.exe", b"injection")
    with pytest.raises(UpdaterError):
        extract_bundle(archive, tmp_path / "payload", "1.1.0")
    assert not (tmp_path / "payload").exists()


def test_program_metadata_is_strict_and_has_fixed_asset_location(staged):
    _, _, release = staged
    assert ProgramRelease.parse(json.dumps(release.to_dict())) == release
    assert release.download_url().startswith("https://github.com/duckfudge/eldewrito-dxvk/releases/download/updater-v1.2.0/")
    for change in ({"url": "https://example.test"}, {"version": "../main"}, {"size": True}, {"schema_version": True}, {"sha256": "bad"}):
        obj = copy.deepcopy(release.to_dict())
        obj.update(change)
        with pytest.raises(UpdaterError):
            ProgramRelease.parse(obj)


def test_program_download_redirect_is_bounded_and_checksum_verified(staged, tmp_path):
    from dxvk_updater.network import FeedClient
    job, _, release = staged
    class Response:
        def __init__(self, url, status, headers=None):
            self.url, self.status_code, self.headers = url, status, headers or {}
        def close(self):
            pass
        def iter_content(self, chunk_size):
            yield (job / "package.zip").read_bytes()
    class Session:
        def get(self, url, **kwargs):
            assert kwargs["allow_redirects"] is False
            if url == release.download_url():
                return Response(url, 302, {"Location": "https://release-assets.githubusercontent.com/a?token=temporary"})
            return Response(url, 200)
    destination = tmp_path / "download.zip"
    FeedClient(Session()).download_program(release, destination)
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == release.sha256


@pytest.mark.parametrize("location", ["http://release-assets.githubusercontent.com/a", "https://evil.test/a", "https://user@release-assets.githubusercontent.com/a"])
def test_program_download_refuses_untrusted_redirect(staged, tmp_path, location):
    from dxvk_updater.network import FeedClient
    _, _, release = staged
    class Session:
        def get(self, *args, **kwargs):
            return type("Response", (), {"status_code": 302, "headers": {"Location": location}, "close": lambda s: None})()
    with pytest.raises(UpdaterError):
        FeedClient(Session()).download_program(release, tmp_path / "download.zip")
