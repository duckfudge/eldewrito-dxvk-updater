import json
import zipfile

import pytest

from dxvk_updater.model import UpdaterError
from scripts.build_windows import audit_archive
from scripts.publish_patch import publish


class API:
    def __init__(self, prior=None):
        self.prior = prior
        self.head = "a" * 40
        self.moves = False
        self.reads = 0
        self.writes = []
        self.conflict = False

    def source_head(self):
        self.reads += 1
        return "c" * 40 if self.moves and self.reads > 1 else self.head

    def feed_head(self):
        return "b" * 40 if self.prior else None

    def read_manifest(self, head):
        return json.dumps(self.prior.to_dict()).encode() if self.prior else None

    def write_manifest(self, manifest, parent):
        if self.conflict:
            raise UpdaterError("Non-fast-forward feed update")
        self.writes.append((manifest, parent))


def test_first_publish(payloads):
    api = API()
    status, manifest = publish(api, lambda commit, name: payloads[name])
    assert status == "published"
    assert api.writes == [(manifest, None)]


def test_documentation_commit_does_not_publish(payloads, manifest):
    api = API(manifest)
    api.head = "c" * 40
    status, result = publish(api, lambda commit, name: payloads[name])
    assert status == "unchanged"
    assert api.writes == []


def test_changed_file_and_manual_dry_run(payloads, manifest):
    api = API(manifest)
    payloads["dxvk.conf"] += b"dxvk.numCompilerThreads = 1\n"
    status, changed = publish(api, lambda commit, name: payloads[name], dry_run=True)
    assert status == "validated (dry run)"
    assert api.writes == []
    assert changed.summary == "Updated dxvk.conf."
    status, changed = publish(api, lambda commit, name: payloads[name])
    assert status == "published"
    assert api.writes[0][1] == "b" * 40


def test_invalid_patch_preserves_feed(payloads, manifest):
    api = API(manifest)
    payloads["d3d9.dll"] = b"bad binary"
    with pytest.raises(UpdaterError):
        publish(api, lambda commit, name: payloads[name])
    assert api.writes == []


def test_source_changes_mid_job_defers_publication(payloads):
    api = API()
    api.moves = True
    status, manifest = publish(api, lambda commit, name: payloads[name])
    assert "deferred" in status
    assert api.writes == []


def test_concurrent_publication_conflict_is_not_forced(payloads):
    api = API()
    api.conflict = True
    with pytest.raises(UpdaterError, match="Non-fast-forward"):
        publish(api, lambda commit, name: payloads[name])
    assert api.writes == []


@pytest.mark.parametrize("name", ["src/app.py", ".github/workflows/build.yml", ".env", "settings.json", "tests/test_engine.py"])
def test_release_audit_rejects_private_source(tmp_path, name):
    archive = tmp_path / "build.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("App/ElDewritoDXVKUpdater.exe", b"synthetic")
        output.writestr("App/" + name, b"must not publish")
    with pytest.raises(ValueError):
        audit_archive(archive)
