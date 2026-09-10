ELDEWRITO DXVK UPDATER

1. Extract the entire ZIP. Keep the _internal folder beside the executable.
2. Run ElDewritoDXVKUpdater.exe. Python is included.
3. Select the game folder containing eldorado.exe.
4. Close ElDewrito before installing, repairing, or restoring the patch.
5. Review the update and choose Install patch or Update now.

The updater checks for updates when opened. It does not run in the background.
Later postpones the update without changing patch files.

Managed files: d3d9.dll, dxvk.conf, eldorado.dxvk-cache.
Changed files are backed up before replacement. Repair replaces all three files.
Restore previous update returns affected files to their previous contents.
Keep the .dxvk-updater directory in your game folder: it contains your restore
point, installed patch records, and any recovery journal. Do not delete it during
an update or recovery. Logs are saved under %LOCALAPPDATA%\ElDewritoDXVKUpdater.

If an update is interrupted, close the game and open the updater again. Recovery
does not need internet access. If a file is locked, close the app holding it and
try again. The updater never closes the game or changes permissions for you.

Patch files and downloads: https://github.com/duckfudge/eldewrito-dxvk
Supports Windows x64 and the curated ElDewrito 0.7 DXVK patch.
Third-party license texts are included in THIRD_PARTY_LICENSES.
