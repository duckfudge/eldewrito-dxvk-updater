"""The deliberately small public patch protocol. No executable instructions."""
from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY = "duckfudge/eldewrito-dxvk"
RAW_BASE = f"https://raw.githubusercontent.com/{REPOSITORY}"
MANIFEST_URL = f"{RAW_BASE}/updates/manifest.json"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
FILES = ("d3d9.dll", "dxvk.conf", "eldorado.dxvk-cache")
MAX_SIZES = {"d3d9.dll": 64 * 1024**2, "dxvk.conf": 256 * 1024,
             "eldorado.dxvk-cache": 64 * 1024**2}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")


class UpdaterError(Exception):
    """An actionable error suitable for presentation to a player."""


def read_json(data: bytes | str) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    try:
        if len(data) > 64 * 1024:
            raise ValueError("JSON exceeds size limit")
        result = json.loads(data, object_pairs_hook=unique)
        if not isinstance(result, dict):
            raise ValueError("Expected object")
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise UpdaterError("The update metadata is not valid JSON.") from exc


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@dataclass(frozen=True)
class PatchFile:
    name: str
    size: int
    sha256: str

    def to_dict(self):
        return {"name": self.name, "size": self.size, "sha256": self.sha256}


def patch_id(files: tuple[PatchFile, ...]) -> str:
    canonical = "".join(f"{f.name}:{f.sha256}\n" for f in sorted(files, key=lambda f: f.name))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class Manifest:
    source_commit: str
    patch_id: str
    published_at: str
    summary: str
    files: tuple[PatchFile, ...]

    @classmethod
    def parse(cls, data: bytes | str | dict) -> "Manifest":
        obj = read_json(data) if not isinstance(data, dict) else data
        try:
            if type(obj.get("schema_version")) is not int or obj["schema_version"] != 1:
                raise UpdaterError("This feed needs a newer updater. Download it from GitHub Releases.")
            if set(obj) != {"schema_version", "source_commit", "patch_id", "published_at", "summary", "files"}:
                raise ValueError("Unexpected manifest fields")
            commit, revision = obj["source_commit"], obj["patch_id"]
            if not isinstance(commit, str) or not HEX40.fullmatch(commit):
                raise ValueError("Invalid source commit")
            if not isinstance(revision, str) or not HEX64.fullmatch(revision):
                raise ValueError("Invalid patch ID")
            stamp = obj["published_at"]
            if not isinstance(stamp, str) or len(stamp) > 40 or datetime.fromisoformat(stamp).tzinfo is None:
                raise ValueError("Invalid timestamp")
            summary = obj["summary"]
            if not isinstance(summary, str) or len(summary) > 2000 or any(ord(c) < 32 and c not in "\n\t" for c in summary):
                raise ValueError("Invalid summary")
            rows = obj["files"]
            if not isinstance(rows, list) or len(rows) != len(FILES):
                raise ValueError("Expected exactly three files")
            parsed = []
            for row in rows:
                if not isinstance(row, dict) or set(row) != {"name", "size", "sha256"}:
                    raise ValueError("Invalid file entry")
                name, size, digest = row["name"], row["size"], row["sha256"]
                if not isinstance(name, str) or name not in FILES:
                    raise ValueError("Unsupported filename")
                if type(size) is not int or not 0 < size <= MAX_SIZES[name]:
                    raise ValueError("Invalid file size")
                if not isinstance(digest, str) or not HEX64.fullmatch(digest):
                    raise ValueError("Invalid checksum")
                parsed.append(PatchFile(name, size, digest))
            if {f.name for f in parsed} != set(FILES):
                raise ValueError("Duplicate or missing patch file")
            files = tuple(sorted(parsed, key=lambda f: FILES.index(f.name)))
            if patch_id(files) != revision:
                raise ValueError("Patch ID does not match contents")
            return cls(commit, revision, stamp, summary, files)
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise UpdaterError(f"Invalid update metadata: {exc}.") from exc

    def to_dict(self):
        return {"schema_version": 1, "source_commit": self.source_commit,
                "patch_id": self.patch_id, "published_at": self.published_at,
                "summary": self.summary, "files": [f.to_dict() for f in self.files]}

    def download_url(self, file: PatchFile) -> str:
        if file not in self.files:
            raise UpdaterError("This file is not part of the selected patch.")
        return f"{RAW_BASE}/{self.source_commit}/{file.name}"


