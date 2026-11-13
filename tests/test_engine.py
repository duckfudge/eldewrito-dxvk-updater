from pathlib import Path
import errno
import hashlib
import struct

import pytest

import dxvk_updater.engine as module
from dxvk_updater.engine import Installer
from dxvk_updater.model import FILES, UpdaterError, make_manifest
from dxvk_updater.storage import Store


def installer(game, client, guard=lambda root: None):
    return Installer(game, client, game_guard=guard)


def snapshot(game):
    return {name: (game / name).read_bytes() if (game / name).exists() else None for name in FILES}


def test_clean_install_restore_and_redo(game, client, manifest, payloads):
    engine = installer(game, client)
    assert engine.check(manifest).kind == "install"
    engine.install(manifest)
    assert snapshot(game) == payloads
    assert engine.check(manifest).kind == "current"
    assert engine.check(manifest).can_restore
    engine.restore()
    assert all(value is None for value in snapshot(game).values())
    engine.restore()
    assert snapshot(game) == payloads


def test_adopts_matching_manual_install_without_replacement(game, client, manifest, payloads):
    for name, data in payloads.items():
        (game / name).write_bytes(data)
    engine = installer(game, client)
    result = engine.check(manifest)
    assert result.kind == "current"
    assert client.calls == []
    assert not result.can_restore


def test_preserves_and_restores_unknown_manual_files(game, client, manifest):
    (game / "d3d9.dll").write_bytes(b"previous rendering wrapper")
    (game / "dxvk.conf").write_bytes(b"personal config")
    before = snapshot(game)
    engine = installer(game, client)
    engine.install(manifest)
    engine.restore()
    assert snapshot(game) == before


def test_local_cache_growth_does_not_trigger_update(game, client, manifest):
    engine = installer(game, client)
    engine.install(manifest)
    (game / "eldorado.dxvk-cache").write_bytes(b"DXVKlarger locally learned cache")
    (game / "dxvk.conf").write_text("dxvk.hud = 0\n")
    assert engine.check(manifest).kind == "current"
    count = len(client.calls)
    engine.install(manifest)
    assert len(client.calls) == count


def test_config_only_update_preserves_local_cache_and_dll(game, client, manifest, payloads):
    engine = installer(game, client)
    engine.install(manifest)
    (game / "eldorado.dxvk-cache").write_bytes(b"DXVKl ocally expanded cache")
    before = snapshot(game)
    payloads["dxvk.conf"] = b"dxvk.hud = fps\ndxvk.numCompilerThreads = 1\n"
    updated = make_manifest("b" * 40, payloads, "config only")
    client.calls.clear()
    engine.install(updated)
    assert client.calls == ["dxvk.conf"]
    assert (game / "eldorado.dxvk-cache").read_bytes() == before["eldorado.dxvk-cache"]
    assert engine.check(updated).kind == "current"
    engine.restore()
    assert snapshot(game) == before
    assert engine.check(updated).kind == "update"
    assert engine.check(updated).suppressed


def test_remote_cache_update_replaces_local_cache(game, client, manifest, payloads):
    engine = installer(game, client)
    engine.install(manifest)
    (game / "eldorado.dxvk-cache").write_bytes(b"DXVKlocal changes")
    import struct
    payloads["eldorado.dxvk-cache"] = struct.pack("<4sII", b"DXVK", 17, 0)
    updated = make_manifest("b" * 40, payloads, "cache update")
    engine.install(updated)
    assert (game / "eldorado.dxvk-cache").read_bytes() == payloads["eldorado.dxvk-cache"]
    engine.restore()
    assert (game / "eldorado.dxvk-cache").read_bytes() == b"DXVKlocal changes"


def test_missing_file_and_explicit_repair(game, client, manifest, payloads):
    engine = installer(game, client)
    engine.install(manifest)
    (game / "d3d9.dll").unlink()
    assert engine.check(manifest).kind == "repair"
    client.calls.clear()
    engine.install(manifest)
    assert client.calls == ["d3d9.dll"]
    (game / "dxvk.conf").write_bytes(b"local config")
    client.calls.clear()
    engine.install(manifest, repair=True)
    assert client.calls == list(FILES)
    assert snapshot(game) == payloads
    repair_id = engine.store.state()["repair_id"]
    assert (engine.store.tx_path(repair_id) / "before" / "dxvk.conf").read_bytes() == b"local config"
    engine.restore()
    assert all(value is None for value in snapshot(game).values())


