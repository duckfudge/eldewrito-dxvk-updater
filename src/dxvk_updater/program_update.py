"""Consent-based portable program updates; no game files belong to this transaction."""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import __version__
from .bundle import EXE, INVENTORY, MAX_ZIP, MAX_MEMBERS, destination, extract_bundle, read_inventory, parse_inventory, owned_path
from .model import HEX40, HEX64, PROGRAM_URL, REPOSITORY, UpdaterError, read_json, sha256_file, version_tuple
from .network import FeedClient
from .settings import settings_directory
from .storage import ID_PATTERN, atomic_copy, atomic_json, safe_regular, file_lock

MARKER = "DXVKUpdater-update.json"
PHASES = {"preparing", "prepared", "applying", "awaiting_startup", "committed", "rolled_back", "recovery_required", "failed"}


@dataclass(frozen=True)
class ProgramRelease:
    version: str
    source_commit: str
    size: int
    sha256: str
    published_at: str
    summary: str

    @classmethod
    def parse(cls, data):
        obj = read_json(data) if not isinstance(data, dict) else data
        if set(obj) != {"schema_version", "version", "source_commit", "size", "sha256", "published_at", "summary"} or type(obj["schema_version"]) is not int or obj["schema_version"] != 1:
            raise UpdaterError("Invalid program update metadata.")
        version_tuple(obj["version"])
        if not isinstance(obj["source_commit"], str) or not HEX40.fullmatch(obj["source_commit"]) or not isinstance(obj["sha256"], str) or not HEX64.fullmatch(obj["sha256"]):
            raise UpdaterError("Invalid program update hashes.")
        if type(obj["size"]) is not int or not 0 < obj["size"] <= MAX_ZIP:
            raise UpdaterError("Invalid program update size.")
        try:
            if not isinstance(obj["published_at"], str) or len(obj["published_at"]) > 40 or datetime.fromisoformat(obj["published_at"]).tzinfo is None:
                raise ValueError()
        except ValueError:
            raise UpdaterError("Invalid program publication date.") from None
        if not isinstance(obj["summary"], str) or len(obj["summary"]) > 2000 or any(ord(c) < 32 and c not in "\n\t" for c in obj["summary"]):
            raise UpdaterError("Invalid program release notes.")
        return cls(*(obj[name] for name in ("version", "source_commit", "size", "sha256", "published_at", "summary")))

    def to_dict(self):
        return {"schema_version": 1, **self.__dict__}

    def download_url(self):
        return f"https://github.com/{REPOSITORY}/releases/download/updater-v{self.version}/ElDewritoDXVKUpdater-{self.version}-windows-x64.zip"

    def newer(self):
        return version_tuple(self.version) > version_tuple(__version__)


def fetch_program(client):
    return ProgramRelease.parse(client.metadata(PROGRAM_URL))


def program_client():
    import requests
    session = requests.Session()
    session.trust_env = False  # Elevated preparation must not accept an environment-supplied CA or proxy.
    return FeedClient(session)


def user_cache():
    root = settings_directory() / "program-updates"
    root.mkdir(parents=True, exist_ok=True)
    safe_regular(root, directory=True, missing=False)
    return root.resolve()


def load_job(job):
    job = Path(job)
    if not ID_PATTERN.fullmatch(job.name):
        raise UpdaterError("Invalid program update identifier.")
    safe_regular(job, directory=True, missing=False)
    safe_regular(job / "job.json", missing=False)
    obj = read_json((job / "job.json").read_bytes())
    fields = {"id", "target", "parent_pid", "parent_created", "release", "phase", "worker_pid", "worker_created", "error"}
    if set(obj) != fields or obj["id"] != job.name or obj["phase"] not in PHASES:
        raise UpdaterError("The program recovery record is damaged.")
    ProgramRelease.parse(obj["release"])
    if not isinstance(obj["target"], str) or not Path(obj["target"]).is_absolute() or Path(obj["target"]).resolve() != Path(obj["target"]):
        raise UpdaterError("Invalid program destination.")
    for prefix in ("parent", "worker"):
        pid, created = obj[prefix + "_pid"], obj[prefix + "_created"]
        if type(pid) is not int or pid < 0 or type(created) not in (int, float) or not math.isfinite(created) or created < 0:
            raise UpdaterError("Invalid program process record.")
    if not isinstance(obj["error"], str) or len(obj["error"]) > 2000:
        raise UpdaterError("Invalid program recovery message.")
    return obj