def validate_payload(name: str, data: bytes):
    if name not in FILES or not 0 < len(data) <= MAX_SIZES[name]:
        raise UpdaterError(f"Invalid size for {name}.")
    if data.startswith(b"version https://git-lfs.github.com/spec/"):
        raise UpdaterError(f"{name} is a Git LFS pointer, not the patch file.")
    if name == "d3d9.dll":
        try:
            offset = struct.unpack_from("<I", data, 0x3C)[0]
            if data[:2] != b"MZ" or data[offset:offset+4] != b"PE\0\0":
                raise ValueError()
            machine = struct.unpack_from("<H", data, offset + 4)[0]
            sections = struct.unpack_from("<H", data, offset + 6)[0]
            optional_size = struct.unpack_from("<H", data, offset + 20)[0]
            characteristics = struct.unpack_from("<H", data, offset + 22)[0]
            magic = struct.unpack_from("<H", data, offset + 24)[0]
            if offset < 64 or machine != 0x14C or magic != 0x10B or characteristics & 0x2002 != 0x2002:
                raise ValueError()
            table = offset + 24 + optional_size
            header_size = struct.unpack_from("<I", data, offset + 24 + 60)[0]
            if optional_size < 96 or not 1 <= sections <= 96 or not table + 40 * sections <= header_size <= len(data):
                raise ValueError()
            for i in range(sections):
                raw_size, raw_offset = struct.unpack_from("<II", data, table + 40 * i + 16)
                if raw_size and (raw_offset < header_size or raw_offset + raw_size > len(data)):
                    raise ValueError()
        except (struct.error, ValueError):
            raise UpdaterError("d3d9.dll must be a valid 32-bit Windows DLL.") from None
    elif name == "dxvk.conf":
        try:
            text = data.decode("utf-8-sig")
            if "\0" in text or not text.strip():
                raise ValueError()
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith(("#", ";")) and not (line.startswith("[") and line.endswith("]")):
                    key, separator, value = line.partition("=")
                    if not separator or not re.fullmatch(r"[A-Za-z0-9_.]+", key.strip()):
                        raise ValueError()
        except (UnicodeError, ValueError):
            raise UpdaterError("dxvk.conf must be a readable UTF-8 DXVK configuration.") from None
    else:
        validate_cache(data)


def validate_cache(data: bytes):
    # DXVK v2.5.3's state-cache format: 12-byte file header, then length/hash/data.
    # Check framing and stored integrity, not GPU compatibility or pipeline semantics.
    try:
        magic, version, _ = struct.unpack_from("<4sII", data)
        if magic != b"DXVK" or not 8 <= version <= 18 or version == 16:
            raise ValueError()
        offset = 12
        while offset < len(data):
            header = struct.unpack_from("<I", data, offset)[0]
            size = header >> (6 if version >= 17 else 8)
            end = offset + 24 + size
            if not 0 < size <= 1024 or end > len(data):
                raise ValueError()
            expected = data[offset + 4:offset + 24]
            if hashlib.sha1(data[offset + 24:end], usedforsecurity=False).digest() != expected:
                raise ValueError()
            offset = end
    except (struct.error, ValueError):
        raise UpdaterError("The shader cache is truncated, damaged, or uses an unsupported format (supported: 8–15, 17–18).") from None


def make_manifest(commit: str, payloads: dict[str, bytes], summary: str) -> Manifest:
    if set(payloads) != set(FILES):
        raise UpdaterError("The patch must contain all three DXVK files.")
    files = []
    for name in FILES:
        data = payloads[name]
        validate_payload(name, data)
        files.append(PatchFile(name, len(data), hashlib.sha256(data).hexdigest()))
    return Manifest.parse({"schema_version": 1, "source_commit": commit,
                           "patch_id": patch_id(tuple(files)),
                           "published_at": datetime.now(timezone.utc).isoformat(),
                           "summary": summary[:2000], "files": [f.to_dict() for f in files]})