def test_failed_download_never_changes_patch(game, client, manifest):
    (game / "d3d9.dll").write_bytes(b"original")
    before = snapshot(game)
    client.fail_at = "dxvk.conf"
    with pytest.raises(OSError):
        installer(game, client).install(manifest)
    assert snapshot(game) == before
    assert not (game / ".dxvk-updater" / "journal.json").exists()


def test_cancel_before_install_leaves_files_alone(game, client, manifest):
    with pytest.raises(UpdaterError, match="cancelled"):
        installer(game, client).install(manifest, cancelled=lambda: True)
    assert all(value is None for value in snapshot(game).values())


def test_running_game_blocks_mutation(game, client, manifest):
    def guard(root):
        raise UpdaterError("Close ElDewrito")
    with pytest.raises(UpdaterError, match="Close"):
        installer(game, client, guard).install(manifest)
    assert client.calls == []


def test_replacement_failure_rolls_back_every_file(game, client, manifest, monkeypatch):
    (game / "d3d9.dll").write_bytes(b"original DLL")
    before = snapshot(game)
    original = module.atomic_copy
    failed = False
    def fail_once(source, target):
        nonlocal failed
        if target == game / "dxvk.conf" and not failed:
            failed = True
            raise PermissionError("injected lock")
        original(source, target)
    monkeypatch.setattr(module, "atomic_copy", fail_once)
    with pytest.raises(UpdaterError, match="previous files were restored"):
        installer(game, client).install(manifest)
    assert snapshot(game) == before
    assert not Store(game).journal_path.exists()


def test_crash_mid_install_recovers_offline(game, client, manifest, monkeypatch):
    (game / "d3d9.dll").write_bytes(b"original")
    before = snapshot(game)
    original = module.atomic_copy
    def interrupt(source, target):
        if target == game / "dxvk.conf":
            raise KeyboardInterrupt("simulated process termination")
        original(source, target)
    monkeypatch.setattr(module, "atomic_copy", interrupt)
    with pytest.raises(KeyboardInterrupt):
        installer(game, client).install(manifest)
    assert Store(game).journal_path.exists()
    monkeypatch.setattr(module, "atomic_copy", original)
    installer(game, None).recover()
    assert snapshot(game) == before
    assert not Store(game).journal_path.exists()


def test_crash_after_state_commit_keeps_new_install(game, client, manifest, payloads, monkeypatch):
    original = Store.save_state
    def commit_then_crash(store, state):
        original(store, state)
        raise KeyboardInterrupt("termination after commit")
    monkeypatch.setattr(Store, "save_state", commit_then_crash)
    with pytest.raises(KeyboardInterrupt):
        installer(game, client).install(manifest)
    monkeypatch.setattr(Store, "save_state", original)
    installer(game, None).recover()
    assert snapshot(game) == payloads
    assert not Store(game).journal_path.exists()


def test_damaged_backup_stops_restore_before_changes(game, client, manifest):
    (game / "d3d9.dll").write_bytes(b"original")
    engine = installer(game, client)
    engine.install(manifest)
    before = snapshot(game)
    state = engine.store.state()
    (engine.store.tx_path(state["transaction_id"]) / "before" / "d3d9.dll").write_bytes(b"tampered")
    with pytest.raises(UpdaterError, match="damaged"):
        engine.restore()
    assert snapshot(game) == before


def test_completed_update_can_open_even_if_old_backup_is_damaged(game, client, manifest, payloads, monkeypatch):
    (game / "d3d9.dll").write_bytes(b"original")
    engine = installer(game, client)
    original = Store.save_state
    def commit_then_crash(store, state):
        original(store, state)
        raise KeyboardInterrupt("termination after commit")
    monkeypatch.setattr(Store, "save_state", commit_then_crash)
    with pytest.raises(KeyboardInterrupt):
        engine.install(manifest)
    monkeypatch.setattr(Store, "save_state", original)
    transaction = engine.store.state()["transaction_id"]
    (engine.store.tx_path(transaction) / "before" / "d3d9.dll").write_bytes(b"damaged")
    engine.recover()
    assert not engine.store.journal_path.exists()
    assert engine.check(manifest).kind == "current"
    with pytest.raises(UpdaterError, match="damaged"):
        engine.restore()
    assert snapshot(game) == payloads


def test_lock_excludes_second_instance(game, client, manifest):
    first, second = installer(game, client), installer(game, client)
    with first.store.lock():
        with pytest.raises(UpdaterError, match="Another updater"):
            second.install(manifest)


def test_unrelated_files_are_never_modified(game, client, manifest):
    unrelated = game / "dewrito_prefs.cfg"
    unrelated.write_text("personal settings")
    engine = installer(game, client)
    engine.install(manifest)
    engine.restore()
    assert unrelated.read_text() == "personal settings"