def save_job(job, data, phase, error=""):
    data.update(phase=phase, error=error[:2000])
    atomic_json(job / "job.json", data)


def clone_runner(target, job):
    inventory = read_inventory(target, verify=True)
    runner = job / "runner"
    runner.mkdir()
    for name in [row["path"] for row in inventory["files"]] + [INVENTORY]:
        atomic_copy(destination(target, name), destination(runner, name, create=True))
    return runner / EXE


def read_broker(copy):
    safe_regular(copy, directory=True, missing=False)
    safe_regular(copy / "broker.json", missing=False)
    obj = read_json((copy / "broker.json").read_bytes())
    if set(obj) != {"job_id", "target", "parent_pid", "parent_created"} or not isinstance(obj["job_id"], str) or not ID_PATTERN.fullmatch(obj["job_id"]):
        raise UpdaterError("Invalid program restart record.")
    target = obj["target"]
    if not isinstance(target, str) or not Path(target).is_absolute() or str(Path(target).resolve()) != target:
        raise UpdaterError("Invalid program restart destination.")
    if type(obj["parent_pid"]) is not int or obj["parent_pid"] < 0 or type(obj["parent_created"]) not in (int, float) or not math.isfinite(obj["parent_created"]) or obj["parent_created"] < 0:
        raise UpdaterError("Invalid program restart process.")
    return obj


def create_broker(copy, target, job_id, parent_pid, parent_created, source):
    atomic_json(copy / "broker.json", {"job_id": job_id, "target": str(target),
                                      "parent_pid": parent_pid, "parent_created": parent_created})
    return clone_runner(source, copy)


def stage_job(job, target, release, parent_pid, parent_created, client, emit, cancelled):
    data = {"id": job.name, "target": str(target), "parent_pid": parent_pid, "parent_created": parent_created,
            "release": release.to_dict(), "phase": "preparing", "worker_pid": 0, "worker_created": 0, "error": ""}
    atomic_json(job / "job.json", data)
    try:
        clone_runner(target, job)
        archive = job / "package.zip"
        emit("Downloading the updater program…", 0)
        downloaded = 0
        def progress(count):
            nonlocal downloaded
            downloaded += count
            emit("Downloading the updater program…", min(0.7, downloaded / release.size * 0.7))
        client.download_program(release, archive, progress, cancelled)
        if cancelled():
            raise UpdaterError("Program update cancelled. Nothing was replaced.")
        extract_bundle(archive, job / "payload", release.version)
        if cancelled():
            raise UpdaterError("Program update cancelled. Nothing was replaced.")
        emit("Program verified; preparing safe replacement and restart…", 0.8)
    except Exception as exc:
        save_job(job, data, "failed", str(exc))
        raise
    save_job(job, data, "prepared")
    return job


