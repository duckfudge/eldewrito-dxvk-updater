"""Faults around persisted boundaries must leave one recoverable program version."""
import json
import shutil
from pathlib import Path

import pytest

from dxvk_updater import program_update as module
from dxvk_updater.bundle import EXE, read_inventory
from dxvk_updater.model import UpdaterError
from test_program_update import files, staged  # Reuse the real staged-package fixture.


@pytest.mark.parametrize("boundary, final_phase", [
    ("backup_first", "committed"),
    ("marker_before", "committed"),
    ("marker_after", "rolled_back"),
    ("applying_before", "rolled_back"),
    ("applying_after", "rolled_back"),
    ("replacement_first", "rolled_back"),
    ("awaiting_startup_before", "rolled_back"),
    ("awaiting_startup_after", "rolled_back"),
    ("committed_before", "rolled_back"),
    ("committed_after", "committed"),
    ("rolled_back_before", "rolled_back"),
    ("rolled_back_after", "rolled_back"),
])
def test_crash_at_persisted_boundaries_recovers(staged, monkeypatch, boundary, final_phase):
    job, target, _ = staged
    before = files(target)
    original_copy = module.atomic_copy
    original_save = module.save_job
    original_marker = module.publish_program_marker
    crashed = False

    def crash():
        nonlocal crashed
        crashed = True
        raise KeyboardInterrupt("simulated process loss")

    def copy(source, destination):
        original_copy(source, destination)
        if not crashed and (
            boundary == "backup_first" and destination.is_relative_to(job / "backup") or
            boundary == "replacement_first" and destination.is_relative_to(target)
        ):
            crash()

    def save(job, data, phase, error=""):
        if not crashed and boundary == phase + "_before":
            crash()
        original_save(job, data, phase, error)
        if not crashed and boundary == phase + "_after":
            crash()

    def marker(target, job):
        if boundary == "marker_before":
            crash()
        original_marker(target, job)
        if boundary == "marker_after":
            crash()

    with monkeypatch.context() as injected:
        injected.setattr(module, "atomic_copy", copy)
        injected.setattr(module, "save_job", save)
        injected.setattr(module, "publish_program_marker", marker)
        with pytest.raises(KeyboardInterrupt):
            module.apply_update(job, lambda: not boundary.startswith("rolled_back"))
    assert crashed

    module.apply_update(job, lambda: final_phase == "committed")
    assert module.load_job(job)["phase"] == final_phase
    assert not (target / module.MARKER).exists()
    read_inventory(target, verify=True)
    if final_phase == "rolled_back":
        assert files(target) == before
    else:
        assert (target / EXE).read_bytes() == b"new program"
        assert (target / "eldorado.exe").read_bytes() == before["eldorado.exe"]


@pytest.mark.parametrize("successful", [True, False])
def test_terminal_cleanup_failure_never_changes_terminal_state(staged, monkeypatch, successful):
    job, target, _ = staged
    before = files(target)
    original = Path.unlink
    denied = False

    def deny_once(path, *args, **kwargs):
        nonlocal denied
        if path == target / module.MARKER and not denied:
            denied = True
            raise PermissionError("injected marker cleanup denial")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_once)
    with pytest.raises(UpdaterError, match="cleanup is pending"):
        module.apply_update(job, lambda: successful)
    phase = "committed" if successful else "rolled_back"
    assert module.load_job(job)["phase"] == phase
    assert (target / EXE).read_bytes() == (b"new program" if successful else before[EXE])
    assert (target / module.MARKER).exists()

    module.apply_update(job, lambda: pytest.fail("Terminal cleanup must not launch again"))
    assert module.load_job(job)["phase"] == phase
    assert not (target / module.MARKER).exists()


@pytest.mark.parametrize("phase", ["committed", "rolled_back"])
def test_error_after_terminal_state_was_written_keeps_that_state(staged, monkeypatch, phase):
    job, target, _ = staged
    original = module.save_job
    failed = False

    def save(job, data, next_phase, error=""):
        nonlocal failed
        original(job, data, next_phase, error)
        if next_phase == phase and not failed:
            failed = True
            raise OSError("injected failure after durable state write")

    monkeypatch.setattr(module, "save_job", save)
    if phase == "committed":
        module.apply_update(job, lambda: True)
    else:
        with pytest.raises(UpdaterError, match="previous version was restored"):
            module.apply_update(job, lambda: False)
    assert module.load_job(job)["phase"] == phase
    assert (target / EXE).read_bytes() == (b"new program" if phase == "committed" else b"old program")
    assert not (target / module.MARKER).exists()


def test_atomic_pointer_publication_never_overwrites_another_owner(staged):
    job, target, _ = staged
    pointer = {"job_id": "b" * 32, "admin": False}
    marker = target / module.MARKER
    marker.write_text(json.dumps(pointer), encoding="utf-8")
    before = files(target)
    with pytest.raises(FileExistsError):
        module.publish_program_marker(target, job)
    assert files(target) == before
    assert not list(target.glob("DXVKUpdater-pointer-*"))


@pytest.mark.parametrize("partial", [False, True])
def test_owned_prepared_marker_recovers_without_installing(staged, partial):
    job, target, _ = staged
    before = files(target)
    shutil.copytree(job / "runner", job / "backup")
    pointer = json.dumps({"job_id": job.name, "admin": False})
    (target / module.MARKER).write_text(pointer[:-3] if partial else pointer, encoding="utf-8")
    module.apply_update(job, lambda: pytest.fail("No files were replaced"))
    assert module.load_job(job)["phase"] == "rolled_back"
    assert files(target) == before


@pytest.mark.parametrize("pointer", ["", '{"job_id": "a', json.dumps({"job_id": "b" * 32, "admin": False})])
def test_prepared_recovery_refuses_unknown_marker_ownership(staged, pointer):
    job, target, _ = staged
    shutil.copytree(job / "runner", job / "backup")
    (target / module.MARKER).write_text(pointer, encoding="utf-8")
    before = files(target)
    with pytest.raises(UpdaterError, match="does not identify"):
        module.apply_update(job, lambda: pytest.fail("Cannot claim another owner's marker"))
    assert files(target) == before


@pytest.mark.parametrize("damage", ["backup", "target"])
def test_legacy_partial_pointer_recovery_requires_unchanged_verified_files(staged, damage):
    job, target, _ = staged
    shutil.copytree(job / "runner", job / "backup")
    pointer = json.dumps({"job_id": job.name, "admin": False})
    (target / module.MARKER).write_text(pointer[:-3], encoding="utf-8")
    ((job / "backup" if damage == "backup" else target) / EXE).write_bytes(b"damaged")
    before = files(target)
    with pytest.raises(UpdaterError, match="verification failed"):
        module.apply_update(job, lambda: pytest.fail("Unverified files must not be touched"))
    assert files(target) == before
