# Publishing and maintenance

## Credentials and workflows

Source/workflows: `duckfudge/eldewrito-dxvk-updater` (public, 0BSD).
Patch files, feeds, and packaged downloads: `duckfudge/eldewrito-dxvk`.

Create a fine-grained token restricted to the patch repository with **Contents:
read/write**. Store it only as updater Actions secret `PUBLISH_TOKEN`, record its
expiry privately, and renew before expiry. Never commit or print it. Pull-request
and fork builds have no publishing access. The patch repository's push publisher
uses its short-lived `GITHUB_TOKEN` and requires no stored publisher credential.

The scheduled publisher checks at minutes 17 and 47 each hour; manual runs offer
a dry run. GitHub can delay schedules or disable them after 60 days of repository
inactivity. The separate patch push publisher remains available. Concurrency is
serialized per repository; cross-repository conflicts get three fresh attempts.
Feed writes preserve unrelated files and never force-push.

## Publish a patch

Stage compatible `d3d9.dll`, `dxvk.conf`, `eldorado.dxvk-cache`, and
`patch-notes.json` on a branch, test them together, then merge to patch `main`.
Notes have exactly `title` (1–100 characters) and `summary` (up to 2000 characters):

```json
{"title": "ElDewrito 0.7 DXVK patch", "summary": "Describe the player-visible change."}
```

Push, schedule, or manual publication validates the complete current main revision.
Only changed patch contents create patch revisions. Notes/documentation-only
commits do not republish an unchanged schema-2 patch; edit notes alongside the next
payload change. Invalid files preserve the previous published feed. The initial
schema-2 migration preserves an unchanged patch's ID and original publication date.
Do not edit generated `updates/manifest.json` or `updates/updater.json` manually.

Use the curated x86 DLL; the initial DLL came from release `0525`. Cache formats
8–15 and 17–18 are supported. A new format needs client validation support first.
Validation does not establish binary safety, GPU compatibility, or correct gameplay.
Do not force-push/delete published source history; clients use commit-pinned URLs.

## Publish the application

1. Update the version in `src/dxvk_updater/__init__.py` and release summary in
   `scripts/publish_release.py`; keep dependency pins consistent when changing them.
2. Run pytest and build on Windows using README commands. Smoke-test the complete
   extracted package, normal replacement/restart, failed startup, crash recovery,
   mixed folders, and administrator-assisted replacement.
3. Push matching tag `updater-vX.Y.Z`, or run Test and build updater with publish
   enabled. A tag/version mismatch aborts publication. Builds test Linux/Windows.
4. Verify the public ZIP/checksum, program feed, and patch feed, then update the
   player download link if needed. Latest DXVK patch release designation stays unchanged.

The release script audits the ZIP paths, ownership inventory, hashes, runtime,
source/settings exclusions, and recognizable GitHub credential patterns. This is
not a general secret scanner. Only ZIP/checksum assets are uploaded, first to a
draft and then published with `make_latest=false`. GitHub's uploaded asset digest
must match before the program feed is published. Source links pin `GITHUB_SHA`.
The program feed cannot downgrade versions or reuse a version for different bytes.

Existing published assets are never replaced. Retrying after a feed-write failure
requires the same source link, complete assets, and re-download/re-audit of the
actual public ZIP. An incomplete draft with assets must be reviewed and removed
before retrying; unreviewed draft assets are never published automatically.

The patch feed waits until the minimum updater is public. Schema 2 requires
1.1.0 and stops 1.0.x clients; no parallel legacy feed is kept. Players manually
install the first self-updating version once. Prefer a fresh folder. Never delete
generic `_internal`, README, or license files from a mixed game directory because
their ownership cannot be established. New runtime/documents use unique names.

Clean Windows 10/11 guests without Python and actual DPI/multiple-monitor checks
are deferred for 1.1.0 by the maintainer. Record that limitation in release notes;
package/runtime checks on a Python-equipped host do not replace those tests.
The package remains unsigned.

## Revert a patch

Restore the desired three files on a branch, merge a **new commit** to main, and
publish. Restored contents regain their previous content ID; commit hashes do not
imply version order. Players can Restore previous update while waiting. Repairs
preserve that previous-revision checkpoint and retain a separate latest repair
backup. Undo restore reverses restoration. Restoring away suppresses that offered
revision until different contents appear; explicit installation remains available.

## Failures and recovery

- HTTP 401/403: check token expiry, selected repo, Contents permission, and branch rules.
- Invalid patch: fix the full set and retry. Git LFS pointer text is rejected.
- Source moved: rerun or wait for the next schedule; a moving source is deferred.
- Feed conflict: retry after three automatic attempts; the feed is never forced.
- Patch file locks: close ElDewrito/lock holders, then reopen. Recovery works offline.
- Damaged backup: preserve metadata/files and use a separate backup. No unverified
  backup is restored. Legacy records recover before migration; do not run an old
  updater against migrated state or restore `installed-1.0-backup.json` alone.
- Personal edits before Repair: Details shows the retained transaction's `before`
  directory. `record.json` records file existence and hashes. With the game closed,
  manually copy desired verified files if recovering those edits.
- Program recovery: retain `DXVKUpdater-update.json` and its UUID cache in
  LocalAppData or protected ProgramData. Reopening dispatches the saved runner
  before importing Tk. Close other updater windows and retry. The broker can stop
  only its own newly started, unacknowledged child after a startup timeout so
  rollback can release file locks. It never kills the game or unrelated windows.
- Unstartable installed runtime: open the saved 1.1.1+ helper with no arguments at
  `%LOCALAPPDATA%\ElDewritoDXVKUpdater\program-updates\<job-id>\runner\ElDewritoDXVKUpdater.exe`.
  The pre-handoff Details/log entry records the path. A normal cached job identifies
  its target through `job.json`; an administrator restart copy uses `broker.json`
  to identify its protected job. Recovery works offline without loading the target
  runtime. Invalid/foreign markers and unverified backups are never guessed at.
- Cleanup failures after commit/rollback retain the terminal state and retry
  marker removal. Never roll back a committed program just to clear a marker.
- First upgrade from 1.1.0: the old cloned helper performs this replacement, so
  its recovery limitations still apply. Provide manual fresh-folder ZIP recovery.
  Old broker directories without metadata cannot safely be associated with an
  installation; automatic cleanup leaves those directories intact.
- UAC staging: only the installed app requests elevation; it independently fetches
  the fixed program feed/ZIP. Protected staging restricts writes to administrators
  and system, allows user read/execute, and restricts implicit owner rights.
  Unsafe pre-existing cache permissions are refused. Preparation errors are saved
  as `error.txt` inside the protected job when available. Cached user-writable
  helpers are never elevated; an unelevated broker restarts the interface.
  Cache descriptors use file-specific read/execute rights so verification matches
  the access list Windows stores. Write probes make one exclusive attempt; using
  tempfile here can retry an ACL denial hundreds of thousands of times on Windows.
  Preparation does not start a replacement worker. Handoff starts the broker,
  which may request a second UAC approval to start the protected worker. Closing
  during preparation safely abandons that prepared job. An inactive abandoned
  job with no recovery marker is collected on a later update.

Rotating logs/settings are local under `%LOCALAPPDATA%/ElDewritoDXVKUpdater`.
Completed program caches for an installation are collected before its next update;
pending recovery and running helpers are preserved. Nothing is uploaded automatically.
