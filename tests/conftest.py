import pytest
from dxvk_updater.demo import demo_payloads
from dxvk_updater.model import make_manifest


@pytest.fixture
def game(tmp_path):
    root = tmp_path / "Halo Online ü 日本語 with spaces"
    root.mkdir()
    (root / "eldorado.exe").write_bytes(b"synthetic game marker; never executed")
    return root


@pytest.fixture
def payloads():
    return demo_payloads()


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