def prune_cache(cache, target):
    """Collect completed jobs for this app; never collect pending recovery or a running helper."""
    import psutil
    for job in cache.iterdir():
        if not ID_PATTERN.fullmatch(job.name):
            continue
        try:
            if (job / "broker.json").exists():
                from .windows import admin_cache
                data = read_broker(job)
                if data["target"] != str(target) or process_alive(data["parent_pid"], data["parent_created"]):
                    continue
                secured = admin_cache() / data["job_id"]
                data = load_job(secured) if secured.exists() else None
                owner = secured
            else:
                data = load_job(job)
                owner = job
            if (target / MARKER).exists():
                continue
            if data and (data["target"] != str(target) or worker_alive(owner, data)):
                continue
            if data and data["phase"] not in {"committed", "rolled_back", "failed"} and not (
                data["phase"] in {"preparing", "prepared"} and not process_alive(data["parent_pid"], data["parent_created"])
            ):
                continue
            runner = os.path.normcase(str(job / "runner" / EXE))
            active = False
            for process in psutil.process_iter(["name", "exe"]):
                if (process.info["name"] or "").casefold() == EXE.casefold():
                    actual = process.info["exe"]
                    if actual is None or os.path.normcase(actual) == runner:
                        active = True
                        break
            if active:
                continue
            paths, directories = [], []
            for parent, children, names in os.walk(job, followlinks=False):
                parent = Path(parent)
                safe_regular(parent, directory=True, missing=False)
                directories.append(parent)
                for name in children:
                    safe_regular(parent / name, directory=True, missing=False)
                for name in names:
                    path = parent / name
                    safe_regular(path, missing=False)
                    relative = path.relative_to(job).parts
                    if len(relative) == 1:
                        if name not in {"job.json", "broker.json", "worker.json", "lock", "package.zip", "error.txt"} and not name.startswith(".writing-"):
                            raise UpdaterError("Unexpected cache file; preserving the job.")
                    elif relative[0] not in {"runner", "payload", "backup"}:
                        raise UpdaterError("Unexpected cache directory; preserving the job.")
                    else:
                        owned_path("/".join(relative[1:]))
                    paths.append(path)
                    if len(paths) > MAX_MEMBERS * 3 + 16:
                        raise UpdaterError("Unexpected cache size; preserving the job.")
            for path in paths:
                path.unlink()
            for path in sorted(directories, key=lambda p: len(p.parts), reverse=True):
                path.rmdir()
        except (OSError, UpdaterError, psutil.Error):
            continue


def writable(target):
    inventory = read_inventory(target)
    directories = {target} | {destination(target, row["path"]).parent for row in inventory["files"]}
    for directory in directories:
        try:
            probe = directory / ("DXVKUpdater-write-test-" + uuid.uuid4().hex)
            # tempfile retries ACL denials on Windows as if they were name collisions.
            with probe.open("xb"):
                pass
            probe.unlink()
        except PermissionError:
            return False
    return True


def prepare_update(release, emit=lambda text, fraction=None: None, cancelled=lambda: False):
    if not release.newer():
        raise UpdaterError("The installed program is already current. Check again for a newer release.")
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise UpdaterError("Program self-updating is available in the packaged Windows app. Extract the latest ZIP to update this copy.")
    from . import windows
    import psutil
    if windows.elevated():
        raise UpdaterError("Reopen the updater normally before updating the program. Administrator approval is requested only for replacement when needed.")
    target, job_id = Path(sys.executable).parent.resolve(), uuid.uuid4().hex
    if (target / MARKER).exists():
        raise UpdaterError("Reopen the updater to finish the pending program recovery first.")
    cache = user_cache()
    prune_cache(cache, target)
    job = cache / job_id
    job.mkdir()
    parent, created = os.getpid(), psutil.Process().create_time()
    if not writable(target):
        create_broker(job, target, job_id, parent, created, target)
        emit("Waiting for administrator approval and verified program preparation…")
        windows.run_elevated(target / EXE, ["--program-prepare-admin", job_id, release.version, str(parent), str(created)])
        secured = windows.admin_cache() / job_id
        data = load_job(secured)
        if data["release"] != release.to_dict() or data["target"] != str(target):
            raise UpdaterError("The published program changed during preparation. Check again before updating.")
        if cancelled():
            cancel_prepared((job, secured))
            raise UpdaterError("Program update cancelled. Nothing was replaced.")
        emit(f"If normal startup fails, open this saved recovery helper: {job / 'runner' / EXE}")
        return job, secured
    client = program_client()
    try:
        stage_job(job, target, release, parent, created, client, emit, cancelled)
    finally:
        client.close()
    emit(f"If normal startup fails, open this saved recovery helper: {job / 'runner' / EXE}")
    return job, job


