"""Exercise queued UI outcomes without opening a window or making network calls."""
import queue
from types import SimpleNamespace

import pytest

import dxvk_updater.app as module
from dxvk_updater.app import App
from dxvk_updater.engine import Status
from dxvk_updater.model import UpdaterError
from dxvk_updater.program_update import ProgramRelease


class Widget:
    def __init__(self):
        self.options = {}

    def configure(self, **kwargs):
        self.options.update(kwargs)

    def set(self, value):
        self.value = value


def ui(manifest):
    app = SimpleNamespace(events=queue.Queue(), busy=True,
                          game="game", manifest=manifest, status=None, local_status=None, operation="check",
                          program=None, program_deferred=False, check_error="", closing=False)
    for name in ("check_button", "folder_button", "update_button", "repair_button",
                 "restore_button", "later_button", "available_value", "available_date",
                 "installed_value", "installed_date", "badge", "headline", "summary",
                 "file_list", "progress", "progress_text"):
        setattr(app, name, Widget())
    app._buttons = lambda: App._buttons(app)
    app._render = lambda: App._render(app)
    app._program_offered = lambda: App._program_offered(app)
    app._log = lambda text: None
    app._poll = lambda: None
    app.after = lambda *args: None
    return app


def test_failed_check_disables_repair_using_stale_manifest(manifest):
    app = ui(manifest)
    app.events.put(("error", "GitHub is unavailable"))
    App._poll(app)
    assert app.manifest is None
    assert app.repair_button.options["state"] == "disabled"
    assert app.restore_button.options["state"] == "disabled"


def test_offline_restore_clears_stale_installed_details():
    app = ui(None)
    app.operation = "restore"
    app.installed_value.configure(text="old patch ID")
    app.events.put(("done", ("restore", None)))
    App._poll(app)
    assert app.installed_value.options["text"] != "old patch ID"
    assert app.file_list.options["text"] == ""


def test_damaged_dll_is_offered_as_repair(game, client, manifest):
    from dxvk_updater.engine import Installer
    engine = Installer(game, client, game_guard=lambda root: None)
    engine.install(manifest)
    (game / "d3d9.dll").write_bytes(b"")
    app = ui(manifest)
    app.game, app.busy, app.status = game, False, engine.check(manifest)
    App._render(app)
    App._buttons(app)
    assert app.update_button.options["state"] == "normal"
    assert app.update_button.options["text"] == "Repair patch files"
    assert "d3d9.dll" in app.file_list.options["text"]
    assert "dxvk.conf" not in app.file_list.options["text"]


@pytest.mark.parametrize("scale", [1, 1.5, 2])
def test_window_and_minimum_fit_screen_at_high_dpi(scale):
    app = SimpleNamespace(_get_window_scaling=lambda: scale,
                          winfo_screenwidth=lambda: 1920, winfo_screenheight=lambda: 1080)
    sizes = {}
    app.geometry = lambda value: sizes.update(geometry=value, window=tuple(map(int, value.partition("+")[0].split("x"))))
    app.minsize = lambda w, h: sizes.update(minimum=(w, h))
    App._fit_window(app)
    assert sizes["geometry"].endswith("+20+20")
    assert sizes["minimum"][0] <= sizes["window"][0] < 1920 / scale
    assert sizes["minimum"][1] <= sizes["window"][1] < 1080 / scale


def test_text_wraps_to_narrower_body():
    app = SimpleNamespace(**{name: Widget() for name in
                          ("headline", "summary", "progress_text", "folder_label", "installed_value", "available_value")})
    App._wrap_text(app, 580)
    assert app.headline.options["wraplength"] == 540
    assert app.summary.options["wraplength"] == 540
    assert app.folder_label.options["wraplength"] == 360


def test_offline_known_restore_point_remains_available(manifest):
    from dxvk_updater.engine import Status
    app = ui(None)
    app.busy = False
    app.local_status = Status("offline", manifest, (), (), True, True)
    App._buttons(app)
    assert app.restore_button.options["state"] == "normal"
    assert app.restore_button.options["text"] == "Undo restore"
    assert app.repair_button.options["state"] == "disabled"


def test_program_offer_precedes_patch_and_later_allows_compatible_patch(manifest):
    from dxvk_updater.engine import Status
    from dxvk_updater.program_update import ProgramRelease
    app = ui(manifest)
    app.busy = False
    app.status = Status("install", None, (), (), False)
    app.program = ProgramRelease("1.2.0", "a" * 40, 100, "b" * 64, "2026-11-13T00:00:00+00:00", "Program notes")
    App._render(app)
    App._buttons(app)
    assert app.update_button.options["text"] == "Update program"
    assert app.repair_button.options["state"] == "disabled"
    App.later(app)
    assert app.program_deferred
    assert app.update_button.options["text"] == "Install patch"
    assert app.repair_button.options["state"] == "normal"


