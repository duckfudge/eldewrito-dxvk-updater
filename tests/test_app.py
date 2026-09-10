"""Exercise queued UI outcomes without opening a window or making network calls."""
import queue
from types import SimpleNamespace

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