def launch(executable, arguments):
    environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    try:
        return subprocess.Popen([str(executable), *map(str, arguments)], executable=str(executable), cwd=executable.parent,
                                env=environment, creationflags=0x08000000 if os.name == "nt" else 0)
    except OSError as exc:
        raise UpdaterError("Could not start the updater process. Close other updater windows and check access to the extracted program. "
                           "If Windows reports a long path, use a shorter folder or extract the latest ZIP manually. " + str(exc)) from exc


def handoff(prepared):
    broker, job = prepared
    try:
        launch(broker / "runner" / EXE, ["--program-broker", job])
    except Exception as exc:
        if broker == job:
            data = load_job(job)
            save_job(job, data, "failed", "The restart broker could not start. " + str(exc))
        raise


def cancel_prepared(prepared):
    copy, job = prepared
    with file_lock(copy / "lock", "Program preparation is still in use."):
        data = load_job(job)
        if (Path(data["target"]) / MARKER).exists() or worker_alive(job, data) or data["phase"] != "prepared":
            raise UpdaterError("Program replacement has already started. Reopen the updater to finish recovery.")
        if copy == job:
            save_job(job, data, "failed", "Program update cancelled. Nothing was replaced.")
        else:
            reference = read_broker(copy)
            if reference["job_id"] != job.name or reference["target"] != data["target"]:
                raise UpdaterError("The restart record belongs to another program update.")


def spawn_helper(job):
    import psutil
    process = launch(job / "runner" / EXE, ["--program-apply", job])
    try:
        atomic_json(job / "worker.json", {"pid": process.pid, "created": psutil.Process(process.pid).create_time()})
    except psutil.NoSuchProcess:
        pass


def worker_alive(job, data):
    if process_alive(data["worker_pid"], data["worker_created"]):
        return True
    path = job / "worker.json"
    safe_regular(path)
    if path.exists():
        worker = read_json(path.read_bytes())
        if set(worker) != {"pid", "created"} or type(worker["pid"]) is not int or type(worker["created"]) not in (int, float):
            raise UpdaterError("Invalid program worker record.")
        return process_alive(worker["pid"], worker["created"])
    return False


def process_alive(pid, created):
    import psutil
    if not pid:
        return False
    try:
        return abs(psutil.Process(pid).create_time() - created) < 0.01
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True


def wait_parent(data):
    deadline = time.monotonic() + 45
    while process_alive(data["parent_pid"], data["parent_created"]):
        if time.monotonic() >= deadline:
            raise UpdaterError("Close all updater windows before replacing the program. No files were replaced.")
        time.sleep(0.1)


def marker_matches(target, job):
    marker = target / MARKER
    safe_regular(marker, missing=False)
    obj = read_json(marker.read_bytes())
    if obj != {"job_id": job.name, "admin": job.parent != user_cache()}:
        raise UpdaterError("Another program update owns this folder's recovery record.")


def staged_inventory(job, data):
    release = ProgramRelease.parse(data["release"])
    archive = job / "package.zip"
    safe_regular(archive, missing=False)
    if archive.stat().st_size != release.size or sha256_file(archive) != release.sha256:
        raise UpdaterError("The staged program ZIP is damaged. Keep the backup intact.")
    with zipfile.ZipFile(archive) as source:
        info = source.getinfo("ElDewritoDXVKUpdater/" + INVENTORY)
        if info.file_size > 1024 * 1024:
            raise UpdaterError("Invalid staged program inventory.")
        return parse_inventory(source.read(info))


def publish_program_marker(target, job):
    marker = target / MARKER
    pointer = {"job_id": job.name, "admin": job.parent != user_cache()}
    fd, name = tempfile.mkstemp(prefix="DXVKUpdater-pointer-", dir=target)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(pointer, output)
            output.flush()
            os.fsync(output.fileno())
        # Windows rename and POSIX hard-link creation both refuse an existing owner.
        if os.name == "nt":
            os.rename(temporary, marker)
        else:
            os.link(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)


def finish_program_marker(job, data):
    target = Path(data["target"])
    marker = target / MARKER
    safe_regular(marker)
    if marker.exists():
        marker_matches(target, job)
        read_inventory(target, verify=True)
        try:
            marker.unlink()
        except OSError as exc:
            message = "Program replacement finished, but recovery-marker cleanup is pending. Close other updater windows and reopen. " + str(exc)
            save_job(job, data, data["phase"], message)
            raise UpdaterError(message) from exc


