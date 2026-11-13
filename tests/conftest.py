import struct
import hashlib
import json
import zipfile

import pytest
from dxvk_updater.model import make_manifest


@pytest.fixture
def game(tmp_path):
    root = tmp_path / "Halo Online ü 日本語 with spaces"
    root.mkdir()
    (root / "eldorado.exe").write_bytes(b"synthetic game marker; never executed")
    return root


@pytest.fixture
def payloads():
    # A synthetic PE image with one complete section; never loaded or executed.
    dll = bytearray(1024)
    dll[:2] = b"MZ"
    struct.pack_into("<I", dll, 0x3C, 0x80)
    dll[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", dll, 0x84, 0x14C, 1)
    struct.pack_into("<HH", dll, 0x94, 224, 0x2002)
    struct.pack_into("<H", dll, 0x98, 0x10B)
    struct.pack_into("<I", dll, 0xD4, 512)  # SizeOfHeaders
    section = 0x98 + 224
    dll[section:section+8] = b".text\0\0\0"
    struct.pack_into("<II", dll, section + 16, 512, 512)
    return {"d3d9.dll": bytes(dll), "dxvk.conf": b"dxvk.hud = 0\n",
            "eldorado.dxvk-cache": struct.pack("<4sII", b"DXVK", 18, 0)}


@pytest.fixture
def manifest(payloads):
    return make_manifest("a" * 40, payloads, "First patch")


class MemoryClient:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []
        self.fail_at = None

    def download(self, manifest, file, target, progress, cancelled):
        self.calls.append(file.name)
        if file.name == self.fail_at:
            raise OSError("Injected connection failure")
        target.write_bytes(self.payloads[file.name])
        progress(file.size)


@pytest.fixture
def client(payloads):
    return MemoryClient(payloads)


@pytest.fixture
def bundle_factory():
    def create(root, version="1.1.0", content=b"old program"):
        root.mkdir(parents=True)
        (root / "_dxvk_updater").mkdir()
        files = {"ElDewritoDXVKUpdater.exe": content,
                 "_dxvk_updater/python313.dll": b"synthetic runtime " + content,
                 "DXVKUpdater-README.txt": b"player documentation"}
        for name, data in files.items():
            (root / name).write_bytes(data)
        inventory = {"schema_version": 1, "version": version,
                     "files": [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                               for name, data in files.items()]}
        (root / "DXVKUpdater-files.json").write_text(json.dumps(inventory), encoding="utf-8")
        return root
    return create


@pytest.fixture
def zip_bundle():
    def create(bundle, target):
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in bundle.rglob("*"):
                if path.is_file():
                    archive.write(path, "ElDewritoDXVKUpdater/" + path.relative_to(bundle).as_posix())
        return target
    return create
