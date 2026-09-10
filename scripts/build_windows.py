"""Build and audit the distributable on Windows; no source ZIP is published."""
from __future__ import annotations

import hashlib
import importlib.metadata
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dxvk_updater import __version__


def build_environment():
    env = dict(os.environ)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONUSERBASE"] = str(ROOT / "build" / "python-user-base")
    env["PYINSTALLER_CONFIG_DIR"] = str(ROOT / "build" / "pyinstaller-cache")
    # Some all-users Python installs don't expose Tcl through the launcher's registry lookup.
    tcl = Path(sys.base_prefix) / "tcl"
    if (tcl / "tcl8.6" / "init.tcl").is_file():
        env["TCL_LIBRARY"] = str(tcl / "tcl8.6")
        env["TK_LIBRARY"] = str(tcl / "tk8.6")
    return env


def audit_archive(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if not any(name.endswith("/ElDewritoDXVKUpdater.exe") for name in names):
            raise ValueError("The executable is missing from the distribution")
        for name in names:
            parts = Path(name).parts
            if Path(name).suffix in {".py", ".pyw", ".pdb"} or any(part in {".git", ".github", "src", "tests", ".venv"} for part in parts):
                raise ValueError(f"Source or build files must not be distributed: {name}")
            if name.endswith((".env", "settings.json", "installed.json", "updater.log")):
                raise ValueError(f"Local settings must not be distributed: {name}")
        if archive.testzip():
            raise ValueError("The distribution ZIP is corrupt")


def add_licenses(bundle):
    destination = bundle / "THIRD_PARTY_LICENSES"
    destination.mkdir(exist_ok=True)
    for package in ("customtkinter", "darkdetect", "packaging", "requests", "certifi", "charset-normalizer", "idna", "urllib3", "psutil", "pyinstaller"):
        distribution = importlib.metadata.distribution(package)
        for item in distribution.files or []:
            if any(part.lower().startswith(("license", "copying", "notice")) for part in item.parts):
                source = Path(distribution.locate_file(item))
                if source.is_file() and source.suffix not in {".py", ".pyc"}:
                    folder = destination / package
                    folder.mkdir(exist_ok=True)
                    shutil.copyfile(source, folder / source.name)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.exists():
        shutil.copyfile(python_license, destination / "Python-LICENSE.txt")


def main():
    if os.name != "nt":
        raise SystemExit("Run this build on Windows with Python 3.13.")
    if sys.version_info[:2] != (3, 13):
        raise SystemExit("Use Python 3.13 for reproducible Windows builds.")
    env = build_environment()
    subprocess.run([sys.executable, "-c", "import tkinter; print('Tcl:', tkinter.Tcl().eval('info patchlevel'))"], env=env, check=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(ROOT / "updater.spec")], cwd=ROOT, env=env, check=True)
    bundle = ROOT / "dist" / "ElDewritoDXVKUpdater"
    add_licenses(bundle)
    shutil.copyfile(ROOT / "PLAYER_README.txt", bundle / "README.txt")
    archive = ROOT / "dist" / f"ElDewritoDXVKUpdater-{__version__}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                output.write(path, str(path.relative_to(bundle.parent)))
    audit_archive(archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(f"Built and audited {archive.name}")


if __name__ == "__main__":
    main()