def recover_prepared_marker(job, data, old):
    target = Path(data["target"])
    marker = target / MARKER
    if read_inventory(job / "backup", verify=True) != old:
        raise UpdaterError("The prepared program backup does not match the unchanged installation.")
    raw = marker.read_bytes()
    try:
        marker_matches(target, job)
    except UpdaterError:
        expected = json.dumps({"job_id": job.name, "admin": job.parent != user_cache()}).encode("utf-8")
        owner = expected.partition(b', "admin"')[0]
        # A legacy partial pointer is recoverable only after its complete owner was written.
        if not raw.startswith(owner) or not expected.startswith(raw):
            raise UpdaterError("The recovery marker does not identify this prepared job. Keep the backup intact and use manual recovery.") from None
        marker.unlink()
        publish_program_marker(target, job)
    save_job(job, data, "rolled_back")
    finish_program_marker(job, data)


def rollback_program(job, data):
    target = Path(data["target"])
    marker_matches(target, job)
    old = read_inventory(job / "backup", verify=True)
    new = staged_inventory(job, data)
    old_names = {row["path"] for row in old["files"]} | {INVENTORY}
    new_names = {row["path"] for row in new["files"]} | {INVENTORY}
    # Verify the complete backup before restoring any file.
    for name in sorted(old_names):
        source = destination(job / "backup", name)
        target_file = destination(target, name, create=True)
        if not target_file.exists() or sha256_file(target_file) != sha256_file(source):
            atomic_copy(source, target_file)
    for name in sorted(new_names - old_names):
        destination(target, name).unlink(missing_ok=True)
    save_job(job, data, "rolled_back", data["error"])


def apply_update(job, wait_for_startup, *, wait_for_parent=wait_parent):
    """Runs only from the cloned runner, after the original UI exits."""
    with file_lock(job / "lock", "Another program replacement helper is already running."):
        _apply_update(job, wait_for_startup, wait_for_parent)


def _apply_update(job, wait_for_startup, wait_for_parent):
    import psutil
    data = load_job(job)
    target = Path(data["target"])
    safe_regular(target, directory=True, missing=False)
    data.update(worker_pid=os.getpid(), worker_created=psutil.Process().create_time())
    atomic_json(job / "job.json", data)
    wait_for_parent(data)
    data = load_job(job)
    if data["phase"] in {"committed", "rolled_back"}:
        finish_program_marker(job, data)
        return
    if data["phase"] in {"applying", "awaiting_startup", "recovery_required"}:
        rollback_program(job, data)
        finish_program_marker(job, data)
        return
    if data["phase"] != "prepared":
        return
    marker = target / MARKER
    safe_regular(marker)
    old = read_inventory(target, verify=True)
    if old != read_inventory(job / "runner", verify=True):
        raise UpdaterError("The installed program changed after preparation. Check again before updating.")
    if marker.exists():
        recover_prepared_marker(job, data, old)
        return
    new = read_inventory(job / "payload", verify=True)
    if new != staged_inventory(job, data) or new["version"] != data["release"]["version"]:
        raise UpdaterError("The staged program version changed.")
    if version_tuple(new["version"]) < version_tuple(old["version"]):
        raise UpdaterError("Program updates cannot downgrade the installed version.")
    old_names = {row["path"] for row in old["files"]} | {INVENTORY}
    new_names = {row["path"] for row in new["files"]} | {INVENTORY}
    for name in new_names - old_names:
        if destination(target, name).exists():
            raise UpdaterError(f"An unrelated file occupies {name}. Move it before updating the program.")
    backup = job / "backup"
    safe_regular(backup, directory=True)
    backup.mkdir(exist_ok=True)
    for name in sorted(old_names):
        atomic_copy(destination(target, name), destination(backup, name, create=True))
    read_inventory(backup, verify=True)
    read_inventory(target, verify=True)
    publish_program_marker(target, job)
    try:
        save_job(job, data, "applying")
        for name in sorted(new_names):
            atomic_copy(destination(job / "payload", name), destination(target, name, create=True))
        for name in sorted(old_names - new_names):
            destination(target, name).unlink()
        read_inventory(target, verify=True)
        save_job(job, data, "awaiting_startup")
        if not wait_for_startup():
            raise UpdaterError("The new program did not confirm successful startup. Restoring the previous version.")
        save_job(job, data, "committed")
    except Exception as exc:
        data = load_job(job)
        if data["phase"] == "committed":
            finish_program_marker(job, data)
            return
        data["error"] = str(exc)[:2000]
        try:
            rollback_program(job, data)
        except Exception as recovery:
            data = load_job(job)
            if data["phase"] != "rolled_back":
                save_job(job, data, "recovery_required", str(exc) + " Close all updater windows and reopen to recover. " + str(recovery))
                raise UpdaterError("Program recovery is pending. Keep the program-update cache intact and close other updater windows.") from recovery
        finish_program_marker(job, data)
        raise UpdaterError("The program update failed; the previous version was restored. " + str(exc)) from exc
    finish_program_marker(job, data)


