from pathlib import Path

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
    engine.restore()
    assert (game / "dxvk.conf").read_bytes() == b"local config"


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
