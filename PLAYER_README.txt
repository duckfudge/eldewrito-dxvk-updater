ELDEWRITO DXVK UPDATER 1.1.1

1. Extract the whole ZIP. Keep _dxvk_updater beside ElDewritoDXVKUpdater.exe.
2. Run the executable. Python is bundled.
3. Choose the game folder containing eldorado.exe; a remembered folder is preferred.
4. Close ElDewrito before installing, repairing, or restoring the patch.
5. Review the patch name, notes, and changed files before choosing Update now.

Version 1.0.x users must install this ZIP manually once, preferably in a fresh
folder. Future program updates ask permission, replace only updater-owned files,
and restart. Game/unrelated files are preserved even in mixed folders. Windows
administrator approval is requested only if the program folder requires it;
preparation and replacement may request approval separately.
The interface restarts normally. Program and patch updates ask separately.
Later skips the program update for this opening when it is still compatible.
This package is unsigned; Windows may display its standard publisher warning.

The app checks when opened and has no tray/background service or telemetry.
Later postpones patch installation until the next opening.
Unknown/development game builds warn and require approval; stable 0.7 is supported.

Managed patch files: d3d9.dll, dxvk.conf, eldorado.dxvk-cache.
Changed files are backed up. Local cache growth/config edits do not repeatedly
trigger updates. Repair replaces all three files and retains a separate backup;
its location appears in Details. Restore previous update returns to the previous
patch revision, which repairs preserve. Undo restore reverses restoration.
A restored-away patch stays optional until different contents are published;
Install anyway remains available. Restoration and undo also work offline.

Keep .dxvk-updater in the game folder intact. It contains installed records,
restore/repair backups, and any recovery journal. An interrupted patch update
recovers when reopened with the game closed, even without internet access.
An upgrade cannot reconstruct older backups already discarded by version 1.0.x.

Keep DXVKUpdater-update.json and matching program-update caches intact during
program recovery. Caches are under %LOCALAPPDATA%\ElDewritoDXVKUpdater\program-updates
or, for administrator-assisted updates, %PROGRAMDATA%\ElDewritoDXVKUpdater\program-updates.
Reopen the updater to resume interrupted replacement. Failed startup restores
the previous program. Close other updater windows if files are locked. The app
never kills the game or silently changes permissions. Logs remain in LocalAppData.

If the installed app cannot start, open the saved 1.1.1+ recovery helper:
%LOCALAPPDATA%\ElDewritoDXVKUpdater\program-updates\<job-id>\runner\ElDewritoDXVKUpdater.exe
The exact path is recorded in Details and updater.log before replacement.
Open this helper with no arguments; it recovers the pending installation offline.
Keep the matching cache until recovery completes. Closing during preparation
cancels before replacement starts. Finished/abandoned inactive caches are cleaned
before the next program update. Older 1.1.0 copies without records are preserved.

The first update from 1.1.0 uses that version's older replacement helper. If it
fails, extract the latest ZIP manually to a fresh folder. Its helper cannot gain
1.1.1 recovery fixes until this first update completes.

Downloads/source links: https://github.com/duckfudge/eldewrito-dxvk
Supports Windows 10/11 x64. Clean VM/actual Windows DPI tests remain deferred.
Third-party license texts are included in DXVKUpdater-LICENSES.
