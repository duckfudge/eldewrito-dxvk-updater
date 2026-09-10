# ElDewrito DXVK Updater

Python application and automation for `duckfudge/eldewrito-dxvk`.
The patch repository contains the patch and generated downloads; this public repository
contains all updater source, tests, build scripts, and publishing workflows.

## Run and build

Use Python 3.13 x64 on Windows:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe launcher.py
.\.venv\Scripts\python.exe scripts/build_windows.py
```

The packaged executable and ZIP appear in `dist`. The ZIP includes the Python
runtime, Tcl/Tk, CustomTkinter resources, and dependency license notices. Keep the
entire extracted folder together. `python launcher.py --preview` shows synthetic
UI data with all patch actions disabled. It does not modify game files.

## What players see

The updater remembers a selected folder containing `eldorado.exe`, checks the
public feed on startup, and offers **Install patch**, **Update now**, or **Later**.
Downloads and installation run on a worker thread; the UI receives queue events.
The app never runs a background service or starts/closes the game.

Only `d3d9.dll`, `dxvk.conf`, and `eldorado.dxvk-cache` are managed. An update replaces
files that changed upstream, plus any missing files. **Repair patch** replaces all
three published files. Every affected local copy is backed up. The growing local
shader cache and manual config edits do not by themselves generate update prompts.

## Publishing

See [setup and maintenance](docs/MAINTENANCE.md). The **Publish patch feed**
workflow checks the public `main` branch at minutes 17 and 47 each hour and can also
be run manually. It publishes a new manifest only when the three patch-file hashes
change. A failed validation leaves the existing feed in place.

The **Test and build updater** workflow runs tests and builds a Windows
package. Public release publication requires a version tag or an explicit manual
publish input. Releases in the patch repository contain packaged downloads.
Application source is available here. Workflows run only in the canonical
`duckfudge/eldewrito-dxvk-updater` repository. Publishing credentials remain in
Actions secrets and are never passed to pull-request builds or bundled in the app.

## Layout

- `src/dxvk_updater`: application UI, strict manifest protocol, bounded downloads,
  per-game settings, and journaled installation/recovery.
- `scripts`: patch publisher, Windows builder, and public binary publisher.
- `tests`: isolated fixtures and simulated failures; no real game installation is
  used or modified by the test suite.
- `.github/workflows`: scheduled publisher and Windows build workflow.

## Recovery model

Each game has a `.dxvk-updater` directory with `installed.json`, a lock, and
transaction directories. All downloads are staged and verified before backups.
Backups and their checksums are written before the recovery journal, which is
written before any game-file replacement. Saving the desired installed state is
the commit marker. A crash before that marker rolls the transaction back on next
startup; a crash after it only requires cleanup. Recovery works offline.

Windows cannot atomically replace three files at once. The journal makes partial
updates recoverable; don't delete `.dxvk-updater` during an update or recovery.
An unreadable or corrupted backup stops recovery with an actionable message.
The app refuses links/junctions in managed files or its metadata paths, uses a
per-game cross-process lock, and never accepts filenames or commands outside its
three-file allowlist.

The manifest's HTTPS transport, fixed repository, commit pinning, and SHA-256
checks protect download integrity. GitHub account and publishing-token access are
the trust boundary; hashes are not an independent publisher signature.

## Compatibility and validation

The target is Windows 10/11 x64 and the curated ElDewrito 0.7 DXVK package. DLL
validation requires an x86 PE DLL. The updater does not select an upstream DXVK
version or infer GPU compatibility from Vulkan instance versions.

Automated tests cover the protocol, per-file updates, manual installs, cache
growth, repair, restore, locks, download failures, crash boundaries, and publishing
races. Validate the packaged executable on a Windows 10 and Windows 11 machine
without Python, and visually check 100%, 150%, and 200% display scaling before
claiming compatibility on those configurations. See `docs/VALIDATION.md` for the
checks actually completed for this build.

Third-party dependencies retain their own licenses.
