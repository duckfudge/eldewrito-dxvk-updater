# Setup and maintenance

## One-time repository setup

1. `duckfudge/eldewrito-dxvk-updater` is **public** at the owner's request. Keep
   application source and workflow YAML here; patch files and player downloads
   remain in `duckfudge/eldewrito-dxvk`.
2. In `duckfudge/eldewrito-dxvk/main`, place the verified x86 `d3d9.dll` from the
   `0525` release next to the existing `dxvk.conf` and `eldorado.dxvk-cache`.
   Preserve the current config/cache rather than replacing them with older ZIP
   copies. The DLL's SHA-256 is
   `0349923a6618f2383451c170a0f24d9c8f328392179aca71826a853655982405`.
3. Create a fine-grained GitHub token owned by duckfudge, restricted to the public
   `eldewrito-dxvk` repository. Grant **Contents: read and write** only (Metadata
   read is automatic). Set a 90-day expiry and record the renewal date privately.
   It needs no access to the application's source repository.
4. In this repository's Actions secrets, save it as `PUBLISH_TOKEN`.
   Do not put it in an issue, source file, build artifact, command-line argument,
   or public repository. Renew the secret before its token expires.
5. Run **Publish patch feed** with `dry_run` selected. Confirm validation succeeds,
   then run with `dry_run` off. It creates the public `updates` branch containing
   only `manifest.json`.
6. Run **Test and build updater**. After reviewing its artifact, run it manually
   with **publish** selected to create public release `updater-v1.0.0`.

The built-in workflow token cannot write a different repository. `PUBLISH_TOKEN`
supplies the public repository permission; it is only passed to publishing steps.
The source checkout uses its own read-only workflow token and disables credential
persistence. Workflows are restricted to the canonical updater repository.
Pull-request builds never receive `PUBLISH_TOKEN`; publishing requires a maintainer
tag, a manual run, or the scheduled patch check.

The initial token, **ElDewrito DXVK Publisher**, expires **December 10, 2026**.
Renew it and replace the private `PUBLISH_TOKEN` secret before that date.

## Publish a patch change

Edit/upload one or more of the three patch files on public `main`. Commit a
compatible file set together when changes depend on one another. Within the next
scheduled run, the publisher validates the files and updates the public
feed. GitHub may delay scheduled jobs; use **Run workflow** to publish sooner.

The publisher generates the patch ID and checksums, so there is no version number
or checksum to edit manually. It pins file downloads to the inspected source
commit. README edits do not produce patches. Changing a file back to an earlier
value is supported and produces the corresponding content ID.

Do not force-push or delete published source history: clients use commit-pinned
download URLs. Do not hand-edit the generated manifest or rewrite `updates`.

## Publish a new updater

Update `__version__` in `src/dxvk_updater/__init__.py` and the matching version in
`pyproject.toml`. Commit and push a matching `updater-vX.Y.Z` tag, or run the build
workflow manually with publish enabled. Build dependencies are pinned in the two
requirements files; update the lock pins together when servicing the runtime.

The publisher audits the ZIP for accidental source/settings and checks its SHA-256.
It creates a draft public release, uploads the executable bundle and checksum,
then publishes it with `make_latest=false`. The latest DXVK patch release is not
changed. A released version is never overwritten. If upload fails, review and
remove the incomplete draft before retrying, or publish a new version.

Application source archives are publicly available from this repository. GitHub's
automatic archives on the binary release refer to the patch repository. Packaged
player ZIPs continue to exclude application source and credentials.

## Revert a bad patch

Restore the desired versions of the affected patch files on public `main` in a new
commit. Run **Publish patch feed**. Players will see a content change and can accept
the corrected patch. They can also use **Restore previous update** locally while
waiting. Application downgrade decisions are never inferred from commit hashes.

## Failures and support

- HTTP 401/403: check token expiry, repository selection, Contents write permission,
  and branch rules. No permission to application source is necessary for the publisher.
- Invalid DLL/config/cache: fix the public files and rerun; the previous manifest
  remains live. Git LFS pointer text cannot be distributed as a patch file.
- Non-fast-forward publication: another publication won the race. Rerun against
  current `main`; the publisher never force-updates the feed.
- Source changed during validation: the run defers publication; rerun or wait for
  the next scheduled run.
- No scheduled publication: inspect Actions status, usage limits, workflow enablement,
  and the last failure. A successful no-change check produces no new feed commit.
- Player file lock: close ElDewrito or the application holding the files, then retry.
  The updater never kills processes or silently elevates permissions.
- Interrupted install: reopen the updater with the game closed. Recovery precedes
  the network check. Keep the game's `.dxvk-updater` directory intact.
- Corrupt restore point: retain the existing files and metadata; recover from a
  separate backup. The app will not apply an unverified restore copy.

Player logs are local only, under `%LOCALAPPDATA%/ElDewritoDXVKUpdater` with rotation.
No telemetry or automatic log uploads are included.

## Repeat the deployment checks

Run `python scripts/smoke_live.py` to exercise the live patch in a newly created
synthetic game folder under `build/live-smoke`. It never executes the fixture's
game marker. Run `python scripts/verify_public_release.py` to download and audit
the released ZIP, checksum, asset list, patch source archive, public application
repository visibility, and latest patch release. These scripts need network access
but no publishing credential. Outputs and test fixtures stay under `build`/`dist`.

When releasing a newer application, update its download link in the public README.
The version-specific link keeps the public latest-release URL reserved for DXVK.
