import copy
import hashlib
import json
import struct

import pytest

from dxvk_updater.model import FILES, Manifest, UpdaterError, make_manifest, read_json, validate_payload


def test_roundtrip_and_commit_pinning(manifest):
    result = Manifest.parse(json.dumps(manifest.to_dict()))
    assert result == manifest
    assert f"/{manifest.source_commit}/d3d9.dll" in result.download_url(result.files[0])


@pytest.mark.parametrize("mutation", [
    lambda m: m.update(schema_version=2),
    lambda m: m.update(schema_version=True),
    lambda m: m.update(source_commit="main"),
    lambda m: m.update(source_commit="../main"),
    lambda m: m.update(patch_id="0" * 64),
    lambda m: m.update(published_at="2026-01-01"),
    lambda m: m.update(summary="bad\x00summary"),
    lambda m: m.update(command="execute something"),
    lambda m: m["files"].pop(),
    lambda m: m["files"][0].update(name="../eldorado.exe"),
    lambda m: m["files"][0].update(name="C:\\Windows\\d3d9.dll"),
    lambda m: m["files"][0].update(name="D3D9.DLL"),
    lambda m: m["files"][0].update(size=-1),
    lambda m: m["files"][0].update(size=True),
    lambda m: m["files"][0].update(size=1024**3),
    lambda m: m["files"][0].update(sha256="unknown"),
    lambda m: m["files"][0].update(url="https://example.org/payload"),
    lambda m: m["files"].__setitem__(1, m["files"][0]),
])
def test_rejects_invalid_manifests(manifest, mutation):
    data = copy.deepcopy(manifest.to_dict())
    mutation(data)
    with pytest.raises(UpdaterError):
        Manifest.parse(data)


@pytest.mark.parametrize("text", ['{"key":1,"key":2}', '[]', '{invalid', 'x' * 70000], ids=["duplicate", "array", "malformed", "oversized"])
def test_invalid_json(text):
    with pytest.raises(UpdaterError):
        read_json(text)


def test_patch_revision_tracks_content_only(payloads):
    first = make_manifest("a" * 40, payloads, "one")
    second = make_manifest("b" * 40, payloads, "two")
    assert first.patch_id == second.patch_id
    payloads["dxvk.conf"] += b"dxvk.numCompilerThreads = 1\n"
    assert make_manifest("b" * 40, payloads, "two").patch_id != first.patch_id


@pytest.mark.parametrize("name,data", [
    ("d3d9.dll", b"not a DLL"),
    ("dxvk.conf", b"invalid line"),
    ("dxvk.conf", b"foo = \x00"),
    ("dxvk.conf", b"\xff\xfe"),
    ("eldorado.dxvk-cache", b"html"),
    ("eldorado.dxvk-cache", b""),
    ("d3d9.dll", b"version https://git-lfs.github.com/spec/v1\n"),
])
def test_rejects_invalid_payloads(name, data):
    with pytest.raises(UpdaterError):
        validate_payload(name, data)


def test_rejects_64bit_dll(payloads):
    dll = bytearray(payloads["d3d9.dll"])
    struct.pack_into("<H", dll, 0x84, 0x8664)
    with pytest.raises(UpdaterError, match="32-bit"):
        validate_payload("d3d9.dll", bytes(dll))


@pytest.mark.parametrize("cut", [160, 512, 1023])
def test_rejects_truncated_pe_sections(payloads, cut):
    with pytest.raises(UpdaterError, match="32-bit"):
        validate_payload("d3d9.dll", payloads["d3d9.dll"][:cut])


@pytest.mark.parametrize("version", [8, 15, 17, 18])
def test_cache_record_integrity(version):
    body = b"s" * 20
    packed = (len(body) << (6 if version >= 17 else 8)) | 3
    data = (struct.pack("<4sIII", b"DXVK", version, 0, packed)
            + hashlib.sha1(body, usedforsecurity=False).digest() + body)
    validate_payload("eldorado.dxvk-cache", data)
    for damaged in (data[:4], data[:-1], data[:-1] + b"x", data + b"x"):
        with pytest.raises(UpdaterError, match="shader cache"):
            validate_payload("eldorado.dxvk-cache", damaged)


@pytest.mark.parametrize("version", [0, 7, 16, 19])
def test_rejects_unsupported_cache_format(version):
    with pytest.raises(UpdaterError, match="unsupported"):
        validate_payload("eldorado.dxvk-cache", struct.pack("<4sII", b"DXVK", version, 0))


def test_installed_schema_rejects_boolean():
    from dxvk_updater.storage import validate_state
    with pytest.raises(UpdaterError, match="damaged"):
        validate_state({"schema_version": True, "manifest": None, "transaction_id": None})
