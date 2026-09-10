# Validation record

Validated September 10, 2026 UTC. This record distinguishes completed checks from
remaining platform validation.

The source repository was subsequently made **public** at the owner's request.
The private-access checks below describe the initial deployment. The current
release audit expects public source visibility, and workflows use a canonical
repository check so publishing continues with public source. Credentials remain
in Actions secrets and are excluded from pull-request builds and application ZIPs.
After the visibility change, the public release audit passed, including anonymous
source repository access and the unchanged application ZIP checksum. Local tests
passed 74 checks with the same Windows symlink-privilege skip noted below.

- Python 3.13.0 x64 on Windows 11 build 26100: automated suite initially passed
  74 tests; one actual-symlink test skipped because the host disallows creating
  symlinks without Developer Mode/elevation. Additional checks are recorded below.
- Real `0525` release ZIP inspected: exactly the three documented patch files.
  Its DLL passes x86 PE DLL validation. Current public `main` config and cache pass
  format validation; config and cache are taken from main, not the historical ZIP.
- Release CI: **75 tests passed** on GitHub's Windows runner with Python 3.13.15,
  including the symlink test skipped on the local host. The complete test, build,
  artifact audit, and public release workflow succeeded:
  https://github.com/duckfudge/eldewrito-dxvk-updater/actions/runs/34476527580
- Packaged executable launched successfully on Windows 11 with the bundled
  Python 3.13 runtime and Tcl/Tk. Default window layout was visually inspected;
  folder selection, cards, update/repair/restore controls, details and footer fit.
  Dependency consistency passes `pip check`. Distribution ZIP passes its source
  exclusion and checksum audit.
- The downloaded public executable also launched on Windows 11, fetched the real
  feed, and displayed the available patch and folder-selection/install prompt.
  The executable loads its bundled runtime; Python was still installed on this
  host, so this is not a clean-machine compatibility claim.
- Private publisher dry run, live publication, and repeated no-change run passed:
  https://github.com/duckfudge/eldewrito-dxvk-updater/actions/runs/34476175164
  https://github.com/duckfudge/eldewrito-dxvk-updater/actions/runs/34476487096
  https://github.com/duckfudge/eldewrito-dxvk-updater/actions/runs/34476667942
  The no-change run preserved feed commit
  `5e45cb0f9eea09c7f649e70cac3ca5ad4d6a4ad7`.
- A subsequent public README-only commit also produced no patch revision:
  https://github.com/duckfudge/eldewrito-dxvk-updater/actions/runs/34477402102
- `python scripts/smoke_live.py` passed against the actual public feed in a newly
  created synthetic game folder with spaces and non-ASCII characters. It checked
  downloads, all hashes, installation, locally grown cache, repair, restoration,
  missing files, originally absent files, and preservation of unrelated files.
- Initial live patch ID:
  `e6dd32eb028138e9aa73e9eef944005cc1598546a22e03299477b87d708fa3c3`.
  Source commit: `7facd7c50cf8eb2ab9dd72dfa0387d04943db969`.
- Public release `updater-v1.0.0` was built from private source commit
  `38c92797ea42751cd2f204a5865e6f0ba13cd88d`. ZIP: 16,107,740 bytes;
  SHA-256: `e088aabcb9a13282426b5c2ff7050c1d4a0d8310ee182baa329c731a5057d90f`.
- `python scripts/verify_public_release.py` downloaded the public ZIP and verified
  its checksum, expected assets, source exclusion, and absence of recognizable
  GitHub token patterns. Automatic public source archives contain only the public
  patch files and README. The private repository is marked Private in GitHub and
  its unauthenticated API lookup returns 404. Latest patch release remains `0525`.
- Schedule is enabled for minutes 17 and 47 each hour. A scheduled trigger has not
  yet been observed; manual runs exercise the same publisher. Concurrent-update
  conflict handling and source changes during a run are covered by unit tests.
- Remaining external checks: Windows 10 and Windows 11 machines without Python
  installed, and visual/interaction checks at 150% and 200% Windows display scaling.
  No claim is made that these separate machine/display configurations were tested.

The test suite uses synthetic files in temporary directories and never starts or
modifies the user's actual ElDewrito installation.