def test_close_during_operation_exits_after_safe_completion():
    import threading
    app = ui(None)
    app.cancel, app.progress_text = threading.Event(), Widget()
    destroyed = []
    app.destroy = lambda: destroyed.append(True)
    App._close(app)
    assert app.cancel.is_set() and app.closing
    app.events.put(("done", ("restore", None)))
    App._poll(app)
    assert destroyed == [True]


@pytest.mark.parametrize("closing", [False, True])
def test_prepared_program_handoff_respects_close(monkeypatch, closing):
    app = ui(None)
    app.operation, app.closing = "program", closing
    job, actions = object(), []
    monkeypatch.setattr(module, "handoff", lambda result: actions.append(("handoff", result)))
    monkeypatch.setattr(module, "cancel_prepared", lambda result: actions.append(("cancel", result)))
    app.destroy = lambda: actions.append(("destroy", None))
    app.events.put(("done", ("program", job)))
    App._poll(app)
    assert actions == [("cancel" if closing else "handoff", job), ("destroy", None)]


def run_check(app):
    app.busy = False
    app.emit = lambda text: app.events.put(("progress", (text, None)))
    app._run = lambda operation, work: app.events.put(("done", (operation, work())))
    App.check(app)
    App._poll(app)


@pytest.mark.parametrize("failure", ["construct", "recover", "after_feed"])
def test_game_errors_preserve_program_offer_and_clear_patch_actions(monkeypatch, game, manifest, failure):
    app = ui(manifest)
    app.game = game
    stale = Status("update", manifest, (), (), True)
    app.status = app.local_status = stale
    program = ProgramRelease("1.2.0", "a" * 40, 100, "b" * 64,
                             "2026-11-13T00:00:00+00:00", "Program notes")
    calls = []

    class Engine:
        def __init__(self, *args):
            calls.append("construct")
            if failure == "construct":
                raise PermissionError("Game folder is read-only")

        def check(self, available=None):
            calls.append("recover" if available is None else "after_feed")
            if failure == ("recover" if available is None else "after_feed"):
                raise UpdaterError("Recovery journal is damaged")
            return Status("offline", manifest, (), (), True)

    def fetch_patch():
        calls.append("patch")
        return manifest

    def fetch_program(client):
        calls.append("program")
        return program

    client = SimpleNamespace(fetch_manifest=fetch_patch, close=lambda: calls.append("close"))
    monkeypatch.setattr(module, "Installer", Engine)
    monkeypatch.setattr(module, "FeedClient", lambda: client)
    monkeypatch.setattr(module, "fetch_program", fetch_program)
    monkeypatch.setattr(module, "game_support", lambda game: ("supported", True))
    run_check(app)
    assert calls == ({
        "construct": ["construct", "program", "close"],
        "recover": ["construct", "recover", "program", "close"],
        "after_feed": ["construct", "recover", "program", "patch", "after_feed", "close"],
    }[failure])
    assert app.program == program
    assert app.manifest is app.status is app.local_status is None
    assert app.check_error
    assert app.installed_value.options["text"] == "Not checked"
    assert app.update_button.options["state"] == "normal"
    assert app.update_button.options["text"] == "Update program"
    assert app.repair_button.options["state"] == app.restore_button.options["state"] == "disabled"
    App.later(app)
    assert app.update_button.options["state"] == "disabled"
    assert app.summary.options["text"] == app.check_error


@pytest.mark.parametrize("failed_feed", ["program", "patch"])
def test_feed_errors_keep_independent_results(monkeypatch, game, manifest, failed_feed):
    app = ui(manifest)
    app.game = game
    local = Status("offline", manifest, (), (), True)
    updated = Status("update", manifest, ("dxvk.conf",), (), True)
    program = ProgramRelease("1.2.0", "a" * 40, 100, "b" * 64,
                             "2026-11-13T00:00:00+00:00", "Program notes")
    calls = []

    def check(available=None):
        calls.append("local" if available is None else "compare")
        return local if available is None else updated

    def fetch_patch():
        calls.append("patch")
        if failed_feed == "patch":
            raise UpdaterError("Patch feed unavailable")
        return manifest

    def fetch_program(client):
        calls.append("program")
        if failed_feed == "program":
            raise UpdaterError("Program feed unavailable")
        return program

    client = SimpleNamespace(fetch_manifest=fetch_patch, close=lambda: calls.append("close"))
    monkeypatch.setattr(module, "Installer", lambda *args: SimpleNamespace(check=check))
    monkeypatch.setattr(module, "FeedClient", lambda: client)
    monkeypatch.setattr(module, "fetch_program", fetch_program)
    monkeypatch.setattr(module, "game_support", lambda game: ("supported", True))
    run_check(app)
    assert calls == ["local", "program", "patch", *(["compare"] if failed_feed == "program" else []), "close"]
    if failed_feed == "program":
        assert app.program is None
        assert app.manifest == manifest and app.status == updated
        assert app.repair_button.options["state"] == "normal"
    else:
        assert app.program == program
        assert app.manifest is None and app.status == local
        App.later(app)
        assert app.repair_button.options["state"] == "disabled"
    assert app.restore_button.options["state"] == "normal"
