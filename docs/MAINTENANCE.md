# Publishing and maintenance

## Publishing credentials

Application source and workflows live in `duckfudge/eldewrito-dxvk-updater`.
Patch files, generated metadata, and player downloads live in
`duckfudge/eldewrito-dxvk`.

1. Create a fine-grained GitHub token restricted to the **patch repository** with
   **Contents: read and write**. It does not need access to the updater repository.
2. Store it in the updater repository's Actions secrets as `PUBLISH_TOKEN`.
   Choose an expiration date, record the renewal date privately, and replace the
   secret before it expires. Never commit the token or pass it on a command line.
3. Run **Publish patch feed** with **dry_run** enabled to validate the current
   patch. Turn dry run off to publish its manifest to the `updates` branch.

The default workflow token cannot write another repository. The publisher secret
is passed only to publishing steps in the canonical updater repository. Source
checkout is read-only and does not persist credentials. Pull-request builds and
fork builds can run tests and packaging without publishing access.

## Publish a patch change

Commit compatible versions of `d3d9.dll`, `dxvk.conf`, and `eldorado.dxvk-cache`
together on patch `main`. The publisher checks at minutes 17 and 47 each hour;
use **Run workflow** to check sooner. GitHub can delay or disable scheduled runs,
so check Actions if a patch is not appearing.

The publisher calculates revision IDs and hashes automatically, pins downloads
to the source commit, and preserves the previous feed on validation failure.
Documentation changes and repeated checks do not create revisions. Publishing
jobs are serialized and inspect the current source when they start. A concurrent
feed update is rejected rather than overwritten.

Use the curated **x86** DLL, not an x64 DXVK DLL. The initial DLL came from the
patch repository's `0525` release. Validation checks PE structure, readable config,
and cache record integrity. Supported cache formats are 8–15 and 17–18; a new
format needs a validator update and a compatible updater release first. Validation
does not replace testing the complete patch in ElDewrito.

Do not force-push or delete published source history: clients use commit-pinned
URLs. Do not hand-edit `updates/manifest.json`.

## Publish an updater release

1. Update `__version__` in `src/dxvk_updater/__init__.py`; package metadata and release
   filenames derive from it. Keep dependency pins consistent in `pyproject.toml`
   and the requirements files when updating dependencies.
2. Run tests and build on Windows using the README commands. Test the extracted
   package on Windows 10/11 without Python, including high-DPI display settings.
3. Push a matching `updater-vX.Y.Z` tag, or run **Test and build updater** manually
   with **publish** enabled. A tag/version mismatch aborts publication.
4. Update the player download link in the patch repository's README if necessary.

The workflow tests Linux and Windows before building. The release script verifies
the ZIP checksum and audits its paths, duplicate names, source/settings exclusions,
and recognizable GitHub credential patterns. This is a packaging safeguard, not
a general-purpose secret scanner. It uploads a ZIP and SHA-256 file to a draft,
then publishes with `make_latest=false` so the latest DXVK patch designation stays
unchanged. An existing release is never replaced. Review and remove any incomplete
draft containing assets before retrying; existing draft assets are never published
without review. Application source remains available in this repository.

## Revert a bad patch

Restore the desired patch files on `main` in a **new commit** and run **Publish patch
feed**. Reverting file contents produces their earlier content ID and prompts
players whose installed upstream hashes differ. Players can also use **Restore
previous update** while waiting. Commit hashes are not used to infer version order.

## Troubleshooting

- **HTTP 401/403:** check token expiry, selected repository, Contents permission,
  and branch rules. Renew the Actions secret if needed.
- **Invalid patch:** fix the complete file set and rerun. Git LFS pointer text is
  rejected; ordinary GitHub files are required. The previous feed remains active.
- **Source changed during validation:** publication is deferred; rerun or wait
  for the next scheduled check.
- **Non-fast-forward feed update:** another publisher won the race; rerun against
  current source. The workflow does not force-update the feed.
- **File locks or permissions:** close ElDewrito and other programs holding the
  files, then retry. The app never kills a process or silently elevates itself.
- **Interrupted installation:** reopen with the game closed. Recovery works
  offline. Keep `.dxvk-updater` intact until recovery completes.
- **Damaged restore point:** retain the files and metadata and recover from a
  separate backup. Unverified backups are never restored.

Rotating player logs and the remembered folder are stored locally under
`%LOCALAPPDATA%/ElDewritoDXVKUpdater`. Logs are not uploaded automatically.