def test_game_directory_validation(tmp_path, client):
    with pytest.raises(UpdaterError, match="missing"):
        installer(tmp_path, client)


def test_file_modified_while_staging_aborts(game, client, manifest, monkeypatch):
    engine = installer(game, client)
    real_prepare = engine._prepare
    def altered(names, desired):
        folder, record = real_prepare(names, desired)
        (game / "dxvk.conf").write_bytes(b"someone changed this")
        return folder, record
    monkeypatch.setattr(engine, "_prepare", altered)
    with pytest.raises(UpdaterError, match="changed while preparing"):
        engine.install(manifest)
    assert not (game / "d3d9.dll").exists()
    assert list(engine.store.transactions.iterdir()) == []


def test_large_local_cache_can_be_backed_up_and_restored(game, client, manifest):
    from dxvk_updater.model import MAX_SIZES, sha256_file
    cache = game / "eldorado.dxvk-cache"
    with cache.open("wb") as stream:
        stream.write(b"DXVK")
        stream.truncate(MAX_SIZES[cache.name] + 1)
    original_hash = sha256_file(cache)
    engine = installer(game, client)
    engine.install(manifest, repair=True)
    engine.restore()
    assert cache.stat().st_size == MAX_SIZES[cache.name] + 1
    assert sha256_file(cache) == original_hash


def test_adoption_after_restore_keeps_new_restore_point(game, client, manifest, payloads):
    # Repair a manually installed patch without first adopting it, then restore.
    for name, data in payloads.items():
        (game / name).write_bytes(data)
    engine = installer(game, client)
    engine.install(manifest, repair=True)
    engine.restore()
    restore_id = engine.store.state()["transaction_id"]
    assert engine.check(manifest).can_restore
    assert engine.store.state()["transaction_id"] == restore_id
    engine.restore()
    assert snapshot(game) == payloads


def test_backup_failure_discards_unstarted_transaction(game, client, manifest, monkeypatch):
    (game / "d3d9.dll").write_bytes(b"original")
    engine = installer(game, client)
    def fail_backup(source, target):
        raise PermissionError("disk full")
    monkeypatch.setattr(module, "atomic_copy", fail_backup)
    with pytest.raises(PermissionError):
        engine.install(manifest)
    assert list(engine.store.transactions.iterdir()) == []
    assert (game / "d3d9.dll").read_bytes() == b"original"


def test_rollback_cleanup_failure_does_not_report_incomplete_recovery(game, client, manifest, monkeypatch):
    engine = installer(game, client)
    original = module.atomic_copy
    def fail_install(source, target):
        if target == game / "dxvk.conf":
            raise PermissionError("locked")
        original(source, target)
    def fail_cleanup(transaction):
        raise PermissionError("cleanup locked")
    monkeypatch.setattr(module, "atomic_copy", fail_install)
    monkeypatch.setattr(engine.store, "remove_transaction", fail_cleanup)
    with pytest.raises(UpdaterError, match="previous files were restored"):
        engine.install(manifest)
    assert all(value is None for value in snapshot(game).values())
    assert not engine.store.journal_path.exists()


def test_symlink_target_is_rejected(game, client, manifest, tmp_path):
    outside = tmp_path / "outside.dll"
    outside.write_bytes(b"must not change")
    try:
        (game / "d3d9.dll").symlink_to(outside)
    except OSError:
        pytest.skip("Creating Windows symlinks requires Developer Mode or elevation")
    with pytest.raises(UpdaterError, match="Links"):
        installer(game, client).install(manifest)
    assert outside.read_bytes() == b"must not change"


@pytest.mark.parametrize("damage", [b"", b"x" * 1024], ids=["empty", "replaced"])
def test_damaged_dll_repairs_only_dll_and_preserves_local_edits(game, client, manifest, damage):
    engine = installer(game, client)
    engine.install(manifest)
    (game / "d3d9.dll").write_bytes(damage)
    (game / "dxvk.conf").write_bytes(b"personal config")
    (game / "eldorado.dxvk-cache").write_bytes(b"locally expanded cache")
    before = snapshot(game)
    status = engine.check(manifest)
    assert status.kind == "repair"
    assert status.repair_files == ("d3d9.dll",)
    client.calls.clear()
    engine.install(manifest)
    assert client.calls == ["d3d9.dll"]
    assert (game / "dxvk.conf").read_bytes() == before["dxvk.conf"]
    assert (game / "eldorado.dxvk-cache").read_bytes() == before["eldorado.dxvk-cache"]
    assert engine.check(manifest).kind == "current"
    repair_id = engine.store.state()["repair_id"]
    assert (engine.store.tx_path(repair_id) / "before" / "d3d9.dll").read_bytes() == damage
    engine.restore()
    assert all(value is None for value in snapshot(game).values())


