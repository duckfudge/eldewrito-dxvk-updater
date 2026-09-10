"""A journaled installer with the installed state as its commit marker."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from .model import FILES, MAX_SIZES, Manifest, UpdaterError, read_json, sha256_file
from .storage import (Store, atomic_copy, atomic_json, ensure_game_closed,
                      safe_regular, validate_backup, validate_state)


@dataclass(frozen=True)
class Status:
    kind: str
    installed: Manifest | None
    available: Manifest
    changed: tuple[str, ...]
    missing: tuple[str, ...]
    can_restore: bool


class Installer:
    def __init__(self, game: Path, client, emit=lambda message, fraction=None: None,
                 game_guard=ensure_game_closed):
        self.store = Store(game)
        self.client = client
        self.emit = emit
        self.game_guard = game_guard

    def _read_record(self, transaction):
        folder = self.store.tx_path(transaction)
        record_path = folder / "record.json"
        safe_regular(record_path, missing=False)
        record = read_json(record_path.read_bytes())
        if set(record) != {"schema_version", "id", "before_state", "desired_state", "entries"} or record["schema_version"] != 1 or record["id"] != transaction:
            raise UpdaterError("The recovery journal is damaged.")
        validate_state(record["before_state"])
        desired = validate_state(record["desired_state"])
        if desired is None or desired["transaction_id"] != transaction:
            raise UpdaterError("The recovery commit marker is damaged.")
        entries = record["entries"]
        if not isinstance(entries, list) or not 1 <= len(entries) <= len(FILES):
            raise UpdaterError("Invalid recovery file list.")
        names = []
        for entry in entries:
            validate_backup(entry, folder / "before")
            names.append(entry["name"])
        if len(names) != len(set(names)):
            raise UpdaterError("Duplicate recovery file.")
        return record

    def _rollback(self, record):
        self.game_guard(self.store.root)
        folder = self.store.tx_path(record["id"]) / "before"
        # Verify every backup before restoring any file.
        for entry in record["entries"]:
            validate_backup(entry, folder)
        for entry in record["entries"]:
            target = self.store.game_file(entry["name"])
            if entry["existed"]:
                atomic_copy(folder / entry["name"], target)
            else:
                target.unlink(missing_ok=True)
        self.store.save_state(record["before_state"])
        self.store.journal_path.unlink()
        self.store.remove_transaction(record["id"])

    def _recover(self):
        safe_regular(self.store.journal_path)
        if not self.store.journal_path.exists():
            return
        pointer = read_json(self.store.journal_path.read_bytes())
        if set(pointer) != {"transaction_id"}:
            raise UpdaterError("The recovery pointer is damaged.")
        transaction = pointer["transaction_id"]
        record = self._read_record(transaction)
        current = self.store.state()
        if current == record["desired_state"]:
            self.store.journal_path.unlink()
            self.store.cleanup(transaction)
            self.emit("Finished cleanup for the completed update.")
            return
        self.emit("Recovering the interrupted update…")
        self._rollback(record)
        self.emit("Your previous patch has been restored.")

    def recover(self):
        with self.store.lock():
            self._recover()

    def _status(self, available: Manifest, *, adopt=False):
        state = self.store.state()
        installed = Manifest.parse(state["manifest"]) if state and state["manifest"] else None
        missing = tuple(name for name in FILES if not self.store.game_file(name).exists())
        can_restore = bool(state and state["transaction_id"])
        if installed is None:
            matching = not missing and all(sha256_file(self.store.game_file(f.name)) == f.sha256 for f in available.files)
            if matching and adopt:
                self.store.save_state({"schema_version": 1, "manifest": available.to_dict(), "transaction_id": None})
                return Status("current", available, available, (), (), False)
            return Status("adopt" if matching else "install", None, available, FILES, missing, can_restore)
        old_hashes = {file.name: file.sha256 for file in installed.files}
        changed = tuple(f.name for f in available.files if old_hashes[f.name] != f.sha256)
        if changed:
            kind = "update"
        elif missing:
            kind = "repair"
        else:
            kind = "current"
        return Status(kind, installed, available, changed, missing, can_restore)

    def check(self, available: Manifest):
        with self.store.lock():
            self._recover()
            return self._status(available, adopt=True)

    def _prepare(self, names, desired_manifest):
        transaction = uuid.uuid4().hex
        folder = self.store.tx_path(transaction)
        folder.mkdir()
        (folder / "stage").mkdir()
        (folder / "before").mkdir()
        record = {"schema_version": 1, "id": transaction, "before_state": self.store.state(),
                  "desired_state": {"schema_version": 1, "manifest": desired_manifest,
                                    "transaction_id": transaction}, "entries": []}
        for name in names:
            source = self.store.game_file(name)
            existed = source.exists()
            entry = {"name": name, "existed": existed, "size": None, "sha256": None}
            if existed:
                if source.stat().st_size > MAX_SIZES[name]:
                    raise UpdaterError(f"Existing {name} is unexpectedly large. Back it up manually before continuing.")
                backup = folder / "before" / name
                atomic_copy(source, backup)
                entry.update(size=backup.stat().st_size, sha256=sha256_file(backup))
            record["entries"].append(entry)
        return folder, record

    def _commit(self, folder, record, actions):
        self.game_guard(self.store.root)
        # Recheck files against their backups after staging and before the first write.
        for entry in record["entries"]:
            current = self.store.game_file(entry["name"])
            if current.exists() != entry["existed"] or entry["existed"] and sha256_file(current) != entry["sha256"]:
                raise UpdaterError("A patch file changed while preparing the update. Close the game and try again.")
        atomic_json(folder / "record.json", record)
        atomic_json(self.store.journal_path, {"transaction_id": record["id"]})
        try:
            for i, (name, source) in enumerate(actions):
                self.game_guard(self.store.root)
                self.emit(f"Installing {name}…", 0.8 + 0.18 * i / len(actions))
                target = self.store.game_file(name)
                if source is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic_copy(source, target)
            self.store.save_state(record["desired_state"])
        except Exception as exc:
            try:
                self._rollback(record)
            except Exception as recovery:
                raise UpdaterError("Update interrupted. Close the game and reopen the updater to finish recovery. "
                                   "Keep the .dxvk-updater folder intact.") from recovery
            raise UpdaterError(f"Update failed; your previous files were restored. {exc}") from exc
        # The new state is now committed; a cleanup failure is recovered next launch.
        try:
            self.store.journal_path.unlink()
            self.store.cleanup(record["id"])
        except OSError:
            pass
        self.emit("Patch installed successfully.", 1.0)

    def install(self, manifest: Manifest, *, repair=False, cancelled=lambda: False):
        with self.store.lock():
            self._recover()
            self.game_guard(self.store.root)
            status = self._status(manifest)
            if status.kind == "adopt" and not repair:
                self.store.save_state({"schema_version": 1, "manifest": manifest.to_dict(), "transaction_id": None})
                return
            names = FILES if repair or status.installed is None else tuple(name for name in FILES if name in status.changed or name in status.missing)
            if not names:
                self.emit("Your patch is already up to date.", 1.0)
                return
            # Stage before backup so a network failure never starts an installation.
            staging_id = uuid.uuid4().hex
            staging = self.store.tx_path(staging_id)
            staging.mkdir()
            (staging / "stage").mkdir()
            try:
                files = [f for f in manifest.files if f.name in names]
                total, downloaded = sum(f.size for f in files), 0
                def progress(count):
                    nonlocal downloaded
                    downloaded += count
                    self.emit("Downloading patch files…", downloaded / total * 0.7)
                for file in files:
                    self.emit(f"Downloading {file.name}…")
                    self.client.download(manifest, file, staging / "stage" / file.name, progress, cancelled)
                if cancelled():
                    raise UpdaterError("Download cancelled. Your installed files were not changed.")
                self.game_guard(self.store.root)
                self.emit("Backing up affected files…", 0.75)
                folder, record = self._prepare(names, manifest.to_dict())
                self._commit(folder, record, [(name, staging / "stage" / name) for name in names])
            finally:
                # On recovery failure, the separate backup transaction remains intact.
                try:
                    self.store.remove_transaction(staging_id)
                except (OSError, UpdaterError):
                    pass

    def restore(self):
        with self.store.lock():
            self._recover()
            self.game_guard(self.store.root)
            current = self.store.state()
            if not current or not current["transaction_id"]:
                raise UpdaterError("There is no previous update to restore for this game folder.")
            previous = self._read_record(current["transaction_id"])
            previous_folder = self.store.tx_path(previous["id"]) / "before"
            old_state = previous["before_state"]
            old_manifest = old_state["manifest"] if old_state else None
            names = [entry["name"] for entry in previous["entries"]]
            folder, record = self._prepare(names, old_manifest)
            actions = [(entry["name"], previous_folder / entry["name"] if entry["existed"] else None)
                       for entry in previous["entries"]]
            self._commit(folder, record, actions)
            self.emit("Previous patch restored.", 1.0)
