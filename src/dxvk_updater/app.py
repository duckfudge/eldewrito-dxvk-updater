"""All Tk calls live on the UI thread; workers only put messages into a queue."""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from . import __version__
from .engine import Installer, Status
from .model import FILES, RELEASES_URL, Manifest, UpdaterError
from .network import FeedClient
from .settings import remember_game, settings_directory, suggested_game

BG = "#0B1018"
PANEL = "#111B28"
CARD = "#162232"
EDGE = "#23374A"
CYAN = "#5DE2E7"
TEXT = "#ECF3F9"
MUTED = "#91A6B9"
GREEN = "#8EE0B2"
AMBER = "#F3CA86"
LOG = logging.getLogger("dxvk_updater")


def human_size(size):
    return f"{size / 1024**2:.2f} MB" if size >= 1024**2 else f"{size / 1024:.1f} KB"


class App(ctk.CTk):
    def __init__(self, *, game=None, preview=False):
        super().__init__()
        self.title(f"ElDewrito DXVK Updater · {__version__}")
        self.geometry("1060x760")
        self.minsize(900, 700)
        self.configure(fg_color=BG)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.manifest = None
        self.status = None
        self.game = game or suggested_game()
        self.preview = preview
        self.details_visible = False
        self.operation = None
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(60, self._poll)
        if preview:
            self._preview()
        else:
            self.after(250, self.check)

    def label(self, parent, text, size=14, color=TEXT, weight="normal", **kwargs):
        return ctk.CTkLabel(parent, text=text, text_color=color,
                            font=ctk.CTkFont(family="Segoe UI", size=size, weight=weight), **kwargs)

    def button(self, parent, text, command, primary=False, **kwargs):
        return ctk.CTkButton(parent, text=text, command=command, height=42,
                             corner_radius=8, font=ctk.CTkFont("Segoe UI", 13, "bold"),
                             fg_color=CYAN if primary else CARD,
                             hover_color="#8BEFF0" if primary else "#20374B",
                             text_color=BG if primary else TEXT,
                             border_width=0 if primary else 1, border_color=EDGE, **kwargs)

    def _build(self):
        side = ctk.CTkFrame(self, width=210, corner_radius=0, fg_color=PANEL)
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(5, weight=1)
        emblem = tk.Canvas(side, width=130, height=108, bg=PANEL, highlightthickness=0)
        emblem.grid(row=0, column=0, pady=(36, 8))
        emblem.create_polygon(65, 8, 108, 32, 108, 79, 65, 103, 22, 79, 22, 32, outline=EDGE, width=2, fill=PANEL)
        emblem.create_polygon(42, 31, 88, 31, 65, 79, outline=CYAN, width=3, fill=PANEL)
        emblem.create_line(50, 47, 80, 47, fill=CYAN, width=2)
        self.label(side, "ELDEWRITO", 20, weight="bold").grid(row=1, column=0)
        self.label(side, "DXVK UPDATER", 11, CYAN, "bold").grid(row=2, column=0, pady=(4, 36))
        self.label(side, "PATCH MANAGER", 10, MUTED, "bold").grid(row=3, column=0, sticky="w", padx=24)
        ctk.CTkFrame(side, height=1, fg_color=EDGE).grid(row=4, column=0, sticky="ew", padx=24, pady=16)
        self.check_button = self.button(side, "Check again", self.check)
        self.check_button.grid(row=6, column=0, padx=20, pady=6, sticky="ew")
        self.repair_button = self.button(side, "Repair patch", self.repair)
        self.repair_button.grid(row=7, column=0, padx=20, pady=6, sticky="ew")
        self.restore_button = self.button(side, "Restore previous update", self.restore)
        self.restore_button.grid(row=8, column=0, padx=20, pady=6, sticky="ew")
        self.label(side, f"VERSION {__version__}\nWindows · ElDewrito 0.7", 11, MUTED, justify="left").grid(row=9, column=0, sticky="w", padx=24, pady=24)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=0, column=1, sticky="nsew", padx=32, pady=28)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(4, weight=1)
        header = ctk.CTkFrame(body, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 22))
        self.label(header, "A smoother way to play.", 29, weight="bold").pack(anchor="w")
        self.label(header, "Your ElDewrito DXVK patch, kept up to date.", 14, MUTED).pack(anchor="w", pady=(6, 0))

        location = ctk.CTkFrame(body, fg_color=PANEL, corner_radius=10, border_width=1, border_color=EDGE)
        location.grid(row=1, column=0, sticky="ew", pady=(0, 18))
        location.grid_columnconfigure(0, weight=1)
        self.label(location, "GAME FOLDER", 10, MUTED, "bold").grid(row=0, column=0, sticky="w", padx=18, pady=(12, 0))
        self.folder_label = self.label(location, self._folder_text(), 12, anchor="w", wraplength=480)
        self.folder_label.grid(row=1, column=0, sticky="ew", padx=18, pady=(2, 12))
        self.folder_button = self.button(location, "Choose folder", self.choose_folder, width=130)
        self.folder_button.grid(row=0, column=1, rowspan=2, padx=14, pady=14)

        versions = ctk.CTkFrame(body, fg_color="transparent")
        versions.grid(row=2, column=0, sticky="ew", pady=(0, 18))
        versions.grid_columnconfigure((0, 1), weight=1, uniform="versions")
        self.installed_value, self.installed_date = self._version_card(versions, 0, "INSTALLED PATCH", "Not checked", "Select your game folder")
        self.available_value, self.available_date = self._version_card(versions, 1, "AVAILABLE PATCH", "Checking…", "From duckfudge / eldewrito-dxvk")

        self.notice = ctk.CTkFrame(body, fg_color=PANEL, corner_radius=12, border_width=1, border_color=EDGE)
        self.notice.grid(row=3, column=0, sticky="ew", pady=(0, 16))
        self.notice.grid_columnconfigure(0, weight=1)
        self.badge = self.label(self.notice, "CONNECTING TO GITHUB", 10, CYAN, "bold")
        self.badge.grid(row=0, column=0, sticky="w", padx=20, pady=(16, 4))
        self.headline = self.label(self.notice, "Checking for patch updates", 20, weight="bold", anchor="w", wraplength=600)
        self.headline.grid(row=1, column=0, sticky="ew", padx=20)
        self.summary = self.label(self.notice, "This only takes a moment.", 13, MUTED, anchor="w", justify="left", wraplength=600)
        self.summary.grid(row=2, column=0, sticky="ew", padx=20, pady=(6, 8))
        self.file_list = self.label(self.notice, "", 12, MUTED, anchor="w", justify="left")
        self.file_list.grid(row=3, column=0, sticky="w", padx=20)
        actions = ctk.CTkFrame(self.notice, fg_color="transparent")
        actions.grid(row=4, column=0, sticky="w", padx=20, pady=(12, 18))
        self.update_button = self.button(actions, "Update now", self.update, primary=True, width=160)
        self.update_button.pack(side="left")
        self.later_button = self.button(actions, "Later", self.later, width=92)
        self.later_button.pack(side="left", padx=(10, 0))

        progress_area = ctk.CTkFrame(body, fg_color="transparent")
        progress_area.grid(row=4, column=0, sticky="nsew")
        progress_area.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(progress_area, height=5, progress_color=CYAN, fg_color=EDGE)
        self.progress.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.progress.set(0)
        self.progress_text = self.label(progress_area, "Ready", 12, MUTED, anchor="w", wraplength=650)
        self.progress_text.grid(row=1, column=0, sticky="ew")
        self.details_button = ctk.CTkButton(progress_area, text="Show details", command=self.toggle_details,
                                           fg_color="transparent", hover_color=PANEL, text_color=MUTED, width=96, anchor="w")
        self.details_button.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.logbox = ctk.CTkTextbox(progress_area, height=115, fg_color=PANEL, text_color=MUTED,
                                    font=ctk.CTkFont("Consolas", 11), corner_radius=8)
        self.logbox.configure(state="disabled")
        footer = ctk.CTkFrame(body, fg_color="transparent")
        footer.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        self.label(footer, "Changes are applied only when you choose to update.", 11, MUTED).pack(side="left")
        ctk.CTkButton(footer, text="GitHub releases ↗", width=115, fg_color="transparent", hover_color=PANEL,
                      text_color=CYAN, command=lambda: webbrowser.open(RELEASES_URL)).pack(side="right")
        self._buttons()

    def _version_card(self, parent, col, title, value, detail):
        frame = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=10)
        frame.grid(row=0, column=col, sticky="ew", padx=(0, 8) if col == 0 else (8, 0))
        self.label(frame, title, 10, MUTED, "bold").pack(anchor="w", padx=18, pady=(14, 4))
        label = self.label(frame, value, 22, TEXT if col == 0 else CYAN, "bold")
        label.pack(anchor="w", padx=18)
        sublabel = self.label(frame, detail, 11, MUTED)
        sublabel.pack(anchor="w", padx=18, pady=(4, 14))
        return label, sublabel

    def _folder_text(self):
        return str(self.game) if self.game else "Choose the folder containing eldorado.exe"

    def _buttons(self):
        available = not self.busy and not self.preview
        for button in (self.check_button, self.folder_button):
            button.configure(state="normal" if available else "disabled")
        ready = available and self.game is not None and self.manifest is not None
        self.update_button.configure(state="normal" if ready and self.status and self.status.kind != "current" else "disabled")
        self.repair_button.configure(state="normal" if ready else "disabled")
        # Restoration is available offline whenever a game is selected; the engine validates it.
        self.restore_button.configure(state="normal" if available and self.game else "disabled")
        self.later_button.configure(state="normal" if available else "disabled")

    def emit(self, text, fraction=None):
        self.events.put(("progress", (text, fraction)))

    def _run(self, operation, function):
        if self.busy or self.preview:
            return
        self.busy = True
        self.operation = operation
        self.cancel.clear()
        self._buttons()
        def work():
            try:
                result = function()
                self.events.put(("done", (operation, result)))
            except Exception as exc:
                LOG.exception("Operation failed: %s", operation)
                self.events.put(("error", str(exc)))
        threading.Thread(target=work, name="dxvk-worker", daemon=False).start()

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    message, fraction = value
                    self.progress_text.configure(text=message)
                    if fraction is not None:
                        self.progress.set(fraction)
                    self._log(message)
                elif kind == "done":
                    self.busy = False
                    operation, result = value
                    if operation == "check":
                        self.manifest, self.status = result
                        self._render()
                    else:
                        self.status = result
                        if self.status:
                            self._render()
                        if operation == "restore":
                            self.badge.configure(text="RESTORE COMPLETE", text_color=GREEN)
                            self.headline.configure(text="Your previous files are back")
                            self.summary.configure(text="You can keep playing with the restored patch. Updates remain optional.")
                        self.progress.set(1)
                    self._buttons()
                elif kind == "error":
                    self.busy = False
                    if self.operation == "check":
                        self.available_value.configure(text="Unavailable")
                        self.available_date.configure(text="Try checking again")
                    # Clear stale update choices after an error; restoration remains available.
                    self.status = None
                    self.badge.configure(text="ACTION NEEDED", text_color=AMBER)
                    self.headline.configure(text="We couldn’t finish that step")
                    self.summary.configure(text=value[:600])
                    self.file_list.configure(text="")
                    self.progress_text.configure(text="Check the message above, then try again.")
                    self._log(value)
                    self._buttons()
        except queue.Empty:
            pass
        self.after(60, self._poll)

    def _log(self, message):
        LOG.info(message)
        self.logbox.configure(state="normal")
        self.logbox.insert("end", f"{datetime.now():%H:%M:%S}  {message}\n")
        if int(self.logbox.index("end-1c").split(".")[0]) > 200:
            self.logbox.delete("1.0", "50.0")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _render(self):
        manifest, status = self.manifest, self.status
        self.available_value.configure(text=manifest.patch_id[:10])
        self.available_date.configure(text=f"Published {manifest.published_at[:10]}")
        if status and status.installed:
            self.installed_value.configure(text=status.installed.patch_id[:10])
            self.installed_date.configure(text=f"Installed release · {status.installed.published_at[:10]}")
        else:
            self.installed_value.configure(text="Not managed yet" if self.game else "No folder selected")
            self.installed_date.configure(text="Ready for first installation" if self.game else "Select your ElDewrito folder")
        if not status:
            badge, headline, summary = "READY TO CONNECT", "Choose your game folder", "Select the folder containing eldorado.exe to check your installed patch."
            names = ()
        elif status.kind == "current":
            badge, headline, summary = "UP TO DATE", "You’re ready to play", "You have the latest published patch. Your local shader cache can continue growing normally."
            names = ()
        elif status.kind == "repair":
            badge, headline, summary = "FILES MISSING", "Let’s restore your patch files", "Missing patch files can be downloaded again. Existing affected files will be backed up."
            names = status.missing
        else:
            badge = "UPDATE AVAILABLE" if status.installed else "PATCH AVAILABLE"
            headline = "A new patch is ready" if status.installed else "Bring DXVK to ElDewrito"
            summary = (manifest.summary[:280] or "A new curated DXVK patch is available.") + "\nAffected local files will be backed up and replaced."
            names = status.changed if status.installed else FILES
            names = tuple(dict.fromkeys((*names, *status.missing)))
        self.badge.configure(text=badge, text_color=GREEN if status and status.kind == "current" else CYAN)
        self.headline.configure(text=headline)
        self.summary.configure(text=summary)
        files = [f for f in manifest.files if f.name in names]
        self.file_list.configure(text="  ·  ".join(f.name for f in files) + (f"\n{human_size(sum(f.size for f in files))} download" if files else ""))
        self.update_button.configure(text="Install patch" if status and not status.installed else "Repair missing files" if status and status.kind == "repair" else "Update now")
        self.progress_text.configure(text="Checked just now · GitHub")

    def choose_folder(self):
        chosen = filedialog.askdirectory(parent=self, title="Select the folder containing eldorado.exe",
                                         initialdir=str(self.game) if self.game else None)
        if not chosen:
            return
        game = Path(chosen)
        if not (game / "eldorado.exe").is_file():
            messagebox.showerror("Not an ElDewrito folder", "Choose the folder that contains eldorado.exe.", parent=self)
            return
        self.game = game.resolve()
        try:
            remember_game(self.game)
        except OSError as exc:
            self._log(f"Could not remember folder: {exc}")
        self.folder_label.configure(text=self._folder_text())
        self.status = None
        self.check()

    def check(self):
        game = self.game
        self.progress.set(0)
        def work():
            client = FeedClient()
            try:
                engine = Installer(game, client, self.emit) if game else None
                if engine:
                    engine.recover()  # Recovery works even when GitHub is offline.
                self.emit("Checking the latest published patch…")
                manifest = client.fetch_manifest()
                return manifest, engine.check(manifest) if engine else None
            finally:
                client.close()
        self._run("check", work)

    def update(self):
        self._install(repair=False)

    def repair(self):
        if messagebox.askyesno("Repair the DXVK patch?", "Download all three published patch files again? Your existing config, cache, and DLL will be backed up and replaced.", parent=self):
            self._install(repair=True)

    def _install(self, repair):
        if not self.game or not self.manifest:
            return
        game, manifest = self.game, self.manifest
        def work():
            client = FeedClient()
            try:
                engine = Installer(game, client, self.emit)
                engine.install(manifest, repair=repair, cancelled=self.cancel.is_set)
                return engine.check(manifest)
            finally:
                client.close()
        self._run("repair" if repair else "install", work)

    def restore(self):
        if not self.game:
            return
        if not messagebox.askyesno("Restore the previous update?", "Restore the files saved before your last successful patch change? Your current affected files will become the new restore point.", parent=self):
            return
        game, manifest = self.game, self.manifest
        def work():
            engine = Installer(game, None, self.emit)
            engine.restore()
            return engine.check(manifest) if manifest else None
        self._run("restore", work)

    def later(self):
        self.badge.configure(text="UPDATE POSTPONED", text_color=MUTED)
        self.headline.configure(text="Play on your own schedule")
        self.summary.configure(text="Your patch files have not been changed. You can update here when you’re ready.")

    def toggle_details(self):
        self.details_visible = not self.details_visible
        if self.details_visible:
            self.logbox.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        else:
            self.logbox.grid_remove()
        self.details_button.configure(text="Hide details" if self.details_visible else "Show details")

    def _close(self):
        if self.busy:
            self.cancel.set()
            self.progress_text.configure(text="Waiting for the current operation to finish safely. You can close this window when it completes.")
            return
        self.destroy()

    def _preview(self):
        # Developer-only visual fixture. No downloads or game-file operations.
        from .model import make_manifest
        from .demo import demo_payloads
        previous = make_manifest("a" * 40, demo_payloads(), "Previous patch")
        latest = make_manifest("b" * 40, demo_payloads(updated=True), "Updated DXVK configuration and shader cache for ElDewrito 0.7.")
        self.manifest = latest
        self.status = Status("update", previous, latest, ("dxvk.conf", "eldorado.dxvk-cache"), (), True)
        self.folder_label.configure(text="Preview only · no game files are accessed")
        self._render()
        self.badge.configure(text="INTERFACE PREVIEW")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true", help="Show a noninteractive visual fixture")
    parser.add_argument("--game-dir", type=Path)
    args = parser.parse_args()
    directory = settings_directory()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(directory / "updater.log", maxBytes=512_000, backupCount=2, encoding="utf-8")
        logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(message)s")
    except OSError:
        logging.basicConfig(level=logging.INFO)
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    App(game=args.game_dir, preview=args.preview).mainloop()


if __name__ == "__main__":
    main()
