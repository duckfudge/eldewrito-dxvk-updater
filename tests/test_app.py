"""Exercise queued UI outcomes without opening a window or making network calls."""
import queue
from types import SimpleNamespace

import pytest

from dxvk_updater.app import App


class Widget:
    def __init__(self):
        self.options = {}

    def configure(self, **kwargs):
        self.options.update(kwargs)

    def set(self, value):
        self.value = value


def ui(manifest):
    app = SimpleNamespace(events=queue.Queue(), busy=True,
                          game="game", manifest=manifest, status=None, operation="check")
    for name in ("check_button", "folder_button", "update_button", "repair_button",
                 "restore_button", "later_button", "available_value", "available_date",
                 "installed_value", "installed_date", "badge", "headline", "summary",
                 "file_list", "progress", "progress_text"):
        setattr(app, name, Widget())
    app._buttons = lambda: App._buttons(app)
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
    assert app.restore_button.options["state"] == "normal"


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
                          ("headline", "summary", "progress_text", "folder_label")})
    App._wrap_text(app, 580)
    assert app.headline.options["wraplength"] == 540
    assert app.summary.options["wraplength"] == 540
    assert app.folder_label.options["wraplength"] == 360
