# Validation record

This record distinguishes completed checks from remaining platform validation.

- Python 3.13.0 x64 on Windows 11 build 26100: automated suite initially passed
  74 tests; one actual-symlink test skipped because the host disallows creating
  symlinks without Developer Mode/elevation. Additional checks are recorded below.
- Real `0525` release ZIP inspected: exactly the three documented patch files.
  Its DLL passes x86 PE DLL validation. Current public `main` config and cache pass
  format validation; config and cache are taken from main, not the historical ZIP.
- Windows 10 without Python: not yet tested on a separate Windows 10 machine.
- Packaged executable launched successfully on Windows 11 with the bundled
  Python 3.13 runtime and Tcl/Tk. Default window layout was visually inspected;
  folder selection, cards, update/repair/restore controls, details and footer fit.
  Dependency consistency passes `pip check`. Distribution ZIP passes its source
  exclusion and checksum audit.
- Public feed, binary download, scheduled publication: pending deployment verification.

The test suite uses synthetic files in temporary directories and never starts or
modifies the user's actual ElDewrito installation.
