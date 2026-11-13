# ElDewrito DXVK Updater

A portable Python updater for the curated [ElDewrito DXVK patch](https://github.com/duckfudge/eldewrito-dxvk).
Checks when opened, asks before installation, and keeps recoverable backups.
Targets stable ElDewrito 0.7 on Windows 10/11 x64; the game uses an **x86 DLL**.

## Players

Download the Windows x64 ZIP from [GitHub Releases](https://github.com/duckfudge/eldewrito-dxvk/releases).
Extract the entire folder and run `ElDewritoDXVKUpdater.exe`; Python is bundled.
**1.0.x users must install 1.1.0 manually once**, preferably in a fresh folder.
Keep `_dxvk_updater` and the prefixed player documents beside the executable.
A valid remembered game folder takes priority over detection beside the updater.
Unknown/development game builds display a warning and require approval.

- **Update now** installs changed upstream files and repairs missing files or a damaged DLL.
- **Later** postpones the patch until the next opening. **Check again** refreshes it.
- **Repair patch** replaces all three managed files and retains a separate pre-repair backup.
- **Restore previous update** returns to the previous patch revision. Repairs preserve that restore point.
- **Undo restore** reverses restoration. Both actions also work offline.
- A restored-away revision stays optional until different contents are offered. **Install anyway** remains available.
- Program updates are offered first, with separate approval, verified replacement, and restart.
  **Later** continues with compatible patches using the installed program.

Only `d3d9.dll`, `dxvk.conf`, and `eldorado.dxvk-cache` are managed as patch files.
Local config edits and cache growth do not repeatedly trigger updates. Repair or
an upstream change replaces affected files after backup. Details shows the latest
pre-repair backup location. Close ElDewrito before patch installation or restoration.

Program replacement supports mixed game/updater folders and preserves unrelated
files. It requests administrator approval only when the program folder needs it;
the patch installer uses normal permissions and the interface restarts unelevated.
The package is unsigned. There is no tray service, telemetry, unattended installation,
or application publishing credential.

## Development

Use Python 3.13 x64 on Windows:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe launcher.py
.\.venv\Scripts\python.exe scripts/build_windows.py
```

ZIP and checksum appear in `dist/`. All dependencies are pinned. No new runtime
dependency is needed for self-updating. `--game-dir "C:\Games\Halo Online"`
selects a folder explicitly. Source is [0BSD](LICENSE); dependencies retain their
licenses. Tests use synthetic files and never execute a game or contact GitHub.
CI tests on Linux/Windows and builds the Windows package. Clean Windows 10/11
without Python, actual DPI settings, and movement between monitors remain deferred
release checks for 1.1.0 at the maintainer's request.

## Publishing

Stage all three patch files and `patch-notes.json` on a branch, then merge to
patch `main`. Notes contain exactly `title` and `summary`. Push, scheduled
30-minute, and manual workflows validate the entire patch before publishing.
Documentation/notes-only commits do not create patch revisions. The one-time
schema upgrade preserves an unchanged patch's content ID and publication date.
GitHub may delay schedules or disable them after 60 days of repository inactivity;
the patch repository also has an independent push-only publisher.

Schema-2 `updates/manifest.json` records a human title, notes, publication date,
source commit, minimum updater version, content-derived patch ID, and each file's
size/SHA-256. Downloads use that exact commit in a fixed repository. Old 1.0.x
clients reject schema 2 and direct players to Releases; no legacy feed is kept.
The publisher waits for the required application release before changing the feed.
Validation checks bounded x86 PE structure, UTF-8 config, and cache record integrity
(formats 8–15 and 17–18). It does not prove binary safety or GPU compatibility.

Program releases have a separate `updates/updater.json`, fixed release-asset URLs,
SHA-256 checks, bounded ZIP extraction, and `DXVKUpdater-files.json` ownership.
Only the executable, prefixed documents/licenses, and `_dxvk_updater` runtime
files are replaced. Unlisted files are preserved and collisions abort replacement.
Both publishers preserve other feed files and retry compare-and-swap conflicts.
Program publication never downgrades the latest offered application version.

## Recovery

Each game has `.dxvk-updater` state, backups, a per-game lock, and a journal.
Downloads are verified before replacement. Saving installed state commits a change;
interrupted operations roll back before network access. Previous-revision and
pre-repair backups are retained separately. A damaged backup stops restoration
before writes. Local caches can exceed upstream download limits.
Legacy state is recovered before migration to schema 2; the original record is
kept as `installed-1.0-backup.json`. Do not run older updaters against migrated
folders or restore that metadata file alone. Deleted backups cannot be recreated.

Program replacement runs from a cloned helper outside the installation directory.
An unelevated broker restarts the interface; failed startup restores the old program.
UAC preparation independently downloads into restricted ProgramData staging and
never elevates a user-writable cached helper. Keep `DXVKUpdater-update.json` and
its matching update cache intact during recovery. Reopening resumes recovery.
Completed caches for that installation are collected before its next program update.

HTTPS and hashes protect transport integrity; feeds are not independently signed.
GitHub and publishing-secret access determine who can publish trusted updates.
Network/install work uses worker threads and queued UI messages. Logs remain local.

See [publishing and maintenance](docs/MAINTENANCE.md) for credentials, releases,
failed workflows, recovery, and reverting a patch.