def trusted_job(path, *, applying=False):
    job = Path(path)
    if not job.is_absolute() or job.resolve() != job or not ID_PATTERN.fullmatch(job.name):
        raise UpdaterError("Unexpected program update location.")
    from . import windows
    is_admin = windows.elevated()
    if applying and is_admin:
        if job.parent != windows.admin_cache():
            raise UpdaterError("Administrator replacement requires protected staging.")
    elif job.parent != user_cache() and job.parent != windows.admin_cache():
        raise UpdaterError("Unexpected program recovery location.")
    safe_regular(job, directory=True, missing=False)
    load_job(job)
    if applying and Path(sys.executable).resolve() != job / "runner" / EXE:
        raise UpdaterError("Program replacement must run outside the installation folder.")
    return job


def broker(job):
    from . import windows
    job = trusted_job(job)
    if windows.elevated():
        raise UpdaterError("The restart broker must run without administrator privileges.")
    started, retried, child, startup_deadline = False, False, None, None
    grace = time.monotonic() + 3
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        data = load_job(job)
        target = Path(data["target"])
        phase = data["phase"]
        if phase == "committed":
            if not (target / MARKER).exists():
                if not started:
                    launch(target / EXE, [])
                return
        if phase == "rolled_back":
            if not (target / MARKER).exists():
                launch(target / EXE, [])
                if data["error"]:
                    raise UpdaterError("The previous program was restored. " + data["error"])
                return
        if phase == "failed":
            raise UpdaterError(data["error"] or "Program preparation failed. No program files were replaced.")
        if phase == "awaiting_startup" and not started:
            child = launch(target / EXE, ["--program-health", job.name])
            started = True
            startup_deadline = time.monotonic() + 50
        if phase == "awaiting_startup" and child and startup_deadline is not None and time.monotonic() >= startup_deadline:
            # Only terminate the unacknowledged child we created; never identify a victim by PID or name.
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            child, startup_deadline = None, None
        if time.monotonic() >= grace and not worker_alive(job, data):
            if retried:
                raise UpdaterError(data["error"] or "Program replacement stopped. Reopen the updater to recover.")
            retried = True
            if job.parent == user_cache():
                spawn_helper(job)
            else:
                windows.run_elevated(job / "runner" / EXE, ["--program-start-admin", job])
            grace = time.monotonic() + 3
        time.sleep(0.2)
    raise UpdaterError("Program replacement has not finished. Keep the update cache intact and close other updater windows.")


def saved_runner_job(executable):
    copy = executable.parent.parent
    if executable.parent.name != "runner" or copy.parent != user_cache() or not ID_PATTERN.fullmatch(copy.name):
        return None
    if (copy / "broker.json").exists():
        from .windows import admin_cache
        reference = read_broker(copy)
        job = trusted_job(admin_cache() / reference["job_id"])
        if load_job(job)["target"] != reference["target"]:
            raise UpdaterError("The saved recovery helper belongs to another installation.")
    else:
        job = trusted_job(copy)
    if not (Path(load_job(job)["target"]) / MARKER).exists():
        raise UpdaterError("No interrupted program replacement needs recovery. Open the updater in its installation folder.")
    return job


