"""Download and independently audit the public release without credentials."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from dxvk_updater import __version__
from scripts.build_windows import audit_archive

API = "https://api.github.com/repos/duckfudge/eldewrito-dxvk"


def read(url):
    request = urllib.request.Request(url, headers={"User-Agent": "DXVK-release-verification"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "public-download")
    output = parser.parse_args().output
    output.mkdir(parents=True, exist_ok=True)
    tag = f"updater-v{__version__}"
    release = json.loads(read(API + "/releases/tags/" + tag))
    archive_name = f"ElDewritoDXVKUpdater-{__version__}-windows-x64.zip"
    expected = {archive_name, archive_name + ".sha256"}
    assert not release["draft"] and {asset["name"] for asset in release["assets"]} == expected
    for asset in release["assets"]:
        (output / asset["name"]).write_bytes(read(asset["browser_download_url"]))
    archive = output / archive_name
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert (output / (archive_name + ".sha256")).read_text(encoding="ascii").strip() == f"{digest}  {archive_name}"
    audit_archive(archive)
    token_pattern = re.compile(rb"github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{30,}")
    with zipfile.ZipFile(archive) as bundle:
        assert not any(token_pattern.search(bundle.read(item)) for item in bundle.namelist() if not item.endswith("/"))
    # GitHub's automatic source archive belongs to the PUBLIC PATCH repository.
    with zipfile.ZipFile(io.BytesIO(read(release["zipball_url"]))) as source:
        names = {"/".join(name.split("/")[1:]) for name in source.namelist() if not name.endswith("/")}
        assert names == {"README.md", "d3d9.dll", "dxvk.conf", "eldorado.dxvk-cache"}, names
    latest = json.loads(read(API + "/releases/latest"))["tag_name"]
    assert not latest.startswith("updater-v")
    source_repo = json.loads(read("https://api.github.com/repos/duckfudge/eldewrito-dxvk-updater"))
    assert source_repo["private"] is False and source_repo["visibility"] == "public"
    result = {"result": "passed", "release": release["html_url"], "sha256": digest,
              "bytes": archive.stat().st_size, "latest_patch_release": latest,
              "public_source_archive": sorted(names), "source_repository_visibility": "public"}
    (ROOT / "build").mkdir(exist_ok=True)
    (ROOT / "build" / "public-release-audit.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
