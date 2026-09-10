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


@pytest.mark.parametrize("name", ["src/app.py", ".github/workflows/build.yml", ".env", "settings.json", "tests/test_engine.py",
                                  "APP.PY", "../outside.exe", "file.txt:payload", "a\\b.txt", "SETTINGS.JSON"])
def test_release_audit_rejects_source_and_unsafe_paths(tmp_path, name):
    archive = tmp_path / "build.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("App/ElDewritoDXVKUpdater.exe", b"synthetic")
        output.writestr("App/" + name, b"must not publish")
    if "\\" in name:
        # ZipInfo normalizes backslashes on Windows; create an actual malformed ZIP name.
        archive.write_bytes(archive.read_bytes().replace(b"App/a/b.txt", b"App/a\\b.txt"))
    with pytest.raises(ValueError):
        audit_archive(archive)


def test_release_audit_rejects_case_collisions(tmp_path):
    archive = tmp_path / "build.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("App/ElDewritoDXVKUpdater.exe", b"synthetic")
        output.writestr("App/readme.txt", b"one")
        output.writestr("App/README.TXT", b"two")
    with pytest.raises(ValueError):
        audit_archive(archive)


def test_release_never_publishes_unreviewed_existing_draft_assets(tmp_path):
    import hashlib
    from dxvk_updater import __version__
    from scripts.publish_release import publish_release
    archive = tmp_path / f"ElDewritoDXVKUpdater-{__version__}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("App/ElDewritoDXVKUpdater.exe", b"synthetic")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n")
    class DraftAPI:
        def __init__(self):
            self.writes = []
        def call(self, method, path, data=None):
            if method == "GET":
                return {"draft": True, "id": 1, "assets": [{"name": "unreviewed.zip"}]}
            self.writes.append((method, path))
            return {"html_url": "released"}
        def upload_asset(self, release, file):
            self.writes.append(file.name)
    api = DraftAPI()
    with pytest.raises(UpdaterError, match="draft"):
        publish_release(api, tmp_path)
    assert api.writes == []


def test_release_tag_must_match_version(tmp_path, monkeypatch):
    from scripts.publish_release import publish_release
    monkeypatch.setenv("GITHUB_REF", "refs/tags/updater-v0.0.0")
    with pytest.raises(UpdaterError, match="tag"):
        publish_release(None, tmp_path)


def test_release_audit_detects_token_across_read_boundary(tmp_path):
    archive = tmp_path / "build.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("App/ElDewritoDXVKUpdater.exe", b"synthetic")
        # A deliberately fake credential shape, split across the scanner's blocks.
        output.writestr("App/data.bin", b"x" * (1024 * 1024 - 5) + b"github_" + b"pat_" + b"a" * 80)
    with pytest.raises(ValueError, match="credential"):
        audit_archive(archive)
