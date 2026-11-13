"""Publish a validated complete patch while preserving the program update feed."""
from __future__ import annotations

import argparse
import sys
import urllib.request
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dxvk_updater.model import FILES, MAX_SIZES, RAW_BASE, Manifest, UpdaterError, make_manifest, read_json, version_tuple
from dxvk_updater.program_update import ProgramRelease
from scripts.github_api import APIError, GitHub


def read_source(commit, name):
    with urllib.request.urlopen(f"{RAW_BASE}/{commit}/{name}", timeout=30) as response:
        data = response.read(MAX_SIZES.get(name, 64 * 1024) + 1)
    return data


def publish(api, read_file=read_source, *, dry_run=False):
    for attempt in range(3):
        try:
            return _publish(api, read_file, dry_run=dry_run)
        except APIError as exc:
            if exc.status not in (409, 422) or attempt == 2:
                raise


def _publish(api, read_file, *, dry_run):
    # Called after the workflow acquires its concurrency slot, never using an old event SHA.
    source = api.source_head()
    parent = api.feed_head()
    prior_data = api.read_manifest(parent)
    prior = Manifest.parse(prior_data) if prior_data is not None else None
    notes = read_json(read_file(source, "patch-notes.json"))
    if set(notes) != {"title", "summary"}:
        raise UpdaterError("patch-notes.json must contain a title and summary.")
    payloads = {name: read_file(source, name) for name in FILES}
    manifest = make_manifest(source, payloads, notes["summary"], notes["title"])
    if prior and prior.patch_id == manifest.patch_id and prior.schema_version == 2:
        return "unchanged", prior
    if prior and prior.patch_id == manifest.patch_id:
        manifest = replace(manifest, published_at=prior.published_at)
    if api.source_head() != source:
        return "source changed; publication deferred to the next run", manifest
    if dry_run:
        return "validated (dry run)", manifest
    program_data = api.read_feed_file("updater.json", parent)
    if program_data is None or version_tuple(ProgramRelease.parse(program_data).version) < version_tuple(manifest.minimum_updater):
        return "validated; waiting for the required updater release before changing the patch feed", manifest
    api.write_manifest(manifest, parent)
    return "published", manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        outcome, manifest = publish(GitHub(), dry_run=args.dry_run)
        print(f"{outcome}: {manifest.patch_id[:12]} from {manifest.source_commit[:12]}")
    except Exception as exc:
        print(f"Publication failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