def dispatch(arguments=None):
    """Handle helper modes before importing Tk or opening files in the target runtime."""
    args = list(sys.argv[1:] if arguments is None else arguments)
    if not getattr(sys, "frozen", False) or os.name != "nt":
        return None
    from . import windows
    if args and args[0] == "--program-health":
        return None
    mode = args[0] if args else ""
    if not mode.startswith("--program-"):
        saved = saved_runner_job(Path(sys.executable).resolve())
        if saved:
            broker(saved)
            return 0
        target = Path(sys.executable).parent
        marker = target / MARKER
        if not marker.exists():
            return None
        safe_regular(marker, missing=False)
        pointer = read_json(marker.read_bytes())
        if set(pointer) != {"job_id", "admin"} or not isinstance(pointer["job_id"], str) or not ID_PATTERN.fullmatch(pointer["job_id"]) or type(pointer["admin"]) is not bool:
            raise UpdaterError("Invalid program recovery pointer. Extract a fresh updater ZIP and retain the update cache.")
        job = (windows.admin_cache() if pointer["admin"] else user_cache()) / pointer["job_id"]
        data = load_job(trusted_job(job))
        if data["target"] != str(target.resolve()):
            raise UpdaterError("The program recovery pointer belongs to another installation.")
        runner = job / "runner" / EXE
        if pointer["admin"]:
            # An unelevated broker is required even when the replacement needs UAC.
            copy = user_cache() / uuid.uuid4().hex
            copy.mkdir()
            import psutil
            create_broker(copy, Path(data["target"]), job.name, os.getpid(), psutil.Process().create_time(), job / "runner")
            runner = copy / "runner" / EXE
        launch(runner, ["--program-broker", job])
        return 0
    try:
        if mode == "--program-prepare-admin" and len(args) == 5:
            job_id, version, pid, created = args[1:]
            if not windows.elevated() or not ID_PATTERN.fullmatch(job_id):
                raise UpdaterError("Administrator preparation was not approved.")
            version_tuple(version)
            target = Path(sys.executable).parent.resolve()
            cache = windows.admin_cache(create=True)
            prune_cache(cache, target)
            job = cache / job_id
            job.mkdir()
            client = program_client()
            try:
                release = fetch_program(client)
                if release.version != version or not release.newer():
                    raise UpdaterError("The program feed changed. Check again before updating.")
                stage_job(job, target, release, int(pid), float(created), client, lambda *a: None, lambda: False)
            finally:
                client.close()
        elif mode == "--program-start-admin" and len(args) == 2:
            if not windows.elevated():
                raise UpdaterError("Administrator recovery was not approved.")
            spawn_helper(trusted_job(args[1], applying=True))
        elif mode == "--program-apply" and len(args) == 2:
            job = trusted_job(args[1], applying=True)
            handle = windows.health_event(job.name)
            try:
                apply_update(job, lambda: windows.wait_health(handle))
            finally:
                windows.close_handle(handle)
        elif mode == "--program-broker" and len(args) == 2:
            broker(Path(args[1]))
        else:
            raise UpdaterError("Invalid program helper arguments.")
        return 0
    except Exception as exc:
        if mode == "--program-prepare-admin" and "job" in locals():
            try:
                (job / "error.txt").write_text(str(exc)[:2000], encoding="utf-8")
            except OSError:
                pass
        if mode != "--program-apply":
            ctypes_message(str(exc))
        elif len(args) == 2:
            try:
                job = trusted_job(args[1], applying=True)
                data = load_job(job)
                if data["phase"] == "prepared" and data["worker_pid"] == os.getpid():
                    save_job(job, data, "failed", str(exc))
            except Exception:
                pass
        return 1


def ctypes_message(message):
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, message[:2000], "ElDewrito DXVK Updater", 0x10)
