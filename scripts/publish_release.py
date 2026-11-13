from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from dxvk_updater import __version__
from dxvk_updater.model import HEX40, REPOSITORY, UpdaterError
from dxvk_updater.program_update import ProgramRelease, program_client
from dxvk_updater.model import version_tuple
from scripts.build_windows import audit_archive
from scripts.github_api import APIError, GitHub

SUMMARY = "More reliable interrupted program recovery, independent program checks, safer cancellation, and bounded helper-cache cleanup."


def publish_program(api, program):
    for attempt in range(3):
        parent = api.feed_head()
        prior_data = api.read_feed_file("updater.json", parent)
        if prior_data:
            prior = ProgramRelease.parse(prior_data)
            if version_tuple(prior.version) > version_tuple(program.version):
                return
            if prior.version == program.version:
                if (prior.sha256, prior.source_commit, prior.size) != (program.sha256, program.source_commit, program.size):
                    raise UpdaterError("The published program version already identifies different contents.")
                return
        try:
            api.write_feed({"updater.json": program.to_dict()}, parent, f"Publish updater {program.version}")
            return
        except APIError as exc:
            if exc.status not in (409, 422) or attempt == 2:
                raise


def release_program(release, source):
    prefix = f"ElDewritoDXVKUpdater-{__version__}-windows-x64.zip"
    assets = {asset["name"]: asset for asset in release.get("assets", [])}
    if len(release.get("assets", [])) != 2 or set(assets) != {prefix, prefix + ".sha256"}:
        raise UpdaterError("The public updater release must contain exactly its ZIP and checksum.")
    link = f"https://github.com/duckfudge/eldewrito-dxvk-updater/tree/{source}"
    if release.get("draft") or link not in release.get("body", ""):
        raise UpdaterError("The public updater release does not identify this source commit.")
    asset = assets[prefix]
    digest = asset.get("digest", "")
    if asset.get("state") != "uploaded" or not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise UpdaterError("GitHub has not confirmed the program asset checksum.")
    program = ProgramRelease.parse({"schema_version": 1, "version": __version__, "source_commit": source,
                                 "size": asset["size"], "sha256": digest[7:],
                                 "published_at": release.get("published_at"), "summary": SUMMARY})
    checksum = assets[prefix + ".sha256"]
    expected = f"{program.sha256}  {prefix}"
    variants = [(expected + ending).encode("ascii") for ending in ("\n", "\r\n")]
    if checksum.get("state") != "uploaded" or not any(checksum.get("size") == len(text) and checksum.get("digest") == "sha256:" + hashlib.sha256(text).hexdigest() for text in variants):
        raise UpdaterError("GitHub's public checksum asset does not match the program ZIP.")
    return program


def publish_release(api, directory):
    tag = f"updater-v{__version__}"
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/tags/") and ref != f"refs/tags/{tag}":
        raise UpdaterError("The workflow tag does not match the application version.")
    source = os.environ.get("GITHUB_SHA", "")
    if not HEX40.fullmatch(source):
        raise UpdaterError("Publish from the build workflow so a valid source commit is recorded.")
    archive = directory / f"ElDewritoDXVKUpdater-{__version__}-windows-x64.zip"
    checksum = archive.with_suffix(".zip.sha256")
    audit_archive(archive)
    expected = f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}"
    if checksum.read_text(encoding="ascii").strip() != expected:
        raise UpdaterError("The release checksum does not match the ZIP.")
    base = f"/repos/{REPOSITORY}/releases"
    body = ("Download the Windows x64 ZIP, extract the entire folder, and run ElDewritoDXVKUpdater.exe. "
                    "Select the folder containing eldorado.exe. Python is bundled; no separate installation is needed.\n\n"
                    "The app checks for DXVK patch updates when opened, asks before installing, and keeps a restore point. "
                    "This release is the updater application; DXVK patch releases remain separate.\n\n"
                    + SUMMARY + "\n\nVersion 1.0.x users must install this ZIP manually once. Future program updates are offered inside the app. "
                    "Extract to a fresh folder; the new runtime is _dxvk_updater. Mixed game folders are supported.\n\n"
                    "Clean Windows 10/11 VM and actual Windows DPI tests are deferred. The package is unsigned.\n\n"
                    f"[Updater source at {source[:12]}](https://github.com/duckfudge/eldewrito-dxvk-updater/tree/{source})")
    try:
        release = api.call("GET", f"{base}/tags/{tag}")
        if not release["draft"]:
            program = release_program(release, source)
            # A retry may rebuild different ZIP bytes. Re-audit the actual immutable public asset.
            client = program_client()
            try:
                with tempfile.TemporaryDirectory(prefix="dxvk-public-audit-") as temporary:
                    actual = Path(temporary) / archive.name
                    client.download_program(program, actual)
                    audit_archive(actual)
            finally:
                client.close()
            publish_program(api, program)
            return release["html_url"]
    except APIError as exc:
        if exc.status != 404:
            raise
        release = api.call("POST", base, {"tag_name": tag, "target_commitish": "main",
            "name": f"ElDewrito DXVK Updater {__version__}", "draft": True, "make_latest": "false",
            "body": body})
    if release.get("assets"):
        # Every published asset must pass this run's audit, including on retries.
        raise UpdaterError("The draft already contains assets. Review and remove the incomplete draft before retrying.")
    for file in (archive, checksum):
        api.upload_asset(release, file)
    result = api.call("PATCH", f"{base}/{release['id']}", {"draft": False, "make_latest": "false", "body": body})
    published = api.call("GET", f"{base}/tags/{tag}")
    program = release_program(published, source)
    if program.sha256 != expected.split()[0] or program.size != archive.stat().st_size:
        raise UpdaterError("GitHub's published program digest differs from the audited ZIP. The update feed was not changed.")
    publish_program(api, program)
    return result["html_url"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path, nargs="?", default=ROOT / "dist")
    args = parser.parse_args()
    try:
        api = GitHub()
        print(publish_release(api, args.directory))
        from scripts.publish_patch import publish
        print(publish(api)[0])
    except Exception as exc:
        print(f"Release failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
