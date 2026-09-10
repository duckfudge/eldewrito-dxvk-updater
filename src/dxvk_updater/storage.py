"""Local writes are bounded to a chosen game and recoverable transactions."""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path

from .model import FILES, Manifest, UpdaterError, read_json, sha256_file

ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")


def safe_regular(path: Path, *, directory=False, missing=True):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return
        raise UpdaterError(f"Required path is missing: {path.name}") from None
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise UpdaterError(f"Links and junctions are not supported here: {path.name}")
    if directory and not stat.S_ISDIR(info.st_mode) or not directory and not stat.S_ISREG(info.st_mode):
        raise UpdaterError(f"Unexpected file type: {path.name}")


def atomic_json(path: Path, value: dict):
    safe_regular(path)
    fd, temp_name = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        safe_regular(path)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def atomic_copy(source: Path, destination: Path):
    safe_regular(source, missing=False)
    safe_regular(destination)
    fd, temp_name = tempfile.mkstemp(prefix=".dxvk-writing-", dir=destination.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as target, source.open("rb") as original:
            shutil.copyfileobj(original, target)
            target.flush()
            os.fsync(target.fileno())
        safe_regular(destination)
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def validate_state(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"schema_version", "manifest", "transaction_id"} or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise UpdaterError("Installed patch records are damaged. Restore your .dxvk-updater backup.")
    transaction = value["transaction_id"]
    if transaction is not None and (not isinstance(transaction, str) or not ID_PATTERN.fullmatch(transaction)):
        raise UpdaterError("Invalid transaction identifier in installed patch records.")
    if value["manifest"] is not None:
        Manifest.parse(value["manifest"])
    return value


class Store:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve(strict=True)
        safe_regular(self.root, directory=True, missing=False)
        safe_regular(self.root / "eldorado.exe", missing=False)
        self.meta = self.root / ".dxvk-updater"
        safe_regular(self.meta, directory=True)
        self.meta.mkdir(exist_ok=True)
        self.transactions = self.meta / "transactions"
        safe_regular(self.transactions, directory=True)
        self.transactions.mkdir(exist_ok=True)
        self.state_path = self.meta / "installed.json"
        self.journal_path = self.meta / "journal.json"

    def game_file(self, name):
        if name not in FILES:
            raise UpdaterError("Unsupported patch destination.")
        target = self.root / name
        safe_regular(target)
        return target

    def tx_path(self, transaction):
        if not isinstance(transaction, str) or not ID_PATTERN.fullmatch(transaction):
            raise UpdaterError("Invalid transaction identifier.")
        target = self.transactions / transaction
        safe_regular(self.meta, directory=True, missing=False)
        safe_regular(self.transactions, directory=True, missing=False)
        safe_regular(target, directory=True)
        return target

    def state(self):
        safe_regular(self.state_path)
        if not self.state_path.exists():
            return None
        return validate_state(read_json(self.state_path.read_bytes()))

    def save_state(self, value):
        validate_state(value)
        if value is None:
            safe_regular(self.state_path)
            self.state_path.unlink(missing_ok=True)
        else:
            atomic_json(self.state_path, value)

    @contextlib.contextmanager
    def lock(self):
        path = self.meta / "lock"
        safe_regular(path)
        with path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise UpdaterError("Another updater is using this game folder. Close it and try again.") from exc
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def remove_transaction(self, transaction):
        # Only our fixed transaction layout is ever removed. No recursive deletion.
        target = self.tx_path(transaction)
        if not target.exists():
            return
        for subdir in ("before", "stage"):
            folder = target / subdir
            safe_regular(folder, directory=True)
            if folder.exists():
                for name in FILES:
                    file = folder / name
                    safe_regular(file)
                    file.unlink(missing_ok=True)
                folder.rmdir()
        record = target / "record.json"
        safe_regular(record)
        record.unlink(missing_ok=True)
        target.rmdir()

    def cleanup(self, keep):
        for entry in self.transactions.iterdir():
            if ID_PATTERN.fullmatch(entry.name) and entry.name != keep:
                try:
                    self.remove_transaction(entry.name)
                except (OSError, UpdaterError):
                    # Cleanup failure must never undo an already committed update.
                    pass


def ensure_game_closed(root: Path):
    import psutil
    target = os.path.normcase(str(root / "eldorado.exe"))
    for process in psutil.process_iter(["name"]):
        try:
            if (process.info["name"] or "").lower() != "eldorado.exe":
                continue
            try:
                actual = os.path.normcase(str(Path(process.exe()).resolve()))
            except psutil.AccessDenied:
                raise UpdaterError("ElDewrito is running. Close the game before changing the patch.") from None
            if actual == target:
                raise UpdaterError("Close ElDewrito before installing or restoring the patch.")
        except psutil.NoSuchProcess:
            continue


def validate_backup(entry, folder: Path):
    if not isinstance(entry, dict) or set(entry) != {"name", "existed", "size", "sha256"}:
        raise UpdaterError("The restore record is damaged.")
    name = entry["name"]
    if name not in FILES or type(entry["existed"]) is not bool:
        raise UpdaterError("The restore record contains an unsupported file.")
    if entry["existed"]:
        if type(entry["size"]) is not int or entry["size"] < 0:
            raise UpdaterError("Invalid backup size.")
        safe_regular(folder, directory=True, missing=False)
        source = folder / name
        safe_regular(source, missing=False)
        if source.stat().st_size != entry["size"] or sha256_file(source) != entry["sha256"]:
            raise UpdaterError(f"The backup of {name} is damaged. Restore from a separate backup.")
    elif entry["size"] is not None or entry["sha256"] is not None:
        raise UpdaterError("Invalid restore metadata.")