def test_disk_full_rollback_skips_already_restored_cache(game, client, payloads, monkeypatch):
    (game / "d3d9.dll").write_bytes(b"previous DLL")
    (game / "dxvk.conf").write_bytes(b"old config")
    (game / "eldorado.dxvk-cache").write_bytes(b"large local cache" * 20000)
    before = snapshot(game)
    body = b"s" * 20
    entry = struct.pack("<I", (len(body) << 6) | 3) + hashlib.sha1(body, usedforsecurity=False).digest() + body
    payloads["eldorado.dxvk-cache"] += entry * 5000
    manifest = make_manifest("a" * 40, payloads, "larger cache")
    engine = installer(game, client)
    original_prepare, original_copy = engine._prepare, module.atomic_copy
    prepared = False
    def prepare(*args):
        nonlocal prepared
        result = original_prepare(*args)
        prepared = True
        return result
    def limited_copy(source, target):
        if prepared and target.parent == game and source.stat().st_size > 64 * 1024:
            raise OSError(errno.ENOSPC, "64 KiB free after backups")
        original_copy(source, target)
    monkeypatch.setattr(engine, "_prepare", prepare)
    monkeypatch.setattr(module, "atomic_copy", limited_copy)
    with pytest.raises(UpdaterError, match="previous files were restored"):
        engine.install(manifest)
    assert snapshot(game) == before
    assert not engine.store.journal_path.exists()
    engine.recover()


def test_reopen_cleans_prejournal_backups_and_partial_temp_files(game, client, manifest, monkeypatch):
    engine = installer(game, client)
    engine.install(manifest)
    original_commit = engine._commit
    keep = engine.store.state()["transaction_id"]
    original = snapshot(game)
    def crash(folder, record, actions):
        (folder / "before" / ".dxvk-writing-interrupted").write_bytes(b"partial backup")
        (folder / ".writing-interrupted").write_bytes(b"partial record")
        raise KeyboardInterrupt("after backups, before journal")
    monkeypatch.setattr(engine, "_commit", crash)
    with pytest.raises(KeyboardInterrupt):
        engine.install(manifest, repair=True)
    assert len(list(engine.store.transactions.iterdir())) == 2
    Installer(game, None, game_guard=lambda root: None).recover()
    assert [p.name for p in engine.store.transactions.iterdir()] == [keep]
    assert snapshot(game) == original
    monkeypatch.setattr(engine, "_commit", original_commit)
    engine.restore()
    assert all(data is None for data in snapshot(game).values())


def test_failed_recovery_keeps_backups_until_game_closes(game, client, manifest, monkeypatch):
    engine = installer(game, client)
    original_copy = module.atomic_copy
    def crash(source, target):
        if target == game / "dxvk.conf":
            raise KeyboardInterrupt("after first replacement")
        original_copy(source, target)
    monkeypatch.setattr(module, "atomic_copy", crash)
    with pytest.raises(KeyboardInterrupt):
        engine.install(manifest)
    monkeypatch.setattr(module, "atomic_copy", original_copy)
    transactions = {p.name for p in engine.store.transactions.iterdir()}
    def running(root):
        raise UpdaterError("Close ElDewrito")
    with pytest.raises(UpdaterError, match="Close"):
        installer(game, None, running).recover()
    assert engine.store.journal_path.exists()
    assert {p.name for p in engine.store.transactions.iterdir()} == transactions
    engine.recover()
    assert not engine.store.journal_path.exists()


def test_repair_preserves_previous_revision_and_separate_local_backup(game, client, manifest, payloads):
    engine = installer(game, client)
    engine.install(manifest)
    payloads["dxvk.conf"] = b"dxvk.hud = fps\n"
    second = make_manifest("b" * 40, payloads, "second")
    engine.install(second)
    checkpoint = engine.store.state()["restore_id"]
    (game / "dxvk.conf").write_bytes(b"my local changes")
    engine.install(second, repair=True)
    state = engine.store.state()
    assert state["restore_id"] == checkpoint != state["repair_id"]
    backup = engine.store.tx_path(state["repair_id"]) / "before" / "dxvk.conf"
    assert backup.read_bytes() == b"my local changes"
    engine.recover()
    engine.restore()
    status = engine.check(second)
    assert status.installed.patch_id == manifest.patch_id
    assert status.undo_restore and status.suppressed
    assert backup.read_bytes() == b"my local changes"
    engine.restore()
    status = engine.check(second)
    assert status.installed.patch_id == second.patch_id
    assert not status.undo_restore and not status.suppressed


