"""The portable application's explicit file ownership and ZIP boundary."""
from __future__ import annotations

import re
import stat
import zipfile
from pathlib import Path

from .model import HEX64, UpdaterError, read_json, sha256_file, version_tuple
from .storage import safe_regular

EXE = "ElDewritoDXVKUpdater.exe"
INVENTORY = "DXVKUpdater-files.json"
RUNTIME = "_dxvk_updater"
ROOT_FILES = {EXE, "DXVKUpdater-README.txt", "DXVKUpdater-LICENSE.txt"}
ROOT_DIRS = {RUNTIME, "DXVKUpdater-LICENSES"}
MAX_ZIP = 64 * 1024**2
MAX_UNPACKED = 512 * 1024**2
MAX_MEMBERS = 4096


def owned_path(name):
    if not isinstance(name, str) or len(name) > 240 or "\\" in name or ":" in name or any(ord(c) < 32 for c in name):
        raise UpdaterError("Unsafe program file path.")
    parts = name.split("/")
    if any(part in {"", ".", ".."} or part.endswith((".", " ")) or
           re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part) for part in parts):
        raise UpdaterError("Unsafe program file path.")
    if name not in ROOT_FILES | {INVENTORY} and not (len(parts) > 1 and parts[0] in ROOT_DIRS):
        raise UpdaterError("The program archive contains an unowned file.")
    return name


def destination(root, name, *, create=False):
    owned_path(name)
    safe_regular(root, directory=True, missing=False)
    current = root
    for part in name.split("/")[:-1]:
        current /= part
        safe_regular(current, directory=True)
        if create:
            current.mkdir(exist_ok=True)
    target = root / name
    safe_regular(target)
    return target


def parse_inventory(data):
    obj = read_json(data, max_size=1024 * 1024)
    if set(obj) != {"schema_version", "version", "files"} or type(obj["schema_version"]) is not int or obj["schema_version"] != 1:
        raise UpdaterError("Invalid program file inventory.")
    version_tuple(obj["version"])
    rows = obj["files"]
    if not isinstance(rows, list) or not 2 <= len(rows) <= MAX_MEMBERS:
        raise UpdaterError("Invalid program file list.")
    seen, total = set(), 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "size", "sha256"}:
            raise UpdaterError("Invalid program file entry.")
        name = owned_path(row["path"])
        if name == INVENTORY or name.casefold() in seen:
            raise UpdaterError("Duplicate program file.")
        seen.add(name.casefold())
        if type(row["size"]) is not int or not 0 <= row["size"] <= MAX_ZIP or not isinstance(row["sha256"], str) or not HEX64.fullmatch(row["sha256"]):
            raise UpdaterError("Invalid program file checksum or size.")
        total += row["size"]
    if EXE.casefold() not in seen or f"{RUNTIME}/python313.dll".casefold() not in seen or total > MAX_UNPACKED:
        raise UpdaterError("The program runtime is missing or too large.")
    # A file cannot also be the parent directory of another member.
    for name in seen:
        if any("/".join(name.split("/")[:i]) in seen for i in range(1, len(name.split("/")))):
            raise UpdaterError("Conflicting program paths.")
    return obj


def read_inventory(root, *, verify=False):
    path = destination(root, INVENTORY)
    safe_regular(path, missing=False)
    if path.stat().st_size > 1024 * 1024:
        raise UpdaterError("The program file inventory is too large.")
    obj = parse_inventory(path.read_bytes())
    if verify:
        for row in obj["files"]:
            file = destination(root, row["path"])
            safe_regular(file, missing=False)
            if file.stat().st_size != row["size"] or sha256_file(file) != row["sha256"]:
                raise UpdaterError(f"Program file verification failed: {row['path']}. Extract a fresh updater ZIP.")
    return obj


def extract_bundle(archive, target, version):
    """Validate every member and hash before a staged package may be installed."""
    if archive.stat().st_size > MAX_ZIP:
        raise UpdaterError("The program ZIP is too large.")
    with zipfile.ZipFile(archive) as source:
        infos = source.infolist()
        if not 2 <= len(infos) <= MAX_MEMBERS + 1:
            raise UpdaterError("Invalid program archive size.")
        members, seen = {}, set()
        for info in infos:
            original = info.orig_filename
            prefix, sep, relative = original.partition("/")
            if not sep or prefix != "ElDewritoDXVKUpdater" or info.is_dir():
                raise UpdaterError("Unexpected program archive layout.")
            owned_path(relative)
            if relative.casefold() in seen or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK or info.flag_bits & 1:
                raise UpdaterError("Duplicate, linked, or encrypted program member.")
            if not 0 <= info.file_size <= MAX_ZIP:
                raise UpdaterError("Program archive member is too large.")
            members[relative] = info
            seen.add(relative.casefold())
        if INVENTORY not in members or members[INVENTORY].file_size > 1024 * 1024:
            raise UpdaterError("The program file inventory is missing.")
        inventory = parse_inventory(source.read(members[INVENTORY]))
        if inventory["version"] != version or set(members) != {row["path"] for row in inventory["files"]} | {INVENTORY}:
            raise UpdaterError("The program archive does not match its inventory or version.")
        if sum(i.file_size for i in infos) > MAX_UNPACKED:
            raise UpdaterError("The unpacked program is too large.")
        target.mkdir()
        for name, info in members.items():
            file = destination(target, name, create=True)
            with source.open(info) as src, file.open("xb") as dst:
                total = 0
                while chunk := src.read(1024 * 1024):
                    total += len(chunk)
                    if total > info.file_size:
                        raise UpdaterError("Program archive size mismatch.")
                    dst.write(chunk)
        read_inventory(target, verify=True)
        return inventory
