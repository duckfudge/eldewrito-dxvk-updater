"""Runs only in the private repository. Public output is manifest.json alone."""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dxvk_updater.model import FILES, MAX_SIZES, RAW_BASE, Manifest, UpdaterError, make_manifest
from scripts.github_api import GitHub


def read_source(commit, name):
    with urllib.request.urlopen(f"{RAW_BASE}/{commit}/{name}", timeout=30) as response:
        data = response.read(MAX_SIZES[name] + 1)
    return data


def publish(api, read_file=read_source, *, dry_run=False):
    # Called after the workflow acquires its concurrency slot, never using an old event SHA.
    source = api.source_head()
    parent = api.feed_head()
    prior_data = api.read_manifest(parent)
    prior = Manifest.parse(prior_data) if prior_data is not None else None
    payloads = {name: read_file(source, name) for name in FILES}
    manifest = make_manifest(source, payloads, "Curated DXVK patch for ElDewrito 0.7.")
    if prior and prior.patch_id == manifest.patch_id:
        return "unchanged", prior
    changed = [f.name for f in manifest.files if not prior or next(p.sha256 for p in prior.files if p.name == f.name) != f.sha256]
    manifest = make_manifest(source, payloads, "Updated " + ", ".join(changed) + ".")
    if api.source_head() != source:
        return "source changed; publication deferred to the next run", manifest
    if dry_run:
        return "validated (dry run)", manifest
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
