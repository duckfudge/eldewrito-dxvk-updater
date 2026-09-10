# ElDewrito DXVK Updater

A Python desktop updater for the curated [ElDewrito DXVK patch](https://github.com/duckfudge/eldewrito-dxvk).
It checks for updates when opened, asks before installing, and keeps a restore point.
The target is ElDewrito 0.7 on Windows 10/11 x64; the game uses an **x86 DXVK DLL**.

## For players

Download an **ElDewritoDXVKUpdater-…-windows-x64.zip** from the patch repository's
[releases](https://github.com/duckfudge/eldewrito-dxvk/releases). Extract the entire
folder and run `ElDewritoDXVKUpdater.exe`. Python is included. Choose the folder
containing `eldorado.exe`, or put the updater beside it for automatic detection.

- **Update now** installs changed upstream files and replaces missing files.
- **Later** postpones installation; **Check again** refreshes the published patch.
- **Repair patch** downloads and replaces all three managed files, with backups.
- **Restore previous update** restores the last affected files, including which
  files originally did not exist. Restoration also works offline.

Only `d3d9.dll`, `dxvk.conf`, and `eldorado.dxvk-cache` are managed. Local cache
growth and configuration edits do not trigger repeated updates. Repair or an
upstream change to those files replaces the local copies after backing them up.
Close ElDewrito before installing or restoring. The app has no tray service,
telemetry, automatic installation, or publishing credentials.

## Development

Use Python 3.13 x64 on Windows:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe launcher.py
.\.venv\Scripts\python.exe scripts/build_windows.py
```

The one-folder PyInstaller build and ZIP appear in `dist/`. Keep all extracted
files together: Python, Tcl/Tk, CustomTkinter assets, and license notices are
included. `python launcher.py --game-dir "C:\Games\Halo Online"` selects a folder
explicitly. Source code is licensed under [0BSD](LICENSE); dependencies retain
their own licenses.

Tests use temporary synthetic game folders and never execute a game or contact
GitHub. CI tests on Linux and Windows and builds the Windows package. Run the
suite when changing update logic or publishing behavior; include regression tests
with bug fixes. Clean Windows 10/11 machines without Python and display scaling
at 100%, 150%, and 200% still require manual release testing.

## How updates work

The [publishing workflow](.github/workflows/publish-patch.yml) checks patch `main`
every 30 minutes and on manual runs. It validates all three files and writes
`updates/manifest.json` only when their contents change. Documentation-only commits
do not produce patch revisions. Scheduling can be delayed by GitHub.

The manifest identifies a source commit and contains filenames, sizes, SHA-256
hashes, a content-derived patch ID, a publication date, and a short summary. The
client downloads from that exact commit using a fixed GitHub repository. It
rejects arbitrary paths, URLs, commands, oversized files, and checksum mismatches.
DLL validation checks x86 PE headers and section bounds. Cache validation checks
record boundaries and stored hashes for formats 8–15 and 17–18, including the
current curated cache. These checks do not establish GPU compatibility or prove
that a binary is safe; the patch publisher remains trusted.

Each selected game has its own `.dxvk-updater` state and backups. All downloads are
verified before replacement. A per-game lock excludes concurrent updaters; a
journal supports rollback after errors or interrupted updates. Saving the installed
state commits a transaction. Recovery runs before the network check. Keep this
directory intact while updating or recovering. A damaged backup stops restoration
before any file is changed. Local backups can exceed the upstream download limits.

HTTPS, commit pinning, and hashes protect transport integrity; the manifest is not
independently signed. GitHub account and publishing-secret access determine who
can publish a trusted patch. Managed files and metadata reject links and junctions.
Network and installation work runs in a worker thread, with UI updates passed
through a queue to Tkinter's main thread.

## Repository layout

- `src/dxvk_updater/`: interface, protocol, downloads, settings, and recovery.
- `scripts/`: patch publishing, Windows packaging, and application releases.
- `tests/`: installation, failure recovery, validation, UI state, and publisher tests.
- `.github/workflows/`: test/build and scheduled patch publication.

See [publishing and maintenance](docs/MAINTENANCE.md) for credential setup,
releasing a new updater, handling workflow failures, and reverting a bad patch.
