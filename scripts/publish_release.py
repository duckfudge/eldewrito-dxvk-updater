from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from dxvk_updater import __version__
from dxvk_updater.model import REPOSITORY, UpdaterError
from scripts.build_windows import audit_archive
from scripts.github_api import APIError, GitHub


def publish_release(api, directory):
    tag = f"updater-v{__version__}"
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/tags/") and ref != f"refs/tags/{tag}":
        raise UpdaterError("The workflow tag does not match the application version.")
    archive = directory / f"ElDewritoDXVKUpdater-{__version__}-windows-x64.zip"
    checksum = archive.with_suffix(".zip.sha256")
    audit_archive(archive)
    expected = f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}"
    if checksum.read_text(encoding="ascii").strip() != expected:
        raise UpdaterError("The release checksum does not match the ZIP.")
    base = f"/repos/{REPOSITORY}/releases"
    try:
        release = api.call("GET", f"{base}/tags/{tag}")
        if not release["draft"]:
            raise UpdaterError("This updater version is already published. Increment the application version to release changes.")
    except APIError as exc:
        if exc.status != 404:
            raise
        release = api.call("POST", base, {"tag_name": tag, "target_commitish": "main",
            "name": f"ElDewrito DXVK Updater {__version__}", "draft": True, "make_latest": "false",
            "body": "Download the Windows x64 ZIP, extract the entire folder, and run ElDewritoDXVKUpdater.exe. "
                    "Select the folder containing eldorado.exe. Python is bundled; no separate installation is needed.\n\n"
                    "The app checks for DXVK patch updates when opened, asks before installing, and keeps a restore point. "
                    "This release is the updater application; DXVK patch releases remain separate."})
    if release.get("assets"):
        # Every published asset must pass this run's audit, including on retries.
        raise UpdaterError("The draft already contains assets. Review and remove the incomplete draft before retrying.")
    for file in (archive, checksum):
        api.upload_asset(release, file)
    result = api.call("PATCH", f"{base}/{release['id']}", {"draft": False, "make_latest": "false"})
    return result["html_url"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path, nargs="?", default=ROOT / "dist")
    args = parser.parse_args()
    try:
        print(publish_release(GitHub(), args.directory))
    except Exception as exc:
        print(f"Release failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