def test_suppression_clears_when_different_patch_is_offered(game, client, manifest, payloads):
    engine = installer(game, client)
    engine.install(manifest)
    engine.restore()
    assert engine.check(manifest).suppressed
    payloads["dxvk.conf"] += b"dxvk.numCompilerThreads = 1\n"
    third = make_manifest("c" * 40, payloads, "third")
    assert not engine.check(third).suppressed
    assert not engine.check(manifest).suppressed


def test_legacy_state_migration_preserves_restore_and_blocks_old_schema(game, client, manifest):
    import json
    engine = installer(game, client)
    engine.install(manifest)
    current = engine.store.state()
    legacy_manifest = manifest.to_dict()
    legacy_manifest.update(schema_version=1)
    for key in ("title", "minimum_updater"):
        legacy_manifest.pop(key)
    legacy = {"schema_version": 1, "manifest": legacy_manifest, "transaction_id": current["transaction_id"]}
    engine.store.state_path.write_text(json.dumps(legacy))
    engine.recover()
    state = engine.store.state()
    assert state["schema_version"] == 2
    assert state["restore_id"] == legacy["transaction_id"]
    assert json.loads((engine.store.meta / "installed-1.0-backup.json").read_text()) == legacy
    engine.restore()
    assert all(value is None for value in snapshot(game).values())


def test_failed_repair_preserves_both_previous_checkpoints(game, client, manifest, monkeypatch):
    engine = installer(game, client)
    engine.install(manifest)
    engine.install(manifest, repair=True)
    old_state = engine.store.state()
    real_copy = module.atomic_copy
    failed = False
    def fail(source, target):
        nonlocal failed
        if target == game / "dxvk.conf" and not failed:
            failed = True
            raise PermissionError("locked")
        real_copy(source, target)
    monkeypatch.setattr(module, "atomic_copy", fail)
    with pytest.raises(UpdaterError, match="previous files were restored"):
        engine.install(manifest, repair=True)
    assert engine.store.state() == old_state
    for transaction in engine._retained(old_state):
        assert engine.store.tx_path(transaction).exists()


def test_interrupted_legacy_journal_recovers_before_state_migration(game, client, manifest, monkeypatch):
    import json
    engine = installer(game, client)
    (game / "d3d9.dll").write_bytes(b"original wrapper")
    before = snapshot(game)
    original_copy = module.atomic_copy
    def crash(source, target):
        if target == game / "dxvk.conf":
            raise KeyboardInterrupt()
        original_copy(source, target)
    monkeypatch.setattr(module, "atomic_copy", crash)
    with pytest.raises(KeyboardInterrupt):
        engine.install(manifest)
    monkeypatch.setattr(module, "atomic_copy", original_copy)
    pointer = json.loads(engine.store.journal_path.read_text())
    record_path = engine.store.tx_path(pointer["transaction_id"]) / "record.json"
    record = json.loads(record_path.read_text())
    record["schema_version"] = 1
    desired = record["desired_state"]
    for key in ("restore_id", "repair_id", "undo_restore", "suppressed_patch"):
        desired.pop(key)
    desired["schema_version"] = 1
    desired["manifest"]["schema_version"] = 1
    desired["manifest"].pop("title")
    desired["manifest"].pop("minimum_updater")
    record_path.write_text(json.dumps(record))
    engine.recover()
    assert snapshot(game) == before
    assert not engine.store.journal_path.exists()


def test_committed_repair_crash_keeps_revision_and_repair_points(game, client, manifest, monkeypatch):
    engine = installer(game, client)
    engine.install(manifest)
    original_id = engine.store.state()["restore_id"]
    original_save = Store.save_state
    def crash(store, state):
        original_save(store, state)
        raise KeyboardInterrupt()
    monkeypatch.setattr(Store, "save_state", crash)
    with pytest.raises(KeyboardInterrupt):
        engine.install(manifest, repair=True)
    monkeypatch.setattr(Store, "save_state", original_save)
    engine.recover()
    state = engine.store.state()
    assert state["restore_id"] == original_id
    assert state["repair_id"] != original_id
    assert {p.name for p in engine.store.transactions.iterdir()} == engine._retained(state)


def test_first_explicit_repair_exposes_its_backup(game, client, manifest):
    (game / "dxvk.conf").write_bytes(b"personal manual installation")
    engine = installer(game, client)
    engine.install(manifest, repair=True)
    status = engine.check(manifest)
    assert status.repair_id == engine.store.state()["restore_id"]
    assert (engine.store.tx_path(status.repair_id) / "before" / "dxvk.conf").read_bytes() == b"personal manual installation"
